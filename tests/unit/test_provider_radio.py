"""T147 — the provider radio works through the ACTUAL shipped engine.js.

Builds a small showcase, then drives the engine under a Node browser shim: the
provider manifest lists open-source (baked/available) plus Claude/OpenAI marked
unavailable with a reason; switching the provider persists; and a baked question is
still answered. The paid providers light up automatically once a funded build bakes
their sets — this gate proves the mechanism on the open-source path.

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
RUNNER = os.path.join(ROOT, "scripts", "showcase", "provider_runner.js")


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestProviderRadio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-provradio-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "8"
        try:
            build_showcase.build(cls.out)
            res = subprocess.run(
                ["node", RUNNER, cls.out, "what is QMentisAI"],
                capture_output=True,
                text=True,
                timeout=120,
            )
        finally:
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)
        assert res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}"
        cls.r = json.loads(res.stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_manifest_open_source_available_others_not(self):
        self.assertIsNone(self.r.get("error"), self.r.get("error"))
        by = {p["key"]: p["available"] for p in self.r["providers"]}
        self.assertTrue(by.get("open-source"))
        self.assertFalse(by.get("anthropic"))
        self.assertFalse(by.get("openai"))
        self.assertEqual(self.r["selected"], "open-source")

    def test_switch_persists_and_is_honest(self):
        self.assertEqual(self.r["switched"], "ok")
        self.assertEqual(self.r["after"]["provider"], "anthropic")
        self.assertFalse(self.r["after"]["available"])
        self.assertTrue(self.r["after"]["reason"])

    def test_baked_question_still_answered(self):
        self.assertEqual(self.r["served_kind"], "answer")


if __name__ == "__main__":
    unittest.main()
