"""F8.1 — the interactive Pages showcase builder and its verifier.

The showcase is the real product surfaces served as static files, backed by a
baked ``snapshot.json`` and the browser-side ``engine.js``. These tests build
it once and assert the structure, the snapshot contents, and that the verifier
guards the invariants (no external URLs, required files present).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest

from scripts import build_showcase, verify_showcase


class TestShowcaseBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="kf-showcase-")
        cls.out = os.path.join(cls.tmp, "showcase")
        # A small corpus slice keeps this build-in-a-test fast; the real Pages
        # build ingests the full corpus (verified separately).
        os.environ.setdefault("KF_SHOWCASE_CORPUS_LIMIT", "8")
        build_showcase.build(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _read(self, *parts):
        with open(os.path.join(self.out, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_required_files_and_surfaces(self):
        for req in (".nojekyll", "index.html", "engine.js", "snapshot.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, req)), f"missing {req}")
        for name in ("workspace", "admin", "curator", "signin", "dashboard"):
            self.assertTrue(
                os.path.isfile(os.path.join(self.out, name, "index.html")),
                f"missing {name}/index.html",
            )
        for logo in ("qualizeal-lockup.png", "qualizeal-mark.png"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, "assets", "brand", "logo", logo)))

    def test_snapshot_has_every_role_and_endpoint(self):
        snap = json.loads(self._read("snapshot.json"))
        for key in ("login", "get", "answers", "galaxy", "usage", "suggestions", "analytics"):
            self.assertIn(key, snap)
        for subject in ("asker.public", "asker.restricted", "curator", "admin"):
            self.assertIn(subject, snap["login"])
        # admin bucket carries the governance reads; curator the quality reads
        self.assertIn("/admin/users", snap["get"]["admin"])
        self.assertIn("/curator/quality", snap["get"]["curator"])
        # at least one baked answer carries a full card + a lit galaxy
        self.assertTrue(snap["answers"])
        lit = [g for g in snap["galaxy"].values() if g.get("nodes")]
        self.assertTrue(lit, "no galaxy has any activated nodes")

    def test_landing_is_explainable_and_relative(self):
        html = self._read("index.html")
        self.assertNotIn("Showcase build pending", html)  # the placeholder is gone
        self.assertIn("./assets/brand/logo/qualizeal-lockup.png", html)
        self.assertIn("./engine.js", html)
        for w in ("Look it up", "Quote it", "Summarise it", "Reason about it"):
            self.assertIn(w, html)  # the four reader levels
        self.assertIn("Open the Workspace", html)
        self.assertIn('id="cbtn"', html)  # the chatbot widget
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_surface_pages_inject_the_engine(self):
        for name in ("workspace", "admin", "curator", "signin", "dashboard"):
            html = self._read(name, "index.html")
            self.assertIn(f"window.KF_SURFACE={name!r}", html)
            self.assertIn('src="../engine.js"', html)
            self.assertIn("../assets/brand/", html)  # relative assets
            self.assertNotIn("/static/assets/", html)  # rewritten away
            # Self-contained: nothing is LOADED from the network. The one outbound
            # navigation the product needs — the Workspace "Get full answer" button
            # opening an `ask` issue on GitHub (T45) — is an anchor built at click
            # time, never a fetched resource.
            self.assertEqual(verify_showcase._EXTERNAL.findall(html), [])
            self.assertNotIn("http://", html)
            for url in re.findall(r"https://[\w./-]+", html):
                self.assertTrue(url.startswith("https://github.com/"), url)

    def test_engine_has_no_external_urls(self):
        eng = self._read("engine.js")
        self.assertNotIn("http://", eng)
        self.assertNotIn("https://", eng)

    def test_verifier_passes_clean_build(self):
        self.assertEqual(verify_showcase.verify(self.out), [])

    def test_verifier_fails_on_external_url(self):
        tmp = tempfile.mkdtemp(prefix="kf-showcase-ext-")
        try:
            out = os.path.join(tmp, "showcase")
            build_showcase.build(out)
            with open(os.path.join(out, "index.html"), "a", encoding="utf-8") as fh:
                fh.write('<script src="https://example.com/x.js"></script>')
            errors = verify_showcase.verify(out)
            self.assertTrue(any("example.com" in e for e in errors))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_verifier_fails_on_missing_files(self):
        tmp = tempfile.mkdtemp(prefix="kf-showcase-miss-")
        try:
            out = os.path.join(tmp, "showcase")
            build_showcase.build(out)
            os.remove(os.path.join(out, ".nojekyll"))
            os.remove(os.path.join(out, "snapshot.json"))
            errors = verify_showcase.verify(out)
            self.assertTrue(any(".nojekyll" in e for e in errors))
            self.assertTrue(any("snapshot.json" in e for e in errors))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
