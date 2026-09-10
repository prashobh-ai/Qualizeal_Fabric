"""T27 — role-conditioned answers (by organisational designation).

A person's role here is their org *designation* — developer, tester, delivery
head, CTO — captured by the admin at access-grant time and carried on
``principal.designation``. It conditions the answer without ever touching what is
retrievable (that is ``scopes``/ACL, enforced before ranking):

- **emphasis** — which grounded evidence leads (a developer's answer leads with
  the implementation, a tester's with the verifying test);
- **depth** — how much of the grounded answer is surfaced (an executive gets the
  headline, a delivery lead a brief, a builder the full detail);
- **lens** — the frame the surfaces render.

``personas.persona_for`` maps the many titles onto a small set of personas; the
browser engine mirrors the same map + ``_persona_view`` so the static showcase
frames a baked answer identically per signed-in designation.
"""

from __future__ import annotations

import unittest

from knowledge_fabric.answer import personas
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.tenants import demo
from tests.util import T, seeded

# A function and the test that verifies it — same identifier, different files —
# so persona emphasis has an implementation and a test to choose between.
_IMPL = '''"""Retry helpers."""


def retry_call(fn, attempts=3):
    """Retry a flaky call with backoff until it succeeds."""
    for _ in range(attempts):
        try:
            return fn()
        except Exception:
            continue
'''
_TEST = '''"""Tests for retry_call."""


def test_retry_call_succeeds():
    """Verify retry_call retries a flaky call and eventually succeeds."""
    assert retry_call(lambda: 1) == 1
'''

# A single-document camelCase subject → one citation (curator gap hint) and a
# distinctive token (a bare pronoun can clarify against it).
_ZEPHYR = "# ZephyrAI\n\nZephyrAI is a standalone widget used only in this fixture.\n"


class TestPersonaMap(unittest.TestCase):
    def test_designation_to_persona(self):
        cases = {
            "Developer": "developer",
            "Senior Software Engineer": "developer",
            "Solution Architect": "developer",
            "DevOps Engineer": "developer",
            "Tester": "quality",
            "QA Engineer": "quality",
            "QE Lead": "quality",
            "SDET": "quality",
            "Automation Engineer": "quality",
            "Delivery Head": "delivery",
            "Engineering Manager": "delivery",
            "Scrum Master": "delivery",
            "Project Lead": "delivery",
            "Director": "executive",
            "VP Engineering": "executive",
            "CEO": "executive",
            "CTO": "executive",
            "Knowledge Curator": "curation",
            "Platform Admin": "operations",
            "": "general",
            "Intern": "general",
        }
        for designation, persona in cases.items():
            self.assertEqual(personas.persona_for(designation), persona, designation)

    def test_depth_cap(self):
        self.assertEqual(personas.depth_cap("CTO"), 1)  # headline
        self.assertEqual(personas.depth_cap("Delivery Head"), 2)  # brief
        self.assertEqual(personas.depth_cap("Developer"), 3)  # full
        self.assertEqual(personas.depth_cap(""), 3)


class TestPersonaConditioning(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T])
        intake, worker = Intake(self.p), IngestWorker(self.p, None)
        worker.intake = intake
        for uri, title, body, mime in [
            ("github://acme/app/retry.py", "retry.py", _IMPL, "text/x-python;code"),
            ("github://acme/app/test_retry.py", "test_retry.py", _TEST, "text/x-python;code"),
            ("internal://q/zephyr.md", "ZephyrAI", _ZEPHYR, "text/markdown"),
        ]:
            intake.submit(
                intake.canonical(T, "github", uri, title, body.encode(), mime=mime, acl=["public"])
            )
        worker.drain()
        self.svc = AnswerService(self.p)

    def _ask(self, subject, q):
        return self.svc.ask(demo.principal_for(self.p, T, subject), q)

    def test_emphasis_leads_with_persona_relevant_evidence(self):
        # Same grounded question; a developer leads with the implementation, a
        # tester with the verifying test.
        dev = self._ask("developer", "how does retry_call work")
        qa = self._ask("tester", "how does retry_call work")
        self.assertEqual(dev.kind, AnswerKind.ANSWER)
        self.assertEqual(qa.kind, AnswerKind.ANSWER)
        self.assertEqual(dev.citations[0].document_title, "retry.py")
        self.assertEqual(qa.citations[0].document_title, "test_retry.py")
        self.assertEqual(dev.role_view["emphasis"], "code")
        self.assertEqual(qa.role_view["emphasis"], "test")

    def test_depth_shapes_length(self):
        q = "what is the test strategy"
        cxo = self._ask("cto", q)  # headline → 1 sentence
        lead = self._ask("delivery", q)  # brief → up to 2
        dev = self._ask("developer", q)  # full → up to 3
        self.assertEqual(cxo.answer_text.count("["), 1)
        self.assertLessEqual(lead.answer_text.count("["), 2)
        self.assertLessEqual(dev.answer_text.count("["), 3)
        self.assertGreaterEqual(dev.answer_text.count("["), lead.answer_text.count("["))

    def test_lens_per_persona(self):
        aq = "what is the test strategy"
        self.assertEqual(self._ask("developer", aq).role_view["lens"], "builder")
        self.assertEqual(self._ask("tester", aq).role_view["lens"], "quality")
        self.assertEqual(self._ask("delivery", aq).role_view["lens"], "delivery")
        self.assertEqual(self._ask("cto", aq).role_view["lens"], "executive")
        self.assertEqual(self._ask("curator", aq).role_view["lens"], "curation")
        self.assertEqual(self._ask("admin", aq).role_view["lens"], "operations")
        # a reader with no designation → the general persona, whose lens is
        # "answer" (the UI renders no strip for it).
        gen = self._ask("asker.public", aq).role_view
        self.assertEqual(gen["lens"], "answer")
        self.assertEqual(gen["persona"], "general")

    def test_facts_stay_grounded_and_cited_for_every_persona(self):
        for sub in ["developer", "tester", "delivery", "cto", "curator", "admin"]:
            a = self._ask(sub, "what is the test strategy")
            self.assertEqual(a.kind, AnswerKind.ANSWER)
            self.assertTrue(a.citations, sub)
            self.assertTrue(a.answer_text.strip(), sub)

    def test_curator_gap_hint_on_single_source(self):
        a = self._ask("curator", "what is zephyrai")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertEqual(a.role_view["sources"], 1)
        self.assertIn("single source", (a.role_view["gap_hint"] or "").lower())

    def test_admin_lens_carries_operations(self):
        rv = self._ask("admin", "what is the test strategy").role_view
        for key in ("level", "model", "cost", "cache_hit", "tokens_in", "tokens_out"):
            self.assertIn(key, rv)

    def test_role_view_serialises(self):
        a = self._ask("developer", "what is the test strategy")
        self.assertEqual(a.to_dict()["role_view"], a.role_view)

    def test_persona_cache_is_isolated(self):
        # Different personas asking the same question must not serve each other's
        # framing from the shared answer cache.
        cxo = self._ask("cto", "what is the test strategy")
        dev = self._ask("developer", "what is the test strategy")
        self.assertLess(cxo.answer_text.count("["), dev.answer_text.count("["))

    def test_designation_never_widens_access(self):
        # A developer with only public scope still cannot see restricted material
        # — the designation frames, it does not grant.
        pr = demo.principal_for(self.p, T, "developer")  # public-only
        self.assertNotIn("restricted", pr.scopes)


if __name__ == "__main__":
    unittest.main()
