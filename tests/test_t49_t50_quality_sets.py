"""T49 — quality sets + gates; T50 — the production demo.

The sets are exact because every expectation derives from eval/fixture.py; these
tests pin that derivation (the sheet is the source of truth), the runner's gate
maths, and that a set which cannot run fails the gate loudly instead of passing
by omission.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout

os.environ.setdefault("KF_MODEL_MODE", "off")

from eval import fixture, quality  # noqa: E402


class TestSets(unittest.TestCase):
    def test_every_set_loads_with_required_fields(self):
        need = {
            "facts": {"id", "question", "expect", "kind"},
            "tables": {"id", "question", "expect", "doc_id", "sheet", "sql_hint"},
            "images": {"id", "question", "doc_id", "name", "expect_substring"},
            "jira": {"id", "question", "expect"},
            "agent": {"id", "question", "expect_tools", "expect_cited"},
        }
        sizes = {"facts": 50, "tables": 20, "images": 10, "jira": 10, "agent": 15}
        for name, keys in need.items():
            rows = quality.load_set(name)
            self.assertEqual(len(rows), sizes[name], name)
            for r in rows:
                self.assertTrue(keys <= set(r), (name, r.get("id")))
            self.assertEqual(len({r["id"] for r in rows}), len(rows), f"duplicate ids in {name}")

    def test_table_expectations_derive_from_the_fixture_sheet(self):
        root = fixture.build(tempfile.mkdtemp())
        con = sqlite3.connect(os.path.join(root, "tables", "qa-metrics.xlsx", "Defects.sqlite"))
        for r in quality.load_set("tables"):
            got = con.execute(r["sql_hint"]).fetchone()[0]
            self.assertAlmostEqual(float(got), float(r["expect"]), 2, r["id"])

    def test_facts_and_jira_expectations_derive_from_the_fixture(self):
        kf = fixture.REPOS["acme/knowledge-fabric"]
        self.assertEqual(kf["commits"]["total"], 1284)
        self.assertEqual(kf["contributors_count"], 7)
        self.assertEqual(kf["pull_requests"]["merged"], 212)
        self.assertEqual(fixture.JIRA["QZ"]["issues"]["by_status"]["open"], 14)
        self.assertEqual(len(fixture.REPOS), 3)

    def test_image_expectations_derive_from_the_fixture(self):
        root = fixture.build(tempfile.mkdtemp())
        for r in quality.load_set("images"):
            d = json.load(open(os.path.join(root, "images", r["doc_id"], r["name"] + ".json")))
            self.assertIn(r["expect_substring"].lower(), json.dumps(d).lower(), r["id"])
            self.assertTrue(d["citation_url"])


class TestRunner(unittest.TestCase):
    def test_golden_sets_pass_and_report(self):
        rep = quality.evaluate(["general", "code", "followup", "role", "gap"])
        self.assertTrue(rep["metrics"]["golden_suite"], rep)
        for s in ("general", "code", "followup", "role", "gap"):
            self.assertEqual(rep["sets"][s]["pass"], rep["sets"][s]["n"], s)
        self.assertIn("Quality sets", quality.format_report(rep))

    def test_images_set_runs_model_free_and_is_cited(self):
        rep = quality.evaluate(["images"])
        self.assertEqual(rep["sets"]["images"]["pass"], 10, rep["sets"]["images"])
        self.assertEqual(rep["metrics"]["citation_rate"], 1.0)

    def test_agent_set_is_skipped_without_real_model_not_silently_passed(self):
        rep = quality.evaluate(["agent"], model="extractive")
        self.assertIn("skipped", rep["sets"]["agent"])
        self.assertIsNone(rep["metrics"]["agent_success"])

    def test_matcher(self):
        self.assertTrue(quality._matches(1284, "1,284 commits as of 2026", "count"))
        self.assertFalse(quality._matches(1284, "1,285 commits", "count"))
        self.assertTrue(
            quality._matches(
                ["Python", "JavaScript"], "Languages: Python (90%), JavaScript", "list"
            )
        )
        self.assertTrue(quality._matches({"open": 14, "done": 40}, "open 14, done 40", "count"))

    def test_gate_fails_loudly_when_a_set_cannot_run(self):
        rep = quality.evaluate(["nonsense"])
        self.assertFalse(rep["passed"])
        self.assertTrue(any("could not run" in g for g in rep["gate_failures"]))


class TestProductionDemo(unittest.TestCase):
    def test_demo_runs_on_the_fixture(self):
        import scripts.demo_production as d

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = d.main(["--fixture"])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out[-1500:])
        for beat in (
            "PROVIDER",
            "ADMIN → MODELS",
            "TEN REPOSITORY",
            "JIRA",
            "EXCEL",
            "IMAGE",
            "QUEUE",
            "DONE",
        ):
            self.assertIn(beat, out)
        self.assertIn("project QZ: open 14", out)
        self.assertIn("COUNT(*) = 12", out)
        self.assertIn("kind=diagram", out)


if __name__ == "__main__":
    unittest.main()
