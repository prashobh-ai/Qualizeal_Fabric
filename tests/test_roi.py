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
    def test_hours_saved_value_and_ratio(self):
        roi.set_settings({"minutes_saved_per_question": 15, "loaded_rate_per_hour": 100})
        o = roi.overview(self.p, T)
        answered = o["value"]["questions_answered"]
        self.assertEqual(answered, len(_QS))
        # hours saved = answered * minutes / 60
        self.assertAlmostEqual(o["value"]["hours_saved"], round(answered * 15 / 60.0, 2), places=2)
        # value delivered = hours saved * loaded rate
        self.assertAlmostEqual(
            o["roi"]["value_delivered_usd"], round(o["value"]["hours_saved"] * 100, 2), places=2
        )
        # ratio = value / spend (or None when spend is zero, as in extractive mode)
        spend = o["roi"]["spend_usd"]
        if spend:
            self.assertAlmostEqual(
                o["roi"]["ratio"], round(o["roi"]["value_delivered_usd"] / spend, 2)
            )
        else:
            self.assertIsNone(o["roi"]["ratio"])

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
