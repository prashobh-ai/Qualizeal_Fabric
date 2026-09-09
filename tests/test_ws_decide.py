"""WS2 · UNDERSTAND & DECIDE — model selector "why", escalation, cache, i18n."""
import unittest

from knowledge_fabric.answer import lang, selector
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind
from knowledge_fabric.tenants import demo
from tests.util import seeded


class _P:  # minimal candidate stand-in for selector unit tests
    class _Pas:
        def __init__(self, d): self.document_id = d
    def __init__(self, d): self.passage = self._Pas(d)


class TestDecide(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["q-quality"])
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, "q-quality", "asker.public")

    def test_selector_levels_and_why(self):
        lookup = selector.classify("what is the coverage target", [_P("d1")], 0.8, False)
        self.assertEqual(lookup["level_name"], "lookup")
        self.assertTrue(lookup["reasons"] and lookup["explain"])
        reason = selector.classify("why does an open defect block a release", [_P("d1")], 0.7, False)
        self.assertEqual(reason["level_name"], "reason")
        multi = selector.classify("what changed", [_P("a"), _P("b"), _P("c")], 0.7, True)
        self.assertGreaterEqual(multi["level"], 2)
        self.assertTrue(any(r["code"] == "graph_multi_hop" for r in multi["reasons"]))

    def test_escalation_only_on_confidence_fail(self):
        d = selector.classify("what is the coverage target", [_P("d1")], 0.8, False)
        same = selector.escalate(d, confidence=0.9, floor=0.35)
        self.assertEqual(same["level"], d["level"])
        up = selector.escalate(d, confidence=0.1, floor=0.35)
        self.assertEqual(up["level"], d["level"] + 1)
        self.assertTrue(any(r["code"] == "confidence_fail" for r in up["reasons"]))

    def test_answer_carries_why_card(self):
        a = self.svc.ask(self.asker, "why does an open defect block its dependent releases?")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertTrue(a.why and a.why.get("explain"))
        self.assertGreaterEqual(a.level, 1)

    def test_answer_cache_saves_cost(self):
        q = "what is the acceptance criteria for coverage?"
        a1 = self.svc.ask(self.asker, q)
        a2 = self.svc.ask(self.asker, q)
        self.assertTrue(a2.cache_hit)
        self.assertGreater(a2.cost_saved, 0)
        self.assertEqual(a2.cost, 0.0)

    def test_language_detection(self):
        self.assertEqual(lang.detect("what is the coverage target"), "en")
        self.assertEqual(lang.detect("quel est le critère d acceptation"), "fr")
        self.assertEqual(lang.detect("¿cuál es el criterio de aceptación?"), "es")
        self.assertEqual(lang.detect("カバレッジの基準は何ですか"), "ja")

    def test_query_translation_maps_domain_terms(self):
        en = lang.translate_query_to_en("critère acceptation couverture", "fr")
        self.assertIn("criteria", en)
        self.assertIn("coverage", en)

    def test_non_english_query_answers_cited(self):
        a = self.svc.ask(self.asker, "quel est le critère d acceptation pour la couverture?")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertEqual(a.lang, "fr")
        self.assertTrue(a.citations)


if __name__ == "__main__":
    unittest.main()
