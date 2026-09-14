"""T144 — the upload drop zone really adds a document, from Admin AND from Curator.

The defect: upload existed on Admin but was buried, and Curator had no file input at
all. The fix puts a prominent dashed drop zone on both surfaces, wired to the same
``/admin/upload`` / ``/curator/upload`` intake.

This gate drives the ACTUAL shipped ``engine.js`` under a Node browser shim (with the
vendored JSZip and the real ``upload_parse.js``) and uploads the REAL QualiZeal SOW
(``corpus/uploads/Nova_x_QZ_Product_Knowledge_Fabric_SOW_v1_1.docx``) through each
endpoint. The snapshot is built with an EMPTY uploads directory, so the drop
genuinely adds the SOW: the documents tile rises by one and the SOW is answered with
a paragraph coordinate. No synthetic DOCX is used — the fixture is the real SOW.

Node runs in the CI ``mcp`` job; the test skips cleanly where Node is absent.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNNER = os.path.join(ROOT, "scripts", "showcase", "sow_runner.js")
JSZIP = os.path.join(ROOT, "knowledge_fabric", "surfaces", "static", "vendor", "jszip.min.js")
PARSER = os.path.join(ROOT, "knowledge_fabric", "surfaces", "assets", "upload_parse.js")
SOW = os.path.join(ROOT, "corpus", "uploads", "Nova_x_QZ_Product_Knowledge_Fabric_SOW_v1_1.docx")


@unittest.skipIf(shutil.which("node") is None, "node not available")
@unittest.skipUnless(os.path.isfile(SOW), "real SOW fixture not present")
class TestUploadDropzone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-sow-")
        cls.out = os.path.join(cls.tmp, "showcase")
        # Build with an EMPTY uploads dir so the drop genuinely adds the SOW (the
        # real corpus/uploads SOW is already in the full Pages baseline, which would
        # otherwise dedupe the upload). Point UPLOADS_DIR at an empty temp dir.
        empty = os.path.join(cls.tmp, "empty-uploads")
        os.makedirs(empty, exist_ok=True)
        saved = build_showcase.UPLOADS_DIR
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "8"
        try:
            build_showcase.UPLOADS_DIR = empty
            build_showcase.build(cls.out)
        finally:
            build_showcase.UPLOADS_DIR = saved
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)

        sow_json = os.path.join(cls.tmp, "sow.json")
        with open(SOW, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        with open(sow_json, "w", encoding="utf-8") as fh:
            json.dump({"docx_b64": b64, "filename": os.path.basename(SOW)}, fh)

        res = subprocess.run(
            [
                "node",
                RUNNER,
                os.path.join(cls.out, "engine.js"),
                os.path.join(cls.out, "snapshot.json"),
                sow_json,
                JSZIP,
                PARSER,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}"
        cls.result = json.loads(res.stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_sow_uploads_from_admin_and_curator(self):
        self.assertIsNone(self.result.get("error"), self.result.get("error"))
        failed = [c for c in self.result.get("checks", []) if not c["ok"]]
        self.assertTrue(self.result.get("ok"), "failed checks: " + json.dumps(failed, indent=2))
        self.assertGreaterEqual(len(self.result.get("checks", [])), 8)


if __name__ == "__main__":
    unittest.main()
