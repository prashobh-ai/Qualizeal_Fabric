"""T143 — delete a source, then re-add it.

The defect: deleting GitHub from Admin removed its card and there was no restore
route, so it could never be added back. The fix keeps a deleted source listed in a
``removed`` state and adds ``POST /admin/connectors/add`` to restore it.

This gate runs the ACTUAL shipped ``engine.js`` under a Node browser shim over a
built snapshot and asserts, twice (the cycle is idempotent):

* baseline — the github card is present and active, ``repositories`` > 0, and the
  repositories question answers;
* after delete — the github card is STILL listed but flagged ``_deleted``, the
  active connector count drops by one, ``repositories`` is 0, and the question no
  longer answers;
* after re-add — the github card is active again, counts and answers return.

Node runs in the CI ``mcp`` job; the test skips cleanly where Node is absent. A
small corpus slice keeps the one build fast.
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
RUNNER = os.path.join(ROOT, "scripts", "showcase", "readd_runner.js")


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestReadd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-readd-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "8"
        try:
            build_showcase.build(cls.out)
            res = subprocess.run(
                [
                    "node",
                    RUNNER,
                    os.path.join(cls.out, "engine.js"),
                    os.path.join(cls.out, "snapshot.json"),
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

    def test_delete_then_readd(self):
        self.assertIsNone(self.result.get("error"), self.result.get("error"))
        failed = [c for c in self.result.get("checks", []) if not c["ok"]]
        self.assertTrue(self.result.get("ok"), "failed checks: " + json.dumps(failed, indent=2))
        # sanity: the cycle ran twice with all its assertions
        self.assertGreaterEqual(len(self.result.get("checks", [])), 24)


if __name__ == "__main__":
    unittest.main()
