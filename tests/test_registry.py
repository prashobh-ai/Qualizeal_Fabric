"""T82 — the known-question registry per persona.

The registry is governed metadata over the existing answer path: it drives
per-persona suggested questions, keeps a matched question on the fast path, and
gives the Curator a list to maintain. These tests prove all three: each
persona's set resolves, a registry question never escalates, and a curator
add/disable persists and takes effect.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import pytest

from knowledge_fabric.answer import personas  # noqa: E402
from knowledge_fabric.answer import registry as kr  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.contracts.types import AnswerKind  # noqa: E402
from knowledge_fabric.mcp import server as mcp_server  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402
from tests.util import T, seeded  # noqa: E402

_FAST = {"none", "fast"}


@pytest.fixture(autouse=True)
def _pin_data_root():
    """Each test gets its own fabric-data root, so a curator write in one test
    never leaks into another (the runtime registry file lives here)."""
    prev = os.environ.get("KF_DATA_ROOT")
    tmp = tempfile.mkdtemp(prefix="kf-registry-")
    os.environ["KF_DATA_ROOT"] = tmp
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


class RegistryBase(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, T, "asker.public")


class TestMatch(RegistryBase):
    def test_match_prefers_specific_and_ignores_nonmatch(self):
        reg = kr.Registry.load()
        self.assertEqual(
            reg.match("how many projects use automation")["id"], "biz.projects_capability"
        )
        self.assertEqual(
            reg.match("which repos are production-ready")["id"], "biz.production_ready"
        )
        self.assertEqual(reg.match("what depends on the selector")["id"], "arch.depends_on")
        # a question the registry does not cover is not forced onto the fast path
        self.assertIsNone(reg.match("please summarise the quarterly board narrative in depth"))

    def test_persona_filter_narrows_but_never_empties(self):
        reg = kr.Registry.load()
        # a developer's audiences include the dev entry
        hit = reg.match("where is classify defined", persona="developer")
        self.assertEqual(hit["id"], "dev.symbol_defined")


class TestSuggestions(RegistryBase):
    def test_every_persona_has_suggestions(self):
        reg = kr.Registry.load()
        for persona in personas.PROFILE:
            sugg = reg.suggestions(persona)
            self.assertTrue(sugg, f"{persona} had no known-question suggestions")
            for s in sugg:
                self.assertTrue(s["question"])
                self.assertIn("kind", s)

    def test_suggestions_differ_by_persona(self):
        reg = kr.Registry.load()
        dev = {s["question"] for s in reg.suggestions("developer")}
        cur = {s["question"] for s in reg.suggestions("curation")}
        self.assertNotEqual(dev, cur)


class TestFastPathLock(RegistryBase):
    def test_every_known_example_takes_the_fast_path(self):
        reg = kr.Registry.load()
        for e in reg.enabled():
            for ex in e.get("examples") or []:
                a = self.svc.ask(self.asker, ex)
                self.assertIn(
                    a.tier, _FAST, f"known question escalated off the fast path: {ex} -> {a.tier}"
                )
                self.assertLessEqual(a.level or 0, 2, f"known question rose above fast: {ex}")

    def test_no_escalation_even_with_a_model_available(self):
        # with a model available the escalation guard must still hold: a known
        # question never rises to reason/escalation and never records the
        # confidence_fail escalation reason.
        p = seeded([T], model_mode="mock")
        svc = AnswerService(p)
        asker = demo.principal_for(p, T, "asker.public")
        reg = kr.Registry.load()
        for e in reg.enabled():
            for ex in e.get("examples") or []:
                a = svc.ask(asker, ex)
                self.assertIn(a.tier, _FAST, f"known question escalated: {ex} -> {a.tier}")
                self.assertLessEqual(a.level or 0, 2)
                codes = {r.get("code") for r in (a.why or {}).get("reasons", [])}
                self.assertNotIn("confidence_fail", codes, f"known question escalated: {ex}")

    def test_lock_records_reason_on_a_grounded_known_question(self):
        # register a known question over a corpus question that answers, so the
        # match reaches the selector and the lock stamps its reason.
        reg = kr.Registry.load()
        reg.upsert(
            {
                "id": "test.promotion",
                "pattern": "what must a release achieve before promotion",
                "persona": ["business", "delivery"],
                "answer_kind": "definition",
                "source": "corpus",
                "examples": ["what must a release achieve before promotion?"],
            }
        )
        a = self.svc.ask(self.asker, "what must a release achieve before promotion?")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertIn(a.tier, _FAST)
        self.assertLessEqual(a.level or 0, 2)
        codes = {r.get("code") for r in (a.why or {}).get("reasons", [])}
        self.assertIn("known_question", codes)


class TestCuratorCrud(RegistryBase):
    def test_add_persists_and_appears_for_its_persona(self):
        reg = kr.Registry.load()
        reg.upsert(
            {
                "id": "biz.custom_kpi",
                "pattern": "how many demos ran for <client>",
                "persona": ["business", "sales"],
                "answer_kind": "facts",
                "examples": ["how many demos ran for the pilot"],
            }
        )
        # a fresh load sees the curator's addition (persisted to fabric-data)
        reg2 = kr.Registry.load()
        ids = {e["id"] for e in reg2.all()}
        self.assertIn("biz.custom_kpi", ids)
        # and it is offered to a delivery/business reader (Home shows the top
        # slice; the full persona set carries the curator's addition)
        qs = {s["question"] for s in reg2.suggestions("delivery", limit=50)}
        self.assertIn("how many demos ran for the pilot", qs)

    def test_disable_retires_a_question_without_deleting_it(self):
        reg = kr.Registry.load()
        reg.set_enabled("hr.values", False)
        reg2 = kr.Registry.load()
        # still present (governed, not deleted) but no longer matched or suggested
        self.assertIn("hr.values", {e["id"] for e in reg2.all()})
        self.assertIsNone(reg2.match("what are the values"))
        gen = {s["question"] for s in reg2.suggestions("general")}
        self.assertNotIn("what are the values", gen)

    def test_upsert_requires_id_and_pattern(self):
        reg = kr.Registry.load()
        with self.assertRaises(ValueError):
            reg.upsert({"pattern": "no id here"})


class TestMcpTool(RegistryBase):
    def test_list_known_questions_persona_and_all(self):
        one = mcp_server.tool_list_known_questions("developer")["result"]
        self.assertEqual(one["persona"], "developer")
        self.assertTrue(one["known_questions"])
        self.assertTrue(one["suggestions"])
        every = mcp_server.tool_list_known_questions()["result"]
        self.assertGreaterEqual(len(every["known_questions"]), len(one["known_questions"]))


if __name__ == "__main__":
    unittest.main()
