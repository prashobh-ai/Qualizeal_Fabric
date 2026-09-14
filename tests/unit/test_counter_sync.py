"""T149 — "My usage" is live, and a real answer lights the galaxy.

The defect: the Workspace usage panel stayed at 0 after questions (it read the baked
usage, not the live event ledger), and the galaxy showed "Nothing linked" on a real
answer. The fix computes /api/usage from the live event ledger and lights the galaxy
from the answer's own citations when no baked galaxy exists.

This gate runs the ACTUAL shipped engine.js under a Node browser shim over a built
snapshot, asks three distinct questions as a signed-in reader, and asserts the Today
counter reads three (computed live) and that at least one real answer lit the galaxy.

Node runs in the CI mcp job; the test skips cleanly where Node is absent.
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
RUNNER = os.path.join(ROOT, "scripts", "showcase", "usage_runner.js")

QUESTIONS = [
    "what is QMentisAI",
    "what is performance engineering",
    "what is security testing",
]


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestCounterSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-counter-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "12"
        try:
            build_showcase.build(cls.out)
            res = subprocess.run(
                [
                    "node",
                    RUNNER,
                    os.path.join(cls.out, "engine.js"),
                    os.path.join(cls.out, "snapshot.json"),
                    *QUESTIONS,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        finally:
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)
        assert res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}"
        cls.result = json.loads(res.stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_today_counter_moves(self):
        self.assertIsNone(self.result.get("error"), self.result.get("error"))
        today = self.result.get("today", {})
        self.assertEqual(
            today.get("questions"),
            len(QUESTIONS),
            f"Today.questions should equal {len(QUESTIONS)}; got {today}",
        )

    def test_real_answer_lights_galaxy(self):
        per = self.result.get("per", [])
        answered = [p for p in per if p.get("kind") == "answer"]
        self.assertTrue(answered, "no question was answered")
        self.assertTrue(
            any(p.get("activated", 0) > 0 for p in answered),
            f"a real answer must light the galaxy; got {per}",
        )


if __name__ == "__main__":
    unittest.main()
