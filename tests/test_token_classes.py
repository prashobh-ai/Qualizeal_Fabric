"""T95 — deep token telemetry: the input/thinking/output/cache split.

Thinking tokens are recorded (a first-class ledger field and the whole spend of
reasoning/tool-loop turns), the five token classes stack to the total and
reconcile with the ledger, and efficiency/waste read off the same rows.
"""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-tokclasses-")

from knowledge_fabric.telemetry import api_ledger, insights  # noqa: E402


class TestTokenClasses(unittest.TestCase):
    def setUp(self):
        # a fresh ledger root per test so counts are deterministic
        os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-tokclasses-")

    def _record(self, purpose, tin, tout, thinking=0, cr=0, cw=0):
        api_ledger.record(
            purpose=purpose,
            model="claude-sonnet-5",
            usage={
                "input_tokens": tin,
                "output_tokens": tout,
                "thinking_tokens": thinking,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cw,
            },
            latency_ms=10.0,
        )

    def test_ledger_records_thinking_tokens(self):
        row = api_ledger.record(
            purpose="answer_bake",
            model="m",
            usage={"input_tokens": 5, "output_tokens": 7, "thinking_tokens": 3},
            latency_ms=1.0,
        )
        self.assertEqual(row["thinking_tokens"], 3)
        self.assertEqual(api_ledger.summary([row])["thinking_tokens"], 3)

    def test_classes_stack_to_total_and_reconcile(self):
        # a final synthesis (with reported thinking) + two reasoning turns + cache
        self._record("answer_bake", tin=100, tout=40, thinking=10, cr=20, cw=5)
        self._record("agent_step", tin=30, tout=15)
        self._record("agent_step", tin=25, tout=10)

        tc = insights.token_classes(1)
        # the five classes stack exactly to total
        self.assertEqual(
            tc["input"] + tc["thinking"] + tc["output"] + tc["cache_read"] + tc["cache_write"],
            tc["total"],
        )
        # thinking = both agent-step turns (30+15+25+10=80) + the reported 10 = 90
        self.assertEqual(tc["thinking"], 90)
        self.assertEqual(tc["input"], 100)  # only the synthesis call's prompt
        self.assertEqual(tc["output"], 30)  # 40 output minus 10 reported thinking
        self.assertEqual((tc["cache_read"], tc["cache_write"]), (20, 5))

        # reconcile with the ledger's own summed input/output (thinking is a
        # reclassification of those same billed tokens, never new tokens)
        summ = api_ledger.summary(api_ledger.rows(1))
        billed = (
            summ["input_tokens"] + summ["output_tokens"] + summ["cache_read"] + summ["cache_write"]
        )
        self.assertEqual(tc["total"], billed)

    def test_overview_exposes_classes_waste_and_definitions(self):
        self._record("answer_bake", tin=100, tout=20, thinking=0)
        self._record("agent_step", tin=40, tout=10)
        ov = insights.overview(1)
        self.assertIn("token_classes", ov)
        self.assertIn("waste", ov)
        self.assertIn("token_classes", ov["definitions"])
        self.assertIn("thinking", ov["definitions"])
        # efficiency numerator (cited output) is a subset of total → ratio in [0,1]
        self.assertGreaterEqual(ov["efficiency"]["efficiency"], 0.0)
        self.assertLessEqual(ov["efficiency"]["efficiency"], 1.0)


if __name__ == "__main__":
    unittest.main()
