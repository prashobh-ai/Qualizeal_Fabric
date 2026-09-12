"""T97 — the V1 Platform Jira board, configured and answered, all offline.

A fake Atlassian transport serves the Agile board configuration (columns as
named groups of statuses), the active sprint with its dates, the workflow status
list, and a JQL search over a small V1 issue set. We assert:

* ``jira_live`` writes the board into ``facts.json`` — ``board.columns`` (each a
  named group of statuses) and a dated ``sprint`` — alongside the by_status
  census;
* the aggregate answer path returns the **exact** in-progress count *from the
  board's own column definition* (the sum over the column's statuses, not a
  single status literal), stamped with freshness;
* :mod:`connectors.provisioning` configures the board reproducibly and reports
  readiness without leaking secrets.
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
from knowledge_fabric.answer import aggregate  # noqa: E402
from knowledge_fabric.connectors import provisioning  # noqa: E402
from knowledge_fabric.connectors.jira_live import JiraLiveConnector  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402
from tests.util import T, seeded  # noqa: E402

SITE = "https://qualizeal-team-aicoe.atlassian.net"
BOARD = 34

# id → name for the workflow statuses
STATUSES = [
    {"id": "1", "name": "To Do"},
    {"id": "2", "name": "In Progress"},
    {"id": "3", "name": "In Review"},
    {"id": "4", "name": "Done"},
]
# the board's columns reference statuses by id; "In Progress" groups two statuses
COLUMNS = [
    {"name": "To Do", "statuses": [{"id": "1"}]},
    {"name": "In Progress", "statuses": [{"id": "2"}, {"id": "3"}]},
    {"name": "Done", "statuses": [{"id": "4"}]},
]
SPRINT = {
    "values": [
        {
            "name": "V1 Sprint 7",
            "state": "active",
            "startDate": "2026-09-08T09:00:00.000Z",
            "endDate": "2026-09-22T09:00:00.000Z",
        }
    ]
}


def _issue(key, status, itype="Task", priority="Medium", assignee="Alice"):
    return {
        "key": key,
        "id": key.split("-")[1],
        "fields": {
            "summary": f"{key} summary",
            "status": {"name": status},
            "issuetype": {"name": itype},
            "priority": {"name": priority},
            "assignee": {"displayName": assignee},
            "project": {"key": "V1"},
            "updated": "2026-09-12T08:00:00.000+0000",
            "created": "2026-09-01T08:00:00.000+0000",
        },
    }


# 2 To Do, 3 In Progress, 1 In Review, 4 Done  → "In Progress" column = 3 + 1 = 4
ISSUES = (
    [_issue(f"V1-{i}", "To Do") for i in (1, 2)]
    + [_issue(f"V1-{i}", "In Progress") for i in (3, 4, 5)]
    + [_issue("V1-6", "In Review")]
    + [_issue(f"V1-{i}", "Done") for i in (7, 8, 9, 10)]
)


def fake_transport(url, headers, timeout=30):
    """Route by path; ``(status, bytes)`` exactly like ``http_transport``."""
    parsed = urllib.parse.urlparse(url)
    path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

    def ok(payload):
        return 200, json.dumps(payload).encode()

    if path == "/rest/api/3/status":
        return ok(STATUSES)
    if path == "/rest/api/3/field":
        return ok([])  # no custom sprint field → sprint_field() resolves to None
    if path == f"/rest/agile/1.0/board/{BOARD}/configuration":
        return ok({"id": BOARD, "name": "V1 Platform", "columnConfig": {"columns": COLUMNS}})
    if path == f"/rest/agile/1.0/board/{BOARD}/sprint":
        return ok(SPRINT if query.get("state", [""])[0] == "active" else {"values": []})
    if path in ("/rest/api/3/search/jql", "/rest/api/3/search"):
        return ok({"issues": ISSUES, "isLast": True, "total": len(ISSUES)})
    return 404, b'{"error":"not found"}'


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-jira-board-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


CFG = {
    "url": SITE,
    "email": "coe@qualizeal.com",
    "token": "secret",
    "projects": ["V1"],
    "board_id": BOARD,
}


class TestBoardFacts(unittest.TestCase):
    def _pull(self):
        JiraLiveConnector("qualizeal", dict(CFG), transport=fake_transport).pull(None)
        return factsmod.load_facts()["jira_projects"]["V1"]

    def test_board_columns_and_sprint_dates_land_in_facts(self):
        block = self._pull()
        issues = block["issues"]
        self.assertEqual(issues["total"], len(ISSUES))
        self.assertEqual(issues["by_status"]["In Progress"], 3)
        self.assertEqual(issues["by_status"]["In Review"], 1)
        # board columns, each a named group of statuses
        cols = {c["name"]: c["statuses"] for c in block["board"]["columns"]}
        self.assertEqual(block["board"]["id"], BOARD)
        self.assertEqual(cols["In Progress"], ["In Progress", "In Review"])
        # dated active sprint from the Agile API (not the issue guess)
        self.assertEqual(block["sprint"]["name"], "V1 Sprint 7")
        self.assertEqual(block["sprint"]["state"], "active")
        self.assertTrue(block["sprint"]["start"] and block["sprint"]["end"])

    def test_no_board_id_no_board_fact(self):
        cfg = {k: v for k, v in CFG.items() if k != "board_id"}
        JiraLiveConnector("qualizeal", cfg, transport=fake_transport).pull(None)
        block = factsmod.load_facts()["jira_projects"]["V1"]
        self.assertNotIn("board", block)


class TestInProgressAnswer(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        JiraLiveConnector(T, dict(CFG), transport=fake_transport).pull(None)
        self.principal = demo.principal_for(self.p, T, "developer")

    def test_in_progress_count_is_exact_from_the_board_column(self):
        res = aggregate.analyse(
            self.p, self.principal, "how many tasks are in progress on the V1 board"
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.pattern, "jira")
        # 3 In Progress + 1 In Review = 4, the board's own column definition
        self.assertIn("4 issues in the In Progress column", res.text)
        self.assertIn("as of", res.text)  # freshness stamp
        self.assertTrue(res.citations)

    def test_to_do_and_done_columns_answer_too(self):
        todo = aggregate.analyse(
            self.p, self.principal, "how many V1 issues are in the To Do column"
        )
        self.assertIn("2 issues in the To Do column", todo.text)
        done = aggregate.analyse(self.p, self.principal, "how many tasks are Done on the V1 board")
        self.assertIn("4 issues in the Done column", done.text)


class TestProvisioning(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")

    def test_configure_v1_board_is_reproducible_and_auditable(self):
        from knowledge_fabric.connectors import admin

        provisioning.configure_jira_v1(self.p, T)
        cfg = admin.effective_config(self.p, T, "jira", {})  # T115: canonical key
        self.assertEqual(cfg["projects"], ["V1"])
        self.assertEqual(cfg["board_id"], 34)
        self.assertEqual(cfg["interval"], "15m")
        self.assertEqual(cfg["url"], SITE)

    def test_readiness_reports_missing_secrets_without_leaking_them(self):
        provisioning.configure_jira_v1(self.p, T)
        for var in ("JIRA_URL", "JIRA_EMAIL", "JIRA_TOKEN"):
            os.environ.pop(var, None)
        r = provisioning.jira_ready(self.p, T)
        self.assertTrue(r["configured"])
        self.assertFalse(r["ready"])
        self.assertEqual(set(r["missing_secrets"]), {"JIRA_URL", "JIRA_EMAIL", "JIRA_TOKEN"})
        # readiness names which secrets are missing but never carries their values
        self.assertNotIn("token", {k.lower() for k in r})


if __name__ == "__main__":
    unittest.main()
