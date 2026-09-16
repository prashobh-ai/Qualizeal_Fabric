"""T160 — Admin user management with role + designation, on the real engine.

Drives the shipped ``engine.js`` under a Node browser shim and asserts the spec's
flow: an admin adds a user with a role and a designation (plus email / department /
team / daily cap); the user appears in ``/admin/users`` with its effective principals;
signing in as that user carries the designation so answers are conditioned by it
(T27 — a Tester gets the "quality" persona lens). Also: edit (upsert), disable (blocks
sign-in), enable, reset budget, delete, and CSV-style bulk upsert.

Also checks the built Admin page carries the role + designation dropdowns and the CSV
import. Skips cleanly where Node is absent.
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
RUNNER = os.path.join(ROOT, "scripts", "showcase", "user_runner.js")


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestUserManagement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-users-")
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

    def test_add_captures_role_designation_and_fields(self):
        a = self.r["added"]
        self.assertTrue(a["present"])
        self.assertEqual(a["designation"], "Tester")
        self.assertEqual(a["email"], "jo@acme.com")
        self.assertEqual(a["department"], "QA")
        self.assertEqual(a["roles"], ["asker"])
        self.assertEqual(a["scopes"], ["public"])
        self.assertEqual(a["status"], "active")

    def test_signin_carries_designation_and_conditions_answers(self):
        self.assertEqual(self.r["signin_designation"], "Tester")
        self.assertEqual(self.r["whoami"]["designation"], "Tester")
        # T27 — a Tester designation drives the "quality" persona lens (tester-shaped).
        self.assertEqual(self.r["answer_persona"], "quality")
        self.assertEqual(self.r["answer_designation"], "Tester")

    def test_edit_upserts_the_same_subject(self):
        e = self.r["edited"]
        self.assertEqual(e["roles"], ["curator"])
        self.assertEqual(e["department"], "Quality Eng")

    def test_disable_blocks_signin_enable_restores(self):
        self.assertTrue(self.r["disabled_login_blocked"])
        self.assertTrue(self.r["reenabled_login_ok"])

    def test_delete_and_bulk_upsert(self):
        self.assertTrue(self.r["deleted_absent"])
        self.assertTrue(self.r["bulk_present"])

    def test_admin_page_has_the_dropdowns_and_csv_import(self):
        with open(os.path.join(self.out, "admin", "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="nu-role"', html)
        self.assertIn('id="nu-designation"', html)
        for d in ("Developer", "Tester", "Architect", "Delivery Manager", "Executive"):
            self.assertIn(d, html)
        self.assertIn("Import users from CSV", html)
        self.assertIn("Access matrix", html)


if __name__ == "__main__":
    unittest.main()
