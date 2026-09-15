"""T159 — the Curator's mutations are REAL, persisted-per-visitor changes.

The four curator panels (curation-modes, review queue, quality, registry) are
baked GETs; the three mutations (curation-mode switch, review-queue decision,
registry enable/disable/upsert) used to be no-op acks. This gate drives the
shipped ``engine.js`` under a Node browser shim and proves that each POST is
reflected on the very next panel GET — exactly what a curator sees on reload:

* the four reads answer for the curator subject with their expected keys;
* setting a source's curation mode to ``manual`` persists to ``sources``;
* disabling a registry entry flips ``enabled`` to false on re-GET, and an
  upsert of a new entry appears in the registry on re-GET;
* a review-queue decision acks (and, when the build baked queue items, the
  decided item drops from the queue).

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
RUNNER = os.path.join(ROOT, "scripts", "showcase", "curator_runner.js")


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestCuratorMutations(unittest.TestCase):
    """POST-then-GET through the real engine: the curator's changes persist."""

    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-cur-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "20"
        try:
            build_showcase.build(cls.out)
        finally:
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)
        res = subprocess.run(
            ["node", RUNNER, cls.out],
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

    def test_curator_is_signed_in(self):
        self.assertTrue(self.r["token"])

    def test_four_panels_read_for_the_curator(self):
        gets = self.r["gets"]
        self.assertIn("default", gets["/curator/curation-modes"])
        self.assertIn("sources", gets["/curator/curation-modes"])
        self.assertIn("items", gets["/curator/review"])
        self.assertIn("entries", gets["/curator/registry"])
        # quality is a substantial governance read, not an empty stub
        self.assertIn("coverage", gets["/curator/quality"])

    def test_curation_mode_switch_persists(self):
        # github had no per-source override before; after the POST it reads manual.
        self.assertTrue(self.r["curation_post_ok"])
        self.assertEqual(self.r["curation_after"], "manual")

    def test_registry_disable_persists(self):
        self.assertTrue(self.r["registry_before_enabled"])  # seeded entry starts enabled
        self.assertTrue(self.r["registry_disable_ok"])
        self.assertFalse(self.r["registry_after_enabled"])  # disabled on re-GET

    def test_registry_upsert_appears(self):
        self.assertTrue(self.r["registry_upsert_ok"])
        self.assertTrue(self.r["registry_upsert_present"])
        # an upserted entry defaults to enabled
        self.assertTrue(self.r["registry_upsert_enabled"])

    def test_review_decision_acks_and_drops(self):
        self.assertTrue(self.r["review_decide_ok"])
        # whatever the queue size, a decided item must not remain in the queue
        self.assertTrue(self.r["review_dropped"])


if __name__ == "__main__":
    unittest.main()
