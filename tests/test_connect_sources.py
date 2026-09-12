"""T118 — connect demo sources from a pasted URL, generically.

Covers the URL parser (GitHub org/user/repo, Jira dashboard/board/project,
Confluence page/space, website, bare owner/repo), the allow-list → config
folding, the credential "not configured" status, and the new Jira dashboard/
board and Confluence page ingestion — all offline through fake transports.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse

import pytest

os.environ.setdefault("KF_MODEL_MODE", "extractive")

from knowledge_fabric import facts as factsmod  # noqa: E402
from knowledge_fabric.connectors import admin, url_parse  # noqa: E402
from knowledge_fabric.connectors.confluence import ConfluenceConnector  # noqa: E402
from knowledge_fabric.connectors.jira_live import JiraLiveConnector  # noqa: E402
from tests.util import T, seeded  # noqa: E402


class TestUrlParse(unittest.TestCase):
    def test_github_org_user_repo(self):
        self.assertEqual(url_parse.parse_source_url("https://github.com/QualiZeal"),
                         {"source": "github", "org": "QualiZeal"})
        self.assertEqual(url_parse.parse_source_url("https://github.com/prashobh-ai"),
                         {"source": "github", "org": "prashobh-ai"})
        self.assertEqual(url_parse.parse_source_url("https://github.com/acme/app"),
                         {"source": "github", "repos": ["acme/app"]})
        self.assertEqual(url_parse.parse_source_url("acme/app"),
                         {"source": "github", "repos": ["acme/app"]})
        self.assertEqual(url_parse.parse_source_url("https://github.com/acme/app.git"),
                         {"source": "github", "repos": ["acme/app"]})

    def test_jira_dashboard_board_project(self):
        site = "https://qualizeal-team-aicoe.atlassian.net"
        dash = url_parse.parse_source_url(f"{site}/jira/dashboards/10201")
        self.assertEqual(dash["source"], "jira")
        self.assertEqual(dash["dashboards"], ["10201"])
        self.assertEqual(dash["url"], site)
        board = url_parse.parse_source_url(f"{site}/jira/software/projects/V1/boards/34")
        self.assertEqual(board["source"], "jira")
        self.assertEqual(board["boards"], ["34"])
        proj = url_parse.parse_source_url(f"{site}/browse/V1-42")
        self.assertEqual(proj["projects"], ["V1"])

    def test_confluence_page_and_space(self):
        site = "https://aicoe-genq.atlassian.net"
        page = url_parse.parse_source_url(f"{site}/wiki/spaces/COE/pages/1703938/Project+Plan")
        self.assertEqual(page["source"], "confluence")
        self.assertEqual(page["pages"], ["1703938"])
        self.assertEqual(page["url"], site)
        space = url_parse.parse_source_url(f"{site}/wiki/spaces/COE/overview")
        self.assertEqual(space["source"], "confluence")
        self.assertEqual(space["spaces"], ["COE"])

    def test_website(self):
        self.assertEqual(url_parse.parse_source_url("https://qualizeal.com"),
                         {"source": "website", "urls": ["https://qualizeal.com"]})
        self.assertEqual(url_parse.parse_source_url("https://my-personal-site.dev/blog")["source"],
                         "website")

    def test_empty_is_none(self):
        self.assertIsNone(url_parse.parse_source_url("   "))


class TestConfigFromAllow(unittest.TestCase):
    def test_github_url_and_plain(self):
        frag = url_parse.config_from_allow(
            "github", ["https://github.com/QualiZeal", "https://github.com/acme/app", "x/y"]
        )
        self.assertEqual(frag["org"], "QualiZeal")
        self.assertEqual(frag["repos"], ["acme/app", "x/y"])

    def test_jira_mixed(self):
        site = "https://q.atlassian.net"
        frag = url_parse.config_from_allow(
            "jira",
            [f"{site}/jira/dashboards/1", f"{site}/jira/software/projects/V1/boards/2", "PLAT"],
        )
        self.assertEqual(frag["dashboards"], ["1"])
        self.assertEqual(frag["boards"], ["2"])  # board URL → boards, not its project
        self.assertEqual(frag["projects"], ["PLAT"])  # the plain entry
        self.assertEqual(frag["url"], site)

    def test_confluence_page(self):
        site = "https://x.atlassian.net"
        frag = url_parse.config_from_allow("confluence", [f"{site}/wiki/spaces/COE/pages/999/T"])
        self.assertEqual(frag["pages"], ["999"])
        self.assertEqual(frag["url"], site)

    def test_backward_compatible_plain_lists(self):
        # allow-lists written before T118 (plain ids) still map to native keys.
        self.assertEqual(url_parse.config_from_allow("jira", ["V1", "PLAT"]),
                         {"projects": ["V1", "PLAT"]})
        self.assertEqual(url_parse.config_from_allow("confluence", ["coe"]),
                         {"spaces": ["COE"]})
        self.assertEqual(url_parse.config_from_allow("files", [".pdf", ".docx"]),
                         {"allow_ext": [".pdf", ".docx"]})

    def test_wrong_source_url_ignored(self):
        # a website URL pasted into the github card is ignored, not misfiled.
        self.assertEqual(url_parse.config_from_allow("github", ["https://example.com/x"]), {})


class TestEffectiveConfigUrlAware(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T])

    def test_pasted_urls_become_config_keys(self):
        admin.upsert(self.p, T, "jira", allow=["https://q.atlassian.net/jira/dashboards/7"])
        cfg = admin.effective_config(self.p, T, "jira", {})
        self.assertEqual(cfg["dashboards"], ["7"])
        self.assertEqual(cfg["url"], "https://q.atlassian.net")

    def test_configured_url_not_overridden_by_parsed(self):
        admin.upsert(
            self.p, T, "confluence",
            config={"url": "https://real.atlassian.net"},
            allow=["https://other.atlassian.net/wiki/spaces/COE/pages/5/T"],
        )
        cfg = admin.effective_config(self.p, T, "confluence", {})
        self.assertEqual(cfg["url"], "https://real.atlassian.net")  # config wins
        self.assertEqual(cfg["pages"], ["5"])


class TestCredentialStatus(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T])
        for var in ("JIRA_URL", "JIRA_EMAIL", "JIRA_TOKEN", "CONFLUENCE_URL",
                    "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN", "KF_GITHUB_TOKEN", "GITHUB_TOKEN"):
            os.environ.pop(var, None)

    def test_jira_missing_is_not_configured(self):
        cr = admin.credentials(self.p, T, "jira")
        self.assertFalse(cr["configured"])
        self.assertEqual(set(cr["missing"]), {"JIRA_URL", "JIRA_EMAIL", "JIRA_TOKEN"})

    def test_jira_configured_via_connector_config(self):
        admin.upsert(self.p, T, "jira",
                     config={"url": "https://q.atlassian.net", "email": "a@b.co", "token": "x"})
        cr = admin.credentials(self.p, T, "jira")
        self.assertTrue(cr["configured"])
        self.assertEqual(cr["missing"], [])

    def test_website_needs_no_secret(self):
        self.assertTrue(admin.credentials(self.p, T, "website")["configured"])

    def test_github_public_only_without_token(self):
        cr = admin.credentials(self.p, T, "github")
        self.assertTrue(cr["configured"])  # public works
        self.assertIn("KF_GITHUB_TOKEN", cr["optional_missing"])


SITE = "https://qualizeal-team-aicoe.atlassian.net"


def _jira_transport(url, headers, timeout=30):
    path = urllib.parse.urlparse(url).path

    def ok(payload):
        return 200, json.dumps(payload).encode()

    if path == "/rest/api/3/field":
        return ok([])
    if path == "/rest/api/3/status":
        return ok([{"id": "1", "name": "To Do"}, {"id": "2", "name": "In Progress"}])
    if path == "/rest/api/3/dashboard":
        return ok({"dashboards": [
            {"id": "10201", "name": "ValidAIte QA Status", "owner": {"displayName": "Prashobh"}},
            {"id": "10202", "name": "Private Board", "owner": {"displayName": "Prashobh"}},
        ], "total": 2})
    if path == "/rest/api/3/dashboard/10201":
        return ok({"id": "10201", "name": "ValidAIte QA Status",
                   "owner": {"displayName": "Prashobh"}})
    if path == "/rest/api/3/dashboard/10201/gadget":
        return ok({"gadgets": [{"title": "Sprint Health"}, {"title": "Open Defects"}]})
    if path == "/rest/agile/1.0/board/34":
        return ok({"id": 34, "name": "Platform", "type": "scrum",
                   "location": {"projectKey": "PLAT"}})
    if path == "/rest/agile/1.0/board/34/issue":
        return ok({"issues": [{"key": "PLAT-1", "id": "1", "fields": {
            "summary": "Login flow", "status": {"name": "In Progress"},
            "issuetype": {"name": "Task"}, "project": {"key": "PLAT"},
            "updated": "2026-09-12T08:00:00.000+0000"}}], "total": 1})
    if path == "/rest/agile/1.0/board/34/configuration":
        cols = [{"name": "In Progress", "statuses": [{"id": "2"}]}]
        return ok({"id": 34, "name": "Platform", "columnConfig": {"columns": cols}})
    if path == "/rest/agile/1.0/board/34/sprint":
        return ok({"values": []})
    return 404, b'{"error":"not found"}'


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-t118-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


class TestJiraDashboardsBoards(unittest.TestCase):
    CFG = {"url": SITE, "email": "coe@qualizeal.com", "token": "secret"}

    def test_list_dashboards_returns_all_visible(self):
        conn = JiraLiveConnector("qualizeal", dict(self.CFG), transport=_jira_transport)
        dashboards = conn.list_dashboards()
        self.assertEqual([d["id"] for d in dashboards], ["10201", "10202"])
        self.assertEqual(dashboards[0]["name"], "ValidAIte QA Status")

    def test_dashboard_ingests_as_document_and_fact(self):
        cfg = {**self.CFG, "dashboards": ["10201"]}
        items, _ = JiraLiveConnector("qualizeal", cfg, transport=_jira_transport).pull(None)
        self.assertTrue(any(i.uri == "jira://dashboard/10201" for i in items))
        rec = next(i for i in items if i.uri == "jira://dashboard/10201")
        self.assertIn("Sprint Health", rec.bytes_.decode())
        facts = factsmod.load_facts()["jira_dashboards"]["10201"]
        self.assertEqual(facts["name"], "ValidAIte QA Status")
        self.assertIn("Open Defects", [g["title"] for g in facts["gadgets"]])

    def test_board_ingests_its_issues_and_fact(self):
        cfg = {**self.CFG, "boards": ["34"]}
        items, _ = JiraLiveConnector("qualizeal", cfg, transport=_jira_transport).pull(None)
        self.assertTrue(any(i.uri == "jira://PLAT/PLAT-1" for i in items))
        board = factsmod.load_facts()["jira_boards"]["34"]
        self.assertEqual(board["name"], "Platform")
        self.assertEqual(board["issues"], 1)

    def test_no_allowlist_raises(self):
        from knowledge_fabric.connectors.jira_live import ConnectorConfigError

        with self.assertRaises(ConnectorConfigError):
            JiraLiveConnector("qualizeal", dict(self.CFG), transport=_jira_transport).pull(None)


CONF_SITE = "https://aicoe-genq.atlassian.net"


def _conf_transport(url, headers, timeout=60):
    path = urllib.parse.urlparse(url).path

    def ok(payload):
        return 200, json.dumps(payload).encode()

    if path == "/wiki/api/v2/pages/1703938":
        return ok({
            "id": "1703938", "title": "Project Plan", "spaceId": "555",
            "body": {"storage": {"value": "<p>Kickoff on 2026-10-01. Lead: Prashobh.</p>"}},
            "version": {"number": 3, "createdAt": "2026-09-01T00:00:00Z"},
            "_links": {"webui": "/spaces/COE/pages/1703938/Project+Plan"},
        })
    if path == "/wiki/api/v2/spaces/555":
        return ok({"id": "555", "key": "COE", "name": "AI CoE"})
    if path == "/wiki/api/v2/pages/1703938/attachments":
        return ok({"results": []})
    return 404, b'{"error":"not found"}'


class TestConfluencePage(unittest.TestCase):
    CFG = {"url": CONF_SITE, "email": "coe@qualizeal.com", "token": "secret", "attachments": False}

    def test_pull_page_returns_storage_body(self):
        conn = ConfluenceConnector("qualizeal", dict(self.CFG), transport=_conf_transport)
        page = conn.pull_page("1703938")
        self.assertEqual(page["title"], "Project Plan")
        self.assertIn("Kickoff", page["body"]["storage"]["value"])

    def test_page_ingests_directly_without_a_space_allowlist(self):
        cfg = {**self.CFG, "pages": ["1703938"]}
        items, _ = ConfluenceConnector("qualizeal", cfg, transport=_conf_transport).pull(None)
        self.assertEqual(len(items), 1)
        rec = items[0]
        self.assertEqual(rec.uri, "confluence://COE/1703938")
        self.assertIn("Kickoff", rec.bytes_.decode())
        self.assertEqual(rec.meta["citation_url"],
                         f"{CONF_SITE}/wiki/spaces/COE/pages/1703938/Project+Plan")

    def test_no_allowlist_raises(self):
        from knowledge_fabric.connectors.confluence import ConnectorConfigError

        with self.assertRaises(ConnectorConfigError):
            ConfluenceConnector("qualizeal", dict(self.CFG), transport=_conf_transport).pull(None)


if __name__ == "__main__":
    unittest.main()
