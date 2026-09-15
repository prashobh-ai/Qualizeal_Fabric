"""T158 — the repeat-answer cache + cost-saved ledger, exercised on the real engine.

Drives the shipped ``engine.js`` under a Node browser shim and asserts:

* the same question asked twice within the same active source set: the second is a
  cache hit at Level 0, ``$0``, model ``cache`` — no retrieval, no model call;
* every delivered answer books ``cost_saved`` (the top-tier cost it avoided), tagged
  by bucket (``repeat-cache`` / ``level-selection``), and the live usage ``cost_saved``
  rises accordingly;
* toggling a cited source off changes the active-set fingerprint and invalidates the
  entry, so the next ask is NOT a cache hit.

Skips cleanly where Node is absent (the engine is browser JS).
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
RUNNER = os.path.join(ROOT, "scripts", "showcase", "cache_runner.js")


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestRepeatCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-cache-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "20"
        try:
            build_showcase.build(cls.out)
        finally:
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)
        res = subprocess.run(
            ["node", RUNNER, cls.out, "what is QMentisAI"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}"
        cls.r = json.loads(res.stdout)
        assert "error" not in cls.r, cls.r

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_first_answer_is_not_a_cache_hit(self):
        self.assertEqual(self.r["first"]["kind"], "answer")
        self.assertFalse(self.r["first"]["cache_hit"])

    def test_repeat_is_a_cache_hit_at_zero_cost(self):
        s = self.r["second"]
        self.assertTrue(s["cache_hit"])
        self.assertEqual(s["cost"], 0)
        self.assertEqual(s["level"], "cache")
        self.assertEqual(s["model"], "cache")
        self.assertEqual(s["bucket"], "repeat-cache")

    def test_cost_saved_is_booked_and_accumulates(self):
        # the first answer books its own saving (level-selection or better)...
        self.assertGreater(self.r["first"]["cost_saved"], 0)
        # ...and the live usage cost_saved sums both bookings.
        self.assertGreater(self.r["usage_saved_7d"], self.r["first"]["cost_saved"])

    def test_toggling_a_source_invalidates_the_cache(self):
        # after github is switched off the fingerprint changes → no longer a hit.
        self.assertFalse(self.r["after_toggle_cache_hit"])


if __name__ == "__main__":
    unittest.main()
