"""T156 — the dashboard analytics are LIVE, not a frozen build-time snapshot.

Drives the shipped ``engine.js`` under a Node browser shim and asserts that
``/api/analytics`` folds the visitor's own questions (the kf.events ledger) onto the
baked series, so every chart the dashboard draws grows as the visitor asks:

* the answer count and cost-saved rise after asking;
* the response is marked ``live_merged`` and the savings breakdown gains the T158
  buckets (``repeat-cache`` / ``level-selection``);
* the timeseries gains a trailing bucket that carries a ``latency_ms`` field (the
  latency-over-time series the dashboard charts);
* the per-user and model-distribution rollups are populated.

Also checks the shipped dashboard page carries the new T156 chart calls.
Skips cleanly where Node is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNNER = os.path.join(ROOT, "scripts", "showcase", "analytics_runner.js")


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestLiveAnalytics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-analytics-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "20"
        try:
            build_showcase.build(cls.out)
        finally:
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)
        res = subprocess.run(["node", RUNNER, cls.out], capture_output=True, text=True, timeout=120)
        assert res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}"
        cls.r = json.loads(res.stdout)
        assert "error" not in cls.r, cls.r

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_answers_and_savings_rise_after_asking(self):
        b, a = self.r["before"], self.r["after"]
        self.assertGreaterEqual(a["answers"], b["answers"] + 4)
        self.assertGreater(a["cost_saved"], b["cost_saved"])
        self.assertTrue(a["merged"])

    def test_savings_breakdown_has_the_t158_buckets(self):
        techniques = self.r["after"]["techniques"]
        self.assertIn("repeat-cache", techniques)
        self.assertIn("level-selection", techniques)
        self.assertGreater(self.r["savings_map"].get("repeat-cache", 0), 0)

    def test_timeseries_grows_with_a_latency_bearing_tail(self):
        b, a = self.r["before"], self.r["after"]
        self.assertGreater(a["ts"], b["ts"])
        self.assertTrue(a["tail_has_latency"])

    def test_per_user_and_model_rollups_populated(self):
        a = self.r["after"]
        self.assertGreaterEqual(a["users"], 1)
        self.assertGreaterEqual(a["models"], 1)

    def test_dashboard_page_has_the_new_chart_calls(self):
        with open(os.path.join(self.out, "dashboard", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn("Model distribution", html)
        self.assertIn("Complexity mix", html)
        self.assertIn("Latency (avg ms) over", html)
        self.assertIn("Spend by user", html)
        self.assertIn("timeseries,'latency_ms'", html)


if __name__ == "__main__":
    unittest.main()
