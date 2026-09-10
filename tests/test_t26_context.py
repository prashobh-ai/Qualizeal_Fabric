"""T26 — two-turn context window + coreference resolution.

A follow-up rarely repeats its subject ("what about its pricing", "and for
testers?", "the second one"). ``answer.context.resolve`` rewrites such a
question from the last one or two turns before retrieval, or asks back with
chips when the reference is genuinely ambiguous. It is deterministic and
model-free, and ``AnswerService.ask(..., context=...)`` runs it before
answering and stamps ``understood_as`` when a rewrite happened.

The browser mirror (``scripts/showcase/engine.js`` → ``resolveCtx``) resolves
the same ladder so the static showcase behaves like the server; here we pin the
Python ladder and the service wiring.
"""

from __future__ import annotations

import unittest

from knowledge_fabric.answer import context as cx
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.tenants import demo
from tests.util import seeded

# Two camelCase products, so the distinctive-subject vocabulary is non-empty.
_QMENTIS = (
    "# QMentisAI\n\n"
    "QMentisAI is an AI test-intelligence platform. QMentisAI pricing is "
    "usage-based, billed per test run. QMentisAI serves QA engineers and "
    "testers who want failure triage without writing scripts.\n"
)
_VALIDAITE = (
    "# ValidAIte\n\n"
    "ValidAIte is a validation harness for AI systems. ValidAIte runs "
    "evaluation suites and grades model outputs against a rubric.\n"
)

_SUBJECTS = {"qmentisai": "QMentisAI", "validaite": "ValidAIte", "nexaai": "NexaAI"}
_BANK = list(_SUBJECTS.values())


def _turn(q, **k):
    return cx.Turn(
        question=q,
        subject=k.get("subject", ""),
        answer_docs=k.get("answer_docs", []),
        kind=k.get("kind", "answer"),
        options=k.get("options", []),
    )


class TestSubjectIn(unittest.TestCase):
    def test_whole_token_not_substring(self):
        # "testers" must not match a "test" subject — whole tokens only.
        self.assertEqual(cx.subject_in("who are the testers", {"test": "Test"}), "")
        self.assertEqual(cx.subject_in("run the test now", {"test": "Test"}), "test")

    def test_latest_mention_wins(self):
        got = cx.subject_in("QMentisAI vs ValidAIte", _SUBJECTS)
        self.assertEqual(got, "validaite")


class TestResolveLadder(unittest.TestCase):
    def _resolve(self, q, turns):
        return cx.resolve(q, turns[-2:], _SUBJECTS, _BANK)

    def test_self_contained_passes_through(self):
        r = self._resolve("What is QMentisAI?", [])
        self.assertEqual(r.question, "What is QMentisAI?")
        self.assertIsNone(r.understood_as)
        self.assertIsNone(r.clarify)

    def test_pronoun_substitutes_sticky_subject(self):
        r = self._resolve("what about its pricing", [_turn("What is QMentisAI?")])
        self.assertEqual(r.understood_as, "what about QMentisAI pricing")

    def test_ellipsis_reaches_two_turns_back(self):
        r = self._resolve(
            "and for testers?",
            [_turn("What is QMentisAI?"), _turn("what about QMentisAI pricing")],
        )
        self.assertEqual(r.understood_as, "QMentisAI for testers")

    def test_comparative_swaps_new_entity(self):
        r = self._resolve("what about NexaAI", [_turn("what does QMentisAI do")])
        self.assertEqual(r.understood_as, "what does NexaAI do")

    def test_bare_pronoun_empty_session_asks_back(self):
        r = self._resolve("when was it made", [])
        self.assertIsNotNone(r.clarify)
        self.assertTrue(set(r.clarify["chips"]) <= set(_BANK))
        self.assertTrue(r.clarify["chips"])

    def test_ordinal_resolves_to_last_answer_doc(self):
        r = self._resolve(
            "the second one",
            [_turn("compare tools", answer_docs=["QMentisAI", "ValidAIte", "NexaAI"])],
        )
        self.assertEqual(r.understood_as, "the second one (ValidAIte)")

    def test_clarify_option_click_reasks_original(self):
        # The previous turn asked back; clicking a chip answers the original.
        r = self._resolve(
            "QMentisAI",
            [_turn("when was it made", kind="clarify", options=["QMentisAI", "ValidAIte"])],
        )
        self.assertEqual(r.understood_as, "when was QMentisAI made")


class TestServiceContext(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["cf"])
        intake, worker = Intake(self.p), IngestWorker(self.p, None)
        worker.intake = intake
        for uri, title, body in [
            ("internal://q/qmentis.md", "QMentisAI", _QMENTIS),
            ("internal://q/validaite.md", "ValidAIte", _VALIDAITE),
        ]:
            intake.submit(
                intake.canonical(
                    "cf",
                    "internal",
                    uri,
                    title,
                    body.encode(),
                    mime="text/markdown",
                    acl=["public"],
                )
            )
        worker.drain()
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, "cf", "asker.public")

    def test_pronoun_followup_stamps_understood_as(self):
        ctx = {"turns": [{"question": "What is QMentisAI?"}]}
        a = self.svc.ask(self.asker, "what about its pricing", context=ctx)
        # the follow-up was rewritten to name the sticky subject before retrieval
        self.assertTrue(a.understood_as)
        self.assertIn("QMentisAI", a.understood_as)
        self.assertNotEqual(a.kind, AnswerKind.CLARIFY)

    def test_self_contained_is_not_stamped(self):
        a = self.svc.ask(self.asker, "What is QMentisAI?", context={"turns": []})
        self.assertIsNone(a.understood_as)

    def test_bare_pronoun_empty_session_clarifies_with_chips(self):
        a = self.svc.ask(self.asker, "when was it made", context={"turns": []})
        self.assertEqual(a.kind, AnswerKind.CLARIFY)
        self.assertTrue(a.suggestions)
        d = a.to_dict()
        self.assertEqual(d["suggestions"], a.suggestions)

    def test_no_context_is_backward_compatible(self):
        # A caller that passes no context still answers (no crash, no clarify).
        a = self.svc.ask(self.asker, "What is QMentisAI?")
        self.assertEqual(a.kind, AnswerKind.ANSWER)


if __name__ == "__main__":
    unittest.main()
