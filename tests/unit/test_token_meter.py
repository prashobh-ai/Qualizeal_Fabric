"""T55/T56 — telemetry insights reconcile with the API call ledger.

Every assertion seeds ledger rows under a temp ``KF_DATA_ROOT`` with
``api_ledger.record`` and checks the insight against the ledger it reads: phase
costs sum to the ledger total, efficiency is a bounded ratio, waste and
percentiles are hand-computable, and active/idle is total minus active.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

os.environ.setdefault("KF_MODEL_MODE", "extractive")

from knowledge_fabric.telemetry import api_ledger, insights  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="kf-t55-")


def _record(purpose, model, tin, tout):
    return api_ledger.record(
        purpose=purpose,
        model=model,
        usage={"input_tokens": tin, "output_tokens": tout},
        latency_ms=5,
    )


class _Ledgered(unittest.TestCase):
    def setUp(self):
        # Scope the ledger to our temp root for the duration of the test only,
        # restoring the prior value in tearDown so no other module's KF_DATA_ROOT
        # is clobbered regardless of test order. A clean ledger per test keeps
        # exact token/cost sums deterministic.
        self._prev_root = os.environ.get("KF_DATA_ROOT")
        os.environ["KF_DATA_ROOT"] = _TMP
        shutil.rmtree(os.path.join(_TMP, "api_calls"), ignore_errors=True)

    def tearDown(self):
        if self._prev_root is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = self._prev_root

    def _seed(self):
        _record("answer_bake", "claude-sonnet-4-6", 1000, 200)
        _record("repo_summary", "claude-haiku-4-5", 500, 50)
        _record("agent_step", "claude-sonnet-4-6", 800, 120)
        _record("translate", "claude-haiku-4-5", 100, 30)
        _record("image_describe", "claude-haiku-4-5", 200, 40)
        _record("capability_classify", "claude-haiku-4-5", 60, 10)  # -> "other"


class TestCostBreakdown(_Ledgered):
    def test_phase_costs_sum_to_ledger_total(self):
        self._seed()
        rows = api_ledger.rows(1)
        total = api_ledger.summary(rows)["cost_usd"]
        bd = insights.cost_breakdown(1)
        self.assertAlmostEqual(sum(b["cost_usd"] for b in bd), total, places=6)
        # calls and tokens reconcile too
        self.assertEqual(sum(b["calls"] for b in bd), len(rows))
        self.assertEqual(
            sum(b["tokens"] for b in bd),
            sum(r["input_tokens"] + r["output_tokens"] for r in rows),
        )

    def test_retrieval_phase_is_always_zero(self):
        self._seed()
        bd = {b["phase"]: b for b in insights.cost_breakdown(1)}
        self.assertIn("retrieval", bd)
        self.assertEqual(bd["retrieval"]["cost_usd"], 0.0)
        self.assertEqual(bd["retrieval"]["calls"], 0)
        # the two summary purposes collapse into one phase
        self.assertEqual(bd["summary"]["calls"], 2)
        self.assertIn("other", bd)  # capability_classify landed here


class TestEfficiency(_Ledgered):
    def test_ratio_in_unit_interval_and_cited_tokens(self):
        self._seed()
        e = insights.efficiency(1)
        self.assertGreaterEqual(e["efficiency"], 0.0)
        self.assertLessEqual(e["efficiency"], 1.0)
        # cited output = answer_bake(200) + agent_step(120)
        self.assertEqual(e["cited_output_tokens"], 320)
        # total = every in+out token the ledger billed
        rows = api_ledger.rows(1)
        self.assertEqual(
            e["total_tokens"], sum(r["input_tokens"] + r["output_tokens"] for r in rows)
        )

    def test_efficiency_zero_on_empty_ledger(self):
        e = insights.efficiency(1)
        self.assertEqual(e, {"efficiency": 0.0, "cited_output_tokens": 0, "total_tokens": 0})


class TestWaste(_Ledgered):
    def test_crafted_case(self):
        w = insights.waste(1000, 300)
        self.assertEqual(w["waste_tokens"], 700)
        self.assertEqual(w["prompt_tokens"], 1000)
        self.assertEqual(w["waste_pct"], 70.0)
        self.assertEqual(w["target_pct"], insights.WASTE_TARGET_PCT)

    def test_no_waste_when_all_cited(self):
        w = insights.waste(500, 500, target_pct=20)
        self.assertEqual(w["waste_tokens"], 0)
        self.assertEqual(w["waste_pct"], 0.0)


class TestPercentiles(_Ledgered):
    def test_matches_hand_computed(self):
        # 1..10 by nearest-rank index int(n*p/100): p50 -> xs[5]=6, p95 -> xs[9]=10.
        p = insights.percentiles(list(range(1, 11)))
        self.assertEqual(p["p50"], 6.0)
        self.assertEqual(p["p95"], 10.0)
        self.assertEqual(p["p99"], 10.0)

    def test_empty_is_zero(self):
        self.assertEqual(insights.percentiles([]), {"p50": 0.0, "p95": 0.0, "p99": 0.0})

    def test_timing_percentiles_over_events(self):
        events = [
            {"latency_ms": 100, "timing": {"total_ms": 100, "phase_ms": {"retrieve": 40}}},
            {"latency_ms": 200, "timing": {"total_ms": 200, "phase_ms": {"retrieve": 60}}},
        ]
        tp = insights.timing_percentiles(events)
        self.assertIn("latency", tp)
        self.assertIn("retrieve", tp["phases"])


class TestActiveVsIdle(_Ledgered):
    def test_idle_is_total_minus_active(self):
        events = [
            {"latency_ms": 1000, "timing": {"total_ms": 1000, "active_ms": 300}},
            {"timing": {"total_ms": 500, "active_ms": 200}},
        ]
        r = insights.active_vs_idle(events)
        self.assertEqual(r["per_answer"][0]["active_ms"], 300.0)
        self.assertEqual(r["per_answer"][0]["idle_ms"], 700.0)  # 1000 - 300
        self.assertEqual(r["per_answer"][1]["idle_ms"], 300.0)  # 500 - 200
        # aggregate: active 500, idle 1000, active_pct 500/1500
        self.assertEqual(r["aggregate"]["active_ms"], 500.0)
        self.assertEqual(r["aggregate"]["idle_ms"], 1000.0)
        self.assertEqual(r["aggregate"]["active_pct"], round(100 * 500 / 1500, 2))

    def test_phase_sum_fallback_and_active_clamp(self):
        # no explicit active_ms -> summed from phase_ms; active clamped to total
        events = [{"timing": {"total_ms": 90, "phase_ms": {"retrieve": 40, "summarise": 100}}}]
        r = insights.active_vs_idle(events)
        self.assertEqual(r["per_answer"][0]["active_ms"], 90.0)  # min(140, 90)
        self.assertEqual(r["per_answer"][0]["idle_ms"], 0.0)


class TestBurnRate(_Ledgered):
    def test_positive_when_cost_present(self):
        _record("answer_bake", "claude-sonnet-4-6", 1_000_000, 100_000)
        b = insights.burn_rate(1)
        self.assertGreater(b["cost_usd"], 0.0)
        self.assertGreater(b["cost_per_hour"], 0.0)
        self.assertGreater(b["projected_per_day"], 0.0)
        self.assertAlmostEqual(b["projected_per_day"], b["cost_per_hour"] * 24.0, places=6)

    def test_zero_on_empty_ledger(self):
        b = insights.burn_rate(1)
        self.assertEqual(b["cost_per_hour"], 0.0)


class TestProviderQuota(_Ledgered):
    def test_open_source_is_unlimited_local(self):
        self.assertEqual(
            insights.provider_quota("open-source"),
            {"provider": "open-source", "limit": "unlimited (local)"},
        )

    def test_anthropic_unknown_without_status(self):
        # no provider_status.json under the temp root
        q = insights.provider_quota("anthropic")
        self.assertEqual(q["provider"], "anthropic")
        self.assertEqual(q["limit"], "unknown")

    def test_registry_is_adaptable(self):
        self.assertIn("anthropic", insights.QUOTA_REGISTRY)
        self.assertIn("open-source", insights.QUOTA_REGISTRY)


class TestPerDimensionAndOverview(_Ledgered):
    def test_per_dimension_buckets_reconcile(self):
        self._seed()
        total = api_ledger.summary(api_ledger.rows(1))["cost_usd"]
        pd = insights.per_dimension(1)
        for dim, buckets in pd.items():
            self.assertAlmostEqual(
                sum(b["cost_usd"] for b in buckets.values()), total, places=6, msg=dim
            )

    def test_overview_bundles_ledger_and_events(self):
        self._seed()
        events = [{"latency_ms": 100, "timing": {"total_ms": 100, "active_ms": 50}}]
        ov = insights.overview(1, events=events)
        for key in (
            "totals",
            "cost_breakdown",
            "efficiency",
            "per_dimension",
            "provider_quota",
            "burn_rate",
            "definitions",
            "active_vs_idle",
            "timing_percentiles",
        ):
            self.assertIn(key, ov)
        self.assertIsInstance(ov["definitions"], dict)


if __name__ == "__main__":
    unittest.main()
