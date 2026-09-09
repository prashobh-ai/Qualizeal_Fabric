"""WS3 · PROVE — telemetry analytics filters, savings by technique, RBAC."""
import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.tenants import demo
from tests.util import seeded


class TestProve(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["test-fabric"])
        self.svc = AnswerService(self.p)
        for user, q in [
            ("asker.public", "why does an open defect block dependent releases?"),
            ("asker.public", "which requirement has a traceability gap?"),
            ("curator", "how fast must critical defects be triaged?"),
            ("asker.public", "why does an open defect block dependent releases?"),  # cache hit
        ]:
            self.svc.ask(demo.principal_for(self.p, "test-fabric", user), q)

    def test_analytics_core_fields(self):
        a = self.p.telemetry.analytics("test-fabric", "7d")
        for key in ("answers", "tokens_in", "tokens_out", "total_cost", "total_cost_saved",
                    "routing_by_level", "routing_reasons", "routing_by_tier",
                    "savings_by_technique", "per_user", "per_role", "by_language", "timeseries"):
            self.assertIn(key, a)
        self.assertGreaterEqual(a["answers"], 4)

    def test_savings_recorded_by_technique(self):
        a = self.p.telemetry.analytics("test-fabric", "7d")
        self.assertTrue(a["savings_by_technique"], "cache savings must be attributed to a technique")
        self.assertGreater(a["total_cost_saved"], 0)
        self.assertGreater(a["cache_hit_rate"], 0)

    def test_routing_has_reasons(self):
        a = self.p.telemetry.analytics("test-fabric", "7d")
        self.assertTrue(a["routing_reasons"], "model routing must record why (reason codes)")

    def test_filter_by_user(self):
        a = self.p.telemetry.analytics("test-fabric", "7d", subject="curator")
        self.assertTrue(all(u == "curator" for u in a["per_user"]))

    def test_filter_by_role(self):
        a = self.p.telemetry.analytics("test-fabric", "7d", role="asker")
        self.assertIn("asker", a["per_role"])
        self.assertNotIn("curator", a["per_role"])

    def test_window_filter_present(self):
        for w in ("24h", "7d", "all"):
            a = self.p.telemetry.analytics("test-fabric", w)
            self.assertEqual(a["window"], w)

    def test_analytics_requires_curator_or_admin(self):
        asker = demo.principal_for(self.p, "test-fabric", "asker.public")
        curator = demo.principal_for(self.p, "test-fabric", "curator")
        self.assertEqual(self.p.policy.check(asker, "curate", {}).decision.value, "deny")
        self.assertEqual(self.p.policy.check(curator, "curate", {}).decision.value, "allow")


if __name__ == "__main__":
    unittest.main()
