"""T33 — load test: the governed answer path under concurrent load.

`scripts/load_test.py` fires many asks across many synthetic principals over a
thread pool and gates the result on SLOs — throughput (QPS), latency
percentiles, a zero error/throttle budget, unique trajectories (no request
bleed), and a warm cache that is not slower than cold. This test runs a small
burst end-to-end (fast, deterministic on the model-free path) and unit-tests the
percentile + breach logic without a full run.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

from scripts import load_test


class TestLoadHelpers(unittest.TestCase):
    """The metric + gate logic — no fabric needed."""

    def test_percentile_picks_ordered_bucket(self):
        xs = [float(i) for i in range(1, 101)]  # 1..100
        self.assertEqual(load_test._percentile(xs, 0.50), 51.0)
        self.assertEqual(load_test._percentile(xs, 0.95), 96.0)
        self.assertEqual(load_test._percentile(xs, 0.99), 100.0)

    def test_percentile_empty_is_zero(self):
        self.assertEqual(load_test._percentile([], 0.95), 0.0)

    def test_report_flags_pass_and_fail(self):
        phase = {
            "requests": 10,
            "concurrency": 4,
            "wall_s": 0.1,
            "qps": 100.0,
            "latency_ms": {"p50": 5.0, "p95": 9.0, "p99": 9.5, "max": 10.0, "mean": 5.5},
            "errors": 0,
            "throttled": 0,
            "declined": 2,
            "distinct_traces": 10,
        }
        r = {"passed": True, "slo": load_test.SLO, "cold": phase, "warm": phase, "breaches": []}
        self.assertIn("PASS", load_test.format_report(r))
        r2 = dict(r, passed=False, breaches=["p95 900ms > 750ms"])
        out = load_test.format_report(r2)
        self.assertIn("FAIL", out)
        self.assertIn("p95 900ms", out)


class TestLoadEndToEnd(unittest.TestCase):
    """A small burst against the real answer path — the shape the gate asserts."""

    def test_small_burst_passes_slos(self):
        r = load_test.run_load(requests=80, concurrency=8)
        self.assertTrue(r["passed"], load_test.format_report(r))
        for phase in (r["cold"], r["warm"]):
            # zero error / throttle budget, and every answered ask carries its
            # own trajectory (no cross-request context bleed under concurrency).
            self.assertEqual(phase["errors"], 0)
            self.assertEqual(phase["throttled"], 0)
            self.assertEqual(phase["distinct_traces"], phase["requests"])
        # the warm run is served largely from the answer cache, so it must not
        # be materially slower than the cold run.
        self.assertGreaterEqual(r["warm"]["qps"], r["cold"]["qps"] * 0.9)

    def test_gate_is_enforced_not_cosmetic(self):
        """A tight SLO must actually fail the run — proves the gate has teeth."""
        saved = dict(load_test.SLO)
        try:
            load_test.SLO["min_qps"] = 10_000_000.0  # unreachable
            r = load_test.run_load(requests=40, concurrency=4)
            self.assertFalse(r["passed"])
            self.assertTrue(any("qps" in b for b in r["breaches"]))
        finally:
            load_test.SLO.clear()
            load_test.SLO.update(saved)


if __name__ == "__main__":
    unittest.main()
