"""Evaluation gate + knowledge health + identifier safety (Section 20, 15, 16, 19)."""
import unittest

from knowledge_fabric.evaluation import gate
from knowledge_fabric.health import metrics
from knowledge_fabric.tenants import demo
from tests.util import seeded


class TestEvalHealth(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["acme-assurance"])

    def test_question_bank_passes_gate(self):
        v = gate.evaluate(self.p, "acme-assurance", candidate_version=1)
        self.assertTrue(v["passed"], v["regressions"])
        self.assertGreaterEqual(v["metrics"]["citation_coverage"], 0.75)

    def test_corrupted_citation_blocks_promotion(self):
        # run a clean evaluate first so the answer cache is populated — the gate
        # must still see the corruption (caches are invalidated on index change).
        self.assertTrue(gate.evaluate(self.p, "acme-assurance", 1)["passed"])
        gate.corrupt_citation_coordinates(self.p, "acme-assurance")
        v = gate.promote_if_passes(self.p, "acme-assurance", candidate_version=2)
        self.assertFalse(v["passed"], "corrupt index must be blocked (I10)")
        self.assertIsNone(v["promoted"])

    def test_health_snapshot_and_risk_register(self):
        h = metrics.latest(self.p, "acme-assurance")
        self.assertIn("coverage", h)
        self.assertGreaterEqual(h["traceability"], 1.0)   # every passage has provenance
        risks = metrics.risk_register(self.p, "acme-assurance")
        self.assertIsInstance(risks, list)

    def test_identifier_safety_validation(self):
        problems = demo.validate_identifiers()
        self.assertEqual(problems, [], f"demo data must be identifier-safe: {problems}")

    def test_identifier_safety_fails_on_real_identifier(self):
        # inject a real-looking phone number and confirm the validator would catch it
        import re
        from knowledge_fabric.tenants.demo import _UNSAFE
        bad = "call us at 212-555-9034 today"
        hit = any(lbl and pat.search(bad) for pat, lbl in _UNSAFE)
        self.assertTrue(hit)


if __name__ == "__main__":
    unittest.main()
