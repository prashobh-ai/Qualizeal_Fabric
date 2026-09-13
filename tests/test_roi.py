"""T96 — leadership ROI + technical OTel views, composed from the spine.

The ROI math is exact and settings-driven (hours saved, value, ratio), every
rollup reconciles with a panel the Admin can already open, and the observability
reconciliation check confirms the dashboard totals match the raw span counters
within one percent. The span waterfall reads the spine's spans for one trace.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import pytest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.telemetry import roi
from knowledge_fabric.tenants import demo
from tests.util import T, seeded

_QS = [
    ("asker.public", "what must a release achieve before promotion?"),
    ("developer", "where is the retry logic"),
    ("cto", "what is the platform"),
]


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-roi-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


class Base(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.svc = AnswerService(self.p)
        for who, q in _QS:
            self.svc.ask(demo.principal_for(self.p, T, who), q)


class TestSettings(Base):
    def test_defaults_and_update(self):
        s = roi.get_settings()
        self.assertEqual(s["minutes_saved_per_question"], 8)
        s2 = roi.set_settings({"minutes_saved_per_question": 12, "loaded_rate_per_hour": 90})
        self.assertEqual(s2["minutes_saved_per_question"], 12)
        self.assertEqual(s2["loaded_rate_per_hour"], 90.0)
        # invalid values are ignored, not stored
        s3 = roi.set_settings({"minutes_saved_per_question": -5, "loaded_rate_per_hour": "oops"})
        self.assertEqual(s3["minutes_saved_per_question"], 12)
        self.assertEqual(s3["loaded_rate_per_hour"], 90.0)


class TestRoiMath(Base):
    def test_value_and_ratio_are_consumption_driven(self):
        """T125 — value delivered and the ROI ratio derive from the live question
        count and the real token consumption of EVERY answer, not a fed calculator.
        In extractive mode (no key) tokens are still measured and the compute cost
        is imputed, so MODEL SPEND is non-zero and the ROI ratio is a real number."""
        o = roi.overview(self.p, T)
        answered = o["value"]["questions_answered"]
        self.assertEqual(answered, len(_QS))

        # real token consumption is recorded even keyless (extractive path)
        self.assertGreater(o["value"]["total_tokens"], 0)
        self.assertEqual(
            o["value"]["total_tokens"], o["value"]["tokens_in"] + o["value"]["tokens_out"]
        )

        # value delivered = the frontier-equivalent worth of those tokens
        expected_value = roi._frontier_value(o["value"]["tokens_in"], o["value"]["tokens_out"])
        self.assertAlmostEqual(o["roi"]["value_delivered_usd"], expected_value, places=6)

        # spend is real (imputed self-hosted compute), so the ratio is a real number
        spend = o["roi"]["spend_usd"]
        self.assertGreater(spend, 0.0)
        self.assertAlmostEqual(o["roi"]["ratio"], round(expected_value / spend, 2))
        # the frontier baseline is named for auditability
        self.assertIn("frontier_model", o["roi"])

    def test_labour_view_still_settings_driven(self):
        """The secondary labour view stays tunable (hours saved × rate), but no
        longer defines value delivered or the ratio."""
        roi.set_settings({"minutes_saved_per_question": 15, "loaded_rate_per_hour": 100})
        o = roi.overview(self.p, T)
        answered = o["value"]["questions_answered"]
        self.assertAlmostEqual(o["value"]["hours_saved"], round(answered * 15 / 60.0, 2), places=2)
        self.assertAlmostEqual(
            o["value"]["labour_value_usd"], round(o["value"]["hours_saved"] * 100, 2), places=2
        )

    def test_adoption_and_quality_reconcile_with_analytics(self):
        o = roi.overview(self.p, T)
        a = self.p.telemetry.analytics(T, "7d")
        self.assertEqual(o["adoption"]["active_users"], len(a.get("per_user", {})))
        self.assertEqual(o["quality"]["trust_avg"], a.get("grounding_avg"))
        self.assertEqual(o["quality"]["citation_coverage"], a.get("citation_coverage"))
        self.assertIn("sla_line", o["service"])


class TestObservability(Base):
    def test_reconciliation_within_tolerance_and_traces(self):
        o = roi.observability(self.p, T)
        self.assertTrue(o["reconciliation"]["ok"], o["reconciliation"])
        self.assertLessEqual(o["reconciliation"]["answers"]["delta_pct"], 1.0)
        self.assertLessEqual(o["reconciliation"]["cost"]["delta_pct"], 1.0)
        self.assertTrue(o["traces"])
        self.assertTrue(all("trace_id" in t for t in o["traces"]))

    def test_waterfall_returns_ordered_spans(self):
        o = roi.observability(self.p, T)
        tid = o["traces"][0]["trace_id"]
        self.assertTrue(tid)
        w = roi.waterfall(self.p, T, tid)
        self.assertEqual(w["trace_id"], tid)
        self.assertTrue(w["spans"])
        offsets = [s["offset_ms"] for s in w["spans"]]
        self.assertEqual(offsets, sorted(offsets))


if __name__ == "__main__":
    unittest.main()
