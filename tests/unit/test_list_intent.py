"""T146 — a list question is scoped by its NOUN, not just "what all".

The defect: the T142 list intent fired on any "what all …" and always composed the
services list, so "what all pages do we have in my confluence" answered with the
services. The fix routes a list question by its noun to exactly one source.

This gate runs the ACTUAL shipped ``engine.js`` under a Node browser shim over a
built snapshot (the same runner as the services gate) and asserts:

* "what all pages … confluence" lists Confluence pages — every citation is a
  Confluence page, none a Service/Product document;
* "what all services …" lists Service documents — every citation title is a
  service;
* "what all repos …" lists repositories — every citation names a repo, none a
  Service/Product document;
* "what all is happening today" (a list verb, no list noun) does NOT enter the
  list path — it is answered normally, not as one of the list templates.

Node runs in the CI ``mcp`` job; the test skips cleanly where Node is absent.
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

Q_PAGES = "what all pages do we have in my confluence"
Q_SERVICES = "what all services are given by Qualizeal?"
Q_REPOS = "what all repos does QualiZeal have"
Q_NONOUN = "what all is happening today"
QUESTIONS = [Q_PAGES, Q_SERVICES, Q_REPOS, Q_NONOUN]

_LIST_TEMPLATES = (
    "Your Confluence has",
    "QualiZeal offers these",
    "QualiZeal has ",
    "The fabric has ",
)


@unittest.skipIf(shutil.which("node") is None, "node not available")
class TestListIntent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts import build_showcase

        cls.tmp = tempfile.mkdtemp(prefix="kf-listintent-")
        cls.out = os.path.join(cls.tmp, "showcase")
        # Hermetic build: another test may set KF_DATA_ROOT / KF_FABRIC_ROOT at import
        # time (e.g. test_token_classes), which makes _seed skip the self-contained
        # Confluence reseed. Clear them so this build always seeds Confluence, then
        # restore. Also cap the corpus so the one build stays fast.
        saved = {k: os.environ.pop(k, None) for k in ("KF_DATA_ROOT", "KF_FABRIC_ROOT")}
        os.environ["KF_SHOWCASE_CORPUS_LIMIT"] = "12"
        try:
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
        finally:
            os.environ.pop("KF_SHOWCASE_CORPUS_LIMIT", None)
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        assert res.stdout.strip(), f"no runner output; stderr={res.stderr[-2000:]}"
        cls.by_q = {r["question"]: r for r in json.loads(res.stdout).get("results", [])}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _titles(self, q):
        return [c["title"] for c in self.by_q[q]["citations"]]

    def test_pages_lists_confluence(self):
        r = self.by_q[Q_PAGES]
        self.assertEqual(r["kind"], "answer")
        # A real list, not the "No Confluence pages …" empty (which also says Confluence).
        self.assertIn("Your Confluence has", r["answer_text"], r["answer_text"])
        titles = self._titles(Q_PAGES)
        self.assertGreaterEqual(len(titles), 3)
        for t in titles:
            self.assertFalse(t.startswith(("Service ", "Product ")), f"page list cited a doc: {t}")

    def test_services_lists_service_docs(self):
        r = self.by_q[Q_SERVICES]
        self.assertEqual(r["kind"], "answer")
        self.assertIn("services", r["answer_text"].lower())
        titles = self._titles(Q_SERVICES)
        self.assertGreaterEqual(len(titles), 3)
        for t in titles:
            self.assertTrue(t.startswith("Service "), f"services list cited a non-service: {t}")

    def test_repos_lists_repositories(self):
        r = self.by_q[Q_REPOS]
        self.assertEqual(r["kind"], "answer")
        self.assertIn("repositories", r["answer_text"].lower())
        titles = self._titles(Q_REPOS)
        self.assertGreaterEqual(len(titles), 1)
        for t in titles:
            self.assertFalse(t.startswith(("Service ", "Product ")), f"repo list cited a doc: {t}")

    def test_list_verb_without_noun_is_not_a_list(self):
        r = self.by_q[Q_NONOUN]
        # It may still be answered, but never via a list template.
        for tmpl in _LIST_TEMPLATES:
            self.assertNotIn(
                tmpl, r.get("answer_text", ""), f"no-noun question hit list template: {tmpl}"
            )


if __name__ == "__main__":
    unittest.main()
