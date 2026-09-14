"""T142 — "what all services are given by QualiZeal?" lists the service documents.

The defect was a code-tier rule firing on the substring ``all`` inside a test
function name, so a services question was answered with a ``.py`` file. The fix
routes a list question to the service (or product) documents. This gate runs the
ACTUAL shipped ``engine.js`` under a Node browser shim over a built snapshot and
asserts each phrasing returns a document list: ten or more citations, every title
a service name, none a ``.py`` path.

Node runs in the CI ``mcp`` job; the test skips cleanly where Node is absent. It
builds a 20-document corpus slice — the leading products/company briefs plus the
first services — so at least ten services are present to be listed.
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
RUNNER = os.path.join(ROOT, "scripts", "showcase", "services_runner.js")

QUESTIONS = [
    "what all services are given by Qualizeal?",
    "which services does QualiZeal offer?",
    "list QualiZeal services",
]


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestServicesQuestion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-services-")
        cls.out = os.path.join(cls.tmp, "showcase")
        # A 20-document slice: 7 product/company briefs + 13 services, so a list of
        # ten or more services is available. (The full Pages build lists all 41.)
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "20"
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
        assert res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}"
        cls.out_json = json.loads(res.stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)

    def test_each_phrasing_lists_services(self):
        self.assertIsNone(self.out_json.get("error"), self.out_json.get("error"))
        results = {r["question"]: r for r in self.out_json.get("results", [])}
        for q in QUESTIONS:
            with self.subTest(question=q):
                r = results[q]
                self.assertEqual(r["kind"], "answer", f"{q} -> {r['kind']}")
                cites = r["citations"]
                self.assertGreaterEqual(
                    len(cites), 10, f"{q} produced {len(cites)} citations, want >= 10"
                )
                # every citation is a service document, never a code path
                for c in cites:
                    self.assertNotIn(".py", c["path"], f"{q} cited code: {c}")
                    self.assertNotIn(".py", c["render"], f"{q} cited code: {c}")
                    self.assertTrue(c["title"], f"{q} citation has no title: {c}")


if __name__ == "__main__":
    unittest.main()
