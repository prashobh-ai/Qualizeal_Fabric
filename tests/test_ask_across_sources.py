"""T119 — ask across all four connected sources, and across them, offline.

Each source is connected from a pasted URL (T118), ingested through the real
pipeline via a fake transport, then asked a question that returns a grounded,
cited answer on the model-free extractive path (no credits). The cross-source
question cites both Jira and the repo (T99). A second "personal account" pass
(personal GitHub repo + personal website) proves the path is generic.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse

import pytest

os.environ.setdefault("KF_MODEL_MODE", "extractive")

from knowledge_fabric import relationships as relmod  # noqa: E402
from knowledge_fabric.answer import cross_source  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.connectors.confluence import ConfluenceConnector  # noqa: E402
from knowledge_fabric.connectors.jira_live import JiraLiveConnector  # noqa: E402
from knowledge_fabric.connectors.website import WebsiteConnector  # noqa: E402
from knowledge_fabric.contracts.types import RawItem, now_ms  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402
from tests.util import T, seeded  # noqa: E402

JIRA_SITE = "https://qualizeal-team-aicoe.atlassian.net"
CONF_SITE = "https://aicoe-genq.atlassian.net"


# --------------------------------------------------------------------------
# fake transports — one per source, offline
# --------------------------------------------------------------------------
def jira_transport(url, headers, timeout=30):
    path = urllib.parse.urlparse(url).path

    def ok(payload):
        return 200, json.dumps(payload).encode()

    if path == "/rest/api/3/field":
        return ok([])
    if path == "/rest/api/3/dashboard/10201":
        return ok(
            {"id": "10201", "name": "ValidAIte QA Status", "owner": {"displayName": "Prashobh"}}
        )
    if path == "/rest/api/3/dashboard/10201/gadget":
        return ok(
            {"gadgets": [{"title": "Open Defects by Severity"}, {"title": "Test Run Pass Rate"}]}
        )
    if path in ("/rest/api/3/search/jql", "/rest/api/3/search"):
        return ok(
            {
                "issues": [
                    {
                        "key": "V1-42",
                        "id": "42",
                        "fields": {
                            "summary": "V1-42 work item",
                            "status": {"name": "Done"},
                            "issuetype": {"name": "Task"},
                            "project": {"key": "V1"},
                            "updated": "2026-09-12T08:00:00.000+0000",
                        },
                    }
                ],
                "isLast": True,
                "total": 1,
            }
        )
    return 404, b'{"error":"not found"}'


def conf_transport(url, headers, timeout=60):
    path = urllib.parse.urlparse(url).path

    def ok(payload):
        return 200, json.dumps(payload).encode()

    if path == "/wiki/api/v2/pages/1703938":
        return ok(
            {
                "id": "1703938",
                "title": "Project Plan",
                "spaceId": "555",
                "body": {
                    "storage": {
                        "value": "<h2>Project Plan</h2><p>The project lead is Prashobh Paul.</p>"
                        "<p>The kickoff date is 1 October 2026.</p>"
                    }
                },
                "version": {"number": 2, "createdAt": "2026-09-01T00:00:00Z"},
                "_links": {"webui": "/spaces/COE/pages/1703938/Project+Plan"},
            }
        )
    if path == "/wiki/api/v2/spaces/555":
        return ok({"id": "555", "key": "COE", "name": "AI CoE"})
    if path == "/wiki/api/v2/pages/1703938/attachments":
        return ok({"results": []})
    return 404, b'{"error":"not found"}'


def website_transport_for(host_body):
    def transport(url, headers, timeout=20):
        return 200, host_body.encode("utf-8")

    return transport


def _ingest(p, items):
    intake = Intake(p)
    worker = IngestWorker(p, intake)
    for it in items:
        it.meta.setdefault("ontology", "quality-assurance")
        intake.submit(it)
    return worker.drain()


def _code_item(uri, title, url, body):
    return RawItem(
        tenant=T,
        source="github_live",
        source_version="1",
        uri=uri,
        mime="text/x-python;code",
        title=title,
        bytes_=body.encode("utf-8"),
        meta={
            "acl": ["public"],
            "source_kind": "github",
            "citation_url": url,
            "arrived_at": now_ms(),
            "as_of": "2026-09-12T08:00:00Z",
        },
    )


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-t119-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


LOGIN_CODE = (
    '"""Auth helpers for single sign-on."""\n\n\n'
    "def login(email, password):\n"
    '    """Verify the password and mint a session token for single sign-on."""\n'
    "    user = verify_password(email, password)\n"
    "    return mint_session_token(user)\n"
)

WEBSITE_HTML = (
    "<html><head><title>QualiZeal — Security Testing</title></head><body>"
    "<h1>Security Testing</h1><p>QualiZeal offers security testing services "
    "including penetration testing, vulnerability assessment and secure code "
    "review for enterprise applications.</p></body></html>"
)


class TestAskAcrossSources(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, T, "developer")
        # GitHub — a login implementation, connected as owner/repo.
        _ingest(
            self.p,
            [
                _code_item(
                    "github://qualizeal/platform/auth.py",
                    "auth.py",
                    "https://github.com/qualizeal/platform/blob/main/auth.py",
                    LOGIN_CODE,
                )
            ],
        )
        # Jira — the ValidAIte QA Status dashboard (pasted dashboard URL).
        jira_items, _ = JiraLiveConnector(
            T,
            {"url": JIRA_SITE, "email": "coe@q.co", "token": "x", "dashboards": ["10201"]},
            transport=jira_transport,
        ).pull(None)
        _ingest(self.p, jira_items)
        # Confluence — the Project Plan page (pasted page URL).
        conf_items, _ = ConfluenceConnector(
            T,
            {
                "url": CONF_SITE,
                "email": "coe@q.co",
                "token": "x",
                "pages": ["1703938"],
                "attachments": False,
            },
            transport=conf_transport,
        ).pull(None)
        _ingest(self.p, conf_items)
        # Website — the QualiZeal security-testing page.
        web_items, _ = WebsiteConnector(
            T,
            {"url": "https://qualizeal.com/security-testing"},
            transport=website_transport_for(WEBSITE_HTML),
        ).pull(None)
        _ingest(self.p, web_items)

    def _cited(self, a):
        return bool(a.citations) and (a.citations[0].coordinate.render() != "")

    def test_github_answers_the_login_implementation_with_a_code_citation(self):
        a = self.svc.ask(self.asker, "show the login implementation")
        self.assertTrue(self._cited(a))
        self.assertIn("```", a.answer_text or "")  # an expandable code block
        self.assertTrue(any("auth.py" in (c.document_title or "") for c in a.citations))

    def test_jira_dashboard_answers_what_is_on_it(self):
        a = self.svc.ask(self.asker, "what is on the ValidAIte QA Status dashboard")
        self.assertTrue(self._cited(a))
        text = a.answer_text or ""
        self.assertTrue("Defects" in text or "Pass Rate" in text or "ValidAIte" in text)

    def test_confluence_page_answers_lead_and_kickoff(self):
        lead = self.svc.ask(self.asker, "who is the project lead in the Project Plan page")
        self.assertTrue(self._cited(lead))
        self.assertIn("Prashobh", lead.answer_text or "")
        kick = self.svc.ask(self.asker, "when is the kickoff date in the Project Plan")
        self.assertTrue(self._cited(kick))
        self.assertIn("October", kick.answer_text or "")

    def test_website_answers_security_testing_offer(self):
        a = self.svc.ask(self.asker, "what does QualiZeal offer for security testing")
        self.assertTrue(self._cited(a))
        self.assertIn("security testing", (a.answer_text or "").lower())

    def test_four_sources_cite_four_distinct_documents(self):
        # each source answers from its OWN ingested document — four questions,
        # four distinct cited documents (the login file, the dashboard, the
        # Project Plan page, the security-testing site).
        titles = set()
        for q in (
            "show the login implementation",
            "what is on the ValidAIte QA Status dashboard",
            "who is the project lead in the Project Plan page",
            "what does QualiZeal offer for security testing",
        ):
            a = self.svc.ask(self.asker, q)
            self.assertTrue(a.citations, f"no citation for {q!r}")
            titles.add(a.citations[0].document_title)
        self.assertGreaterEqual(len(titles), 4, f"expected 4 distinct sources, saw {titles}")


class TestCrossSource(unittest.TestCase):
    """The Jira task says Done — is there a matching commit in the repo? (T99)"""

    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        jira_items, _ = JiraLiveConnector(
            T,
            {"url": JIRA_SITE, "email": "coe@q.co", "token": "x", "projects": ["V1"]},
            transport=jira_transport,
        ).pull(None)
        _ingest(self.p, jira_items)
        _ingest(
            self.p,
            [
                RawItem(
                    tenant=T,
                    source="github_live",
                    source_version="1",
                    uri="github://qualizeal/platform/commits/abc123",
                    mime="text/markdown",
                    title="Fix login retry",
                    bytes_=b"Fix login retry\n\nImplements the fix from V1-42.",
                    meta={
                        "acl": ["public"],
                        "source_kind": "github",
                        "citation_url": "https://github.com/qualizeal/platform/commit/abc123",
                        "arrived_at": now_ms(),
                        "as_of": "2026-09-12T08:00:00Z",
                    },
                )
            ],
        )
        relmod.scan(self.p, T)
        self.principal = demo.principal_for(self.p, T, "developer")

    def test_cross_source_cites_both(self):
        res = cross_source.verify(
            self.p, self.principal, "the Jira task V1-42 shows done — check the repo and verify"
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.verdict, "agree")
        srcs = {(c.coordinate.locator or {}).get("source") for c in res.citations}
        self.assertIn("jira", srcs)


class TestGenericPersonalPass(unittest.TestCase):
    """The same connect-and-ask works for a personal GitHub + personal website."""

    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, T, "developer")
        _ingest(
            self.p,
            [
                _code_item(
                    "github://prashobh-ai/sideproject/app.py",
                    "app.py",
                    "https://github.com/prashobh-ai/sideproject/blob/main/app.py",
                    LOGIN_CODE,
                )
            ],
        )
        personal_html = (
            "<html><head><title>My Blog</title></head><body><h1>About</h1>"
            "<p>This personal site documents my weekend robotics experiments.</p>"
            "</body></html>"
        )
        web_items, _ = WebsiteConnector(
            T, {"url": "https://prashobh.dev"}, transport=website_transport_for(personal_html)
        ).pull(None)
        _ingest(self.p, web_items)

    def test_personal_github_code_answers(self):
        a = self.svc.ask(self.asker, "show the login implementation")
        self.assertIn("```", a.answer_text or "")
        self.assertTrue(any("app.py" in (c.document_title or "") for c in a.citations))

    def test_personal_website_answers(self):
        a = self.svc.ask(self.asker, "what does this personal site document")
        self.assertTrue(a.citations)
        self.assertIn("robotics", (a.answer_text or "").lower())


if __name__ == "__main__":
    unittest.main()
