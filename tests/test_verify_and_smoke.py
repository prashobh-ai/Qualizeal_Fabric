"""T124b/c — the post-ingest fabric assertion and the smoke-demo assertion,
offline with synthetic facts and a stub answer service."""

from __future__ import annotations

import unittest

from scripts import smoke_demo
from scripts import verify_showcase as vs

SOURCES = [
    {"key": "website", "kind": "website", "url": "https://qualizeal.com"},
    {"key": "github_qualizeal", "kind": "github", "org": "Qualizeal"},
    {"key": "jira", "kind": "jira", "dashboards": ["10001"], "boards": ["34"], "projects": ["V1"]},
    {"key": "confluence", "kind": "confluence", "pages": ["1703938"]},
]


def _good_facts():
    return {
        "documents": {
            "by_area": {"website": 25, "github": 8, "confluence": 3},
            "by_type": {},
            "total": 36,
        },
        "repositories": {"Qualizeal/app": {}, "Qualizeal/site": {}},
        "jira_boards": {"34": {"name": "Platform", "issues": 12}},
        "jira_dashboards": {"10001": {"name": "QA Status", "gadgets": [{"title": "Defects"}]}},
        "jira_projects": {"V1": {"issues": {"by_status": {"In Progress": 4, "Done": 8}}}},
    }


class TestVerifyFabricSources(unittest.TestCase):
    def test_all_sources_have_documents(self):
        self.assertEqual(vs.verify_fabric_sources(_good_facts(), SOURCES), [])

    def test_website_too_few_documents(self):
        f = _good_facts()
        f["documents"]["by_area"]["website"] = 5
        errs = vs.verify_fabric_sources(f, SOURCES)
        self.assertTrue(any("website" in e and "5" in e for e in errs))

    def test_preflight_ok_but_zero_is_named_a_connector_bug(self):
        f = _good_facts()
        f["documents"]["by_area"]["confluence"] = 0
        preflight = {"results": [{"key": "confluence", "ok": True, "skipped": False}]}
        errs = vs.verify_fabric_sources(f, SOURCES, preflight)
        self.assertTrue(
            any("confluence" in e and "connector bug, not credentials" in e for e in errs)
        )

    def test_github_repos_but_no_code_documents(self):
        f = _good_facts()
        f["documents"]["by_area"]["github"] = 0
        errs = vs.verify_fabric_sources(f, SOURCES)
        self.assertTrue(any("github" in e and "0 code" in e for e in errs))

    def test_jira_board_missing(self):
        f = _good_facts()
        f["jira_boards"] = {}
        errs = vs.verify_fabric_sources(f, SOURCES)
        self.assertTrue(any("board-34" in e for e in errs))

    def test_jira_by_status_empty(self):
        f = _good_facts()
        f["jira_projects"]["V1"]["issues"]["by_status"] = {}
        errs = vs.verify_fabric_sources(f, SOURCES)
        self.assertTrue(any("by_status census empty" in e for e in errs))


class _Ans:
    def __init__(self, kind, cited):
        self.kind = type("K", (), {"value": kind})()
        self.citations = [object()] if cited else []


class _Svc:
    def __init__(self, mapping):
        self.mapping = mapping

    def ask(self, asker, q):
        for kind, question in smoke_demo.QUESTIONS.items():
            if question == q:
                return self.mapping.get(kind, _Ans("answer", True))
        return _Ans("gap", False)


class TestRunSmoke(unittest.TestCase):
    def test_all_cited_answers_pass(self):
        svc = _Svc({k: _Ans("answer", True) for k in smoke_demo.QUESTIONS})
        self.assertEqual(smoke_demo.run_smoke(object(), svc, SOURCES, asker=object()), [])

    def test_a_gap_fails_and_names_the_source(self):
        svc = _Svc(
            {
                **{k: _Ans("answer", True) for k in smoke_demo.QUESTIONS},
                "confluence": _Ans("gap", False),
            }
        )
        errs = smoke_demo.run_smoke(object(), svc, SOURCES, asker=object())
        self.assertEqual(len(errs), 1)
        self.assertIn("confluence", errs[0])

    def test_answer_without_citation_fails(self):
        svc = _Svc(
            {
                **{k: _Ans("answer", True) for k in smoke_demo.QUESTIONS},
                "jira": _Ans("answer", False),
            }
        )
        errs = smoke_demo.run_smoke(object(), svc, SOURCES, asker=object())
        self.assertTrue(any("jira" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
