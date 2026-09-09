"""F0.2 — the Pages showcase builder and its verifier."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from scripts import build_showcase, verify_showcase


class TestShowcaseBuilder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kf-showcase-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_produces_required_files(self):
        out = os.path.join(self.tmp, "showcase")
        build_showcase.build(out)
        self.assertTrue(os.path.isfile(os.path.join(out, ".nojekyll")))
        self.assertTrue(os.path.isfile(os.path.join(out, "index.html")))
        self.assertTrue(os.path.isfile(os.path.join(out, "assets", "brand",
                                                     "qualizeal-mark.jpg")))
        with open(os.path.join(out, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        # relative paths only; no external URLs; base-path agnostic
        self.assertIn("./assets/brand/qualizeal-mark.jpg", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        # copyright + wordmark + banner
        self.assertIn("QualiZeal. All rights reserved.", html)
        self.assertIn("QualiZeal Knowledge Fabric", html)
        self.assertIn("Showcase build pending", html)

    def test_verifier_passes_clean_build(self):
        out = os.path.join(self.tmp, "showcase")
        build_showcase.build(out)
        self.assertEqual(verify_showcase.verify(out), [])

    def test_verifier_fails_on_external_url(self):
        out = os.path.join(self.tmp, "showcase")
        build_showcase.build(out)
        with open(os.path.join(out, "index.html"), "a", encoding="utf-8") as fh:
            fh.write('<script src="https://example.com/x.js"></script>')
        errors = verify_showcase.verify(out)
        self.assertTrue(errors)
        self.assertIn("https://example.com/x.js", " ".join(errors))

    def test_verifier_fails_on_missing_nojekyll(self):
        out = os.path.join(self.tmp, "showcase")
        build_showcase.build(out)
        os.remove(os.path.join(out, ".nojekyll"))
        errors = verify_showcase.verify(out)
        self.assertTrue(any(".nojekyll" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
