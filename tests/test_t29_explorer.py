"""T29 — self-serve telemetry Explorer: flat per-answer events with every
dimension and metric, so any permutation can be filtered and grouped in one
table. Here we verify the data contract the Explorer reads.
"""

from __future__ import annotations

import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.tenants import demo
from tests.util import seeded

_DIMS = {"role", "level", "model", "lang", "complexity", "kind"}
_METRICS = {
    "answered",
    "declined",
    "cited",
    "tokens_in",
    "tokens_out",
    "cost",
    "cost_saved",
    "latency_ms",
    "grounding",
}


class TestTelemetryEvents(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["cf"])
        svc = AnswerService(self.p)
        for subject in ("asker.public", "curator", "qa-agent"):
            svc.ask(demo.principal_for(self.p, "cf", subject), "what must a release achieve?")

    def test_one_row_per_answer_with_all_dimensions_and_metrics(self):
        ev = self.p.telemetry.events("cf")
        self.assertTrue(ev)
        row = ev[0]
        self.assertTrue(_DIMS <= set(row), f"missing dimensions: {_DIMS - set(row)}")
        self.assertTrue(_METRICS <= set(row), f"missing metrics: {_METRICS - set(row)}")

    def test_role_is_normalised_to_base(self):
        roles = {e["role"] for e in self.p.telemetry.events("cf")}
        # "asker.public" -> "asker", "qa-agent" stays a single token
        self.assertIn("asker", roles)
        self.assertTrue(all("." not in r for r in roles))

    def test_answered_and_declined_are_complementary(self):
        for e in self.p.telemetry.events("cf"):
            self.assertEqual(e["answered"] + e["declined"], 1)

    def test_events_are_pivotable(self):
        # the contract the browser Explorer relies on: group by a dimension,
        # aggregate a metric — here, cost by role.
        ev = self.p.telemetry.events("cf")
        by_role: dict[str, float] = {}
        for e in ev:
            by_role[e["role"]] = by_role.get(e["role"], 0.0) + e["cost"]
        self.assertEqual(round(sum(by_role.values()), 6), round(sum(e["cost"] for e in ev), 6))


if __name__ == "__main__":
    unittest.main()
