"""T157 — curator golden answers: save, serve-first, and delete, on the real engine.

Drives the shipped ``engine.js`` under a Node browser shim and asserts the golden
flow the spec describes:

* saving a verified {question, answer, citations} persists it (GET lists it);
* asking that question is then served FIRST from golden — Level 0, ``$0``, model
  ``golden``, cited — before retrieval or any model call;
* a lightly reworded variant (≥ 0.9 similarity) still resolves to golden, while an
  unrelated question does not;
* deleting the golden retires it (GET no longer lists it) and the question falls back
  to the normal answer path.

Also checks the shipped Curator page has the Golden-answers panel and the Workspace
carries the "Save as golden" affordance. Skips cleanly where Node is absent.
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
RUNNER = os.path.join(ROOT, "scripts", "showcase", "golden_runner.js")


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestGoldenQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-golden-")
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

    def test_curator_is_signed_in(self):
        self.assertTrue(self.r["token"])

    def test_before_save_is_not_golden(self):
        self.assertFalse(self.r["before"]["golden"])

    def test_save_persists_and_lists(self):
        self.assertTrue(self.r["save_ok"])
        self.assertTrue(self.r["id"])
        self.assertEqual(self.r["list_after_save"], 1)

    def test_question_is_served_from_golden_at_zero_cost(self):
        a = self.r["after"]
        self.assertTrue(a["golden"])
        self.assertEqual(a["level"], "golden")
        self.assertEqual(a["cost"], 0)
        self.assertEqual(a["model"], "golden")
        self.assertGreaterEqual(a["cites"], 1)

    def test_reworded_matches_but_unrelated_does_not(self):
        self.assertTrue(self.r["reworded"]["golden"])
        self.assertFalse(self.r["unrelated"]["golden"])

    def test_delete_retires_the_golden(self):
        self.assertTrue(self.r["delete_ok"])
        self.assertEqual(self.r["list_after_delete"], 0)
        self.assertFalse(self.r["after_delete"]["golden"])

    def test_curator_and_workspace_surfaces_carry_the_feature(self):
        with open(os.path.join(self.out, "curator", "index.html"), encoding="utf-8") as fh:
            cur = fh.read()
        self.assertIn("Golden answers", cur)
        self.assertIn("golden-form", cur)
        with open(os.path.join(self.out, "workspace", "index.html"), encoding="utf-8") as fh:
            ws = fh.read()
        self.assertIn("Save as golden", ws)


if __name__ == "__main__":
    unittest.main()
