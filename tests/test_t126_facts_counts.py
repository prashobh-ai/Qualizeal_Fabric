"""T126/T127 — count and inventory questions answer from facts, ahead of the
code-symbol tier, so "how many repos are in github" is the real count and not a
matching test/function symbol. Also covers Jira issues, Confluence pages and the
document total, and confirms a genuine code question still gets the code.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import pytest

from knowledge_fabric import facts as factsmod
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.tenants import demo
from tests.util import seeded

TENANT = "cf"

# A code file whose symbols contain "github" and "repos" — exactly the shape that
# used to hijack "how many repos" into a code answer before the facts tier.
_CODE = '''\
def list_github_repos(org):
    """List the repositories in a GitHub org."""
    return client.get(f"/orgs/{org}/repos")


def test_github_repos_allow_list(org):
    """A test that filters the github repos allow-list."""
    assert list_github_repos(org)
'''


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-t126-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


def _write_facts():
    factsmod.fd.write_json(
        factsmod.fd.data_path("facts.json", mkdir=True),
        {
            "repositories": {
                "Qualizeal/qualizeal-fabric": {"full_name": "Qualizeal/qualizeal-fabric"},
                "Qualizeal/qmentisai": {"full_name": "Qualizeal/qmentisai"},
                "Qualizeal/validaite": {"full_name": "Qualizeal/validaite"},
            },
            "jira_projects": {
                "QF": {
                    "name": "QualiZeal Fabric",
                    "url": "https://example.atlassian.net",
                    "board": {"id": 34, "name": "QF board", "columns": []},
                    "issues": {
                        "total": 12,
                        "by_status": {"To Do": 4, "In Progress": 3, "Done": 5},
                        "by_type": {"Story": 7, "Task": 3, "Bug": 2},
                    },
                }
            },
            "confluence_spaces": {
                "AICOE": {"name": "AI CoE", "url": "https://example/wiki", "pages": 8}
            },
            "documents": {"total": 20, "by_area": {}, "by_type": {}},
        },
    )


class Base(unittest.TestCase):
    def setUp(self):
        _write_facts()
        self.p = seeded([TENANT], model_mode="extractive")
        intake, worker = Intake(self.p), IngestWorker(self.p, None)
        worker.intake = intake
        intake.submit(
            intake.canonical(
                TENANT,
                "github",
                "github://Qualizeal/qualizeal-fabric/connectors/github.py",
                "github.py",
                _CODE.encode(),
                mime="text/x-python;code",
                acl=["public"],
            )
        )
        worker.drain()
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, TENANT, "developer")

    def _ask(self, q):
        return self.svc.ask(self.asker, q)


class TestCounts(Base):
    def test_how_many_repos_is_a_count_not_a_code_symbol(self):
        a = self._ask("how many repos are then in the github?")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertEqual((a.why or {}).get("level_name"), "facts")
        self.assertIn("3 repositories", a.answer_text)
        self.assertNotIn("```", a.answer_text)  # never a code block
        self.assertNotIn("def ", a.answer_text)

    def test_jira_issue_count(self):
        a = self._ask("how many issues are in the Jira project")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertEqual((a.why or {}).get("level_name"), "facts")
        self.assertIn("12 issues", a.answer_text)

    def test_jira_bug_count(self):
        a = self._ask("how many bugs are in Jira")
        self.assertIn("2 bugs", a.answer_text)

    def test_confluence_page_count(self):
        a = self._ask("how many pages are in the Confluence space")
        self.assertEqual((a.why or {}).get("level_name"), "facts")
        self.assertIn("8 pages", a.answer_text)

    def test_document_total(self):
        a = self._ask("how many documents are in the fabric")
        self.assertIn("20 documents", a.answer_text)


class TestNoRegression(Base):
    def test_a_code_question_without_count_intent_still_gets_code(self):
        # No count intent → the facts tier declines and the identifier tier answers
        # with the actual function, line-anchored.
        a = self._ask("where is the list_github_repos function")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertIn("```", a.answer_text)
        self.assertIn("list_github_repos", a.answer_text)


if __name__ == "__main__":
    unittest.main()
