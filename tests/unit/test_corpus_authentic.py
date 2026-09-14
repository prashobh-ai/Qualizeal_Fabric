"""T141 — the fabric contains only the organisation's authentic knowledge.

After a showcase build, the baked index must hold no document from this repository
itself (its Python, tests, README) and none of the retired synthetic org filler.
A QualiZeal reader asking about services must never be answered with a test
function or an invented HR policy, so this is a hard gate: it asserts the absence
directly on the built snapshot, and ``verify_showcase`` runs the same check to fail
the Pages build.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

FORBIDDEN_TITLES = {
    "Hr Leave Policy",
    "Onboarding Guide",
    "Security Sso Standard",
    "Test Automation Playbook",
    "Onboarding a New Source",
    "Model Cost Playbook",
}


class TestCorpusAuthentic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-authentic-")
        cls.out = os.path.join(cls.tmp, "showcase")
        os.environ.setdefault("KF_SHOWCASE_CORPUS_LIMIT", "8")
        build_showcase.build(cls.out)
        with open(os.path.join(cls.out, "snapshot.json"), encoding="utf-8") as fh:
            cls.snap = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_no_own_repository_document(self):
        docs = (self.snap.get("index") or {}).get("docs") or []
        offenders = [d for d in docs if "Qualizeal_Fabric" in (d.get("url") or "")]
        self.assertEqual(offenders, [], f"own-repo documents leaked in: {offenders}")

    def test_no_test_or_code_passages(self):
        passages = (self.snap.get("index") or {}).get("passages") or []
        bad_symbol = [
            p for p in passages if (p.get("symbol") or "").startswith(("tests.", "test_"))
        ]
        self.assertEqual(bad_symbol, [], f"test symbols leaked in: {bad_symbol[:3]}")
        bad_uri = [p for p in passages if "Qualizeal_Fabric" in (p.get("url") or "")]
        self.assertEqual(bad_uri, [], f"own-repo passages leaked in: {bad_uri[:3]}")

    def test_no_synthetic_org_titles(self):
        docs = (self.snap.get("index") or {}).get("docs") or []
        titles = {d.get("title", "") for d in docs}
        leaked = titles & FORBIDDEN_TITLES
        self.assertEqual(leaked, set(), f"synthetic/meta titles leaked in: {leaked}")


if __name__ == "__main__":
    unittest.main()
