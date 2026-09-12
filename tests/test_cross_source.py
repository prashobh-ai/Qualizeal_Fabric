"""T99 — cross-source verification: Jira status vs the repository, offline.

We ingest two Jira issues (both marked Done) via a fake Jira transport and one
commit document that mentions one of them, rebuild the relationships graph, and
assert:

* the scan links the commit to the issue key it mentions (a code→issue edge);
* verifying the mentioned issue reports **agreement** citing BOTH sources;
* verifying the un-referenced issue reports the **specific discrepancy** (marked
  Done, but no code references it);
* the governed answer path routes a "verify … against the repo" question to the
  cross-source answer and returns the two-source result end to end;
* an unknown key is reported as insufficient, never asserted beyond the evidence.
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
from knowledge_fabric.connectors.jira_live import JiraLiveConnector  # noqa: E402
from knowledge_fabric.contracts.types import RawItem, now_ms  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402
from tests.util import T, seeded  # noqa: E402

SITE = "https://qualizeal-team-aicoe.atlassian.net"


def _issue(key, status):
    return {
        "key": key,
        "id": key.split("-")[1],
        "fields": {
            "summary": f"{key} work item",
            "status": {"name": status},
            "issuetype": {"name": "Task"},
            "priority": {"name": "High"},
            "assignee": {"displayName": "Alice"},
            "project": {"key": "V1"},
            "updated": "2026-09-12T08:00:00.000+0000",
            "created": "2026-09-01T08:00:00.000+0000",
        },
    }


ISSUES = [_issue("V1-42", "Done"), _issue("V1-99", "Done")]


def jira_transport(url, headers, timeout=30):
    path = urllib.parse.urlparse(url).path

    def ok(payload):
        return 200, json.dumps(payload).encode()

    if path == "/rest/api/3/field":
        return ok([])
    if path in ("/rest/api/3/search/jql", "/rest/api/3/search"):
        return ok({"issues": ISSUES, "isLast": True, "total": len(ISSUES)})
    return 404, b'{"error":"not found"}'


def _commit_item(text: str):
    return RawItem(
        tenant=T,
        source="github_live",
        source_version="1",
        uri="github://acme/app/commits/abc123def456",
        mime="text/markdown",
        title="Fix retry logic",
        bytes_=text.encode("utf-8"),
        meta={
            "acl": ["public"],
            "source_kind": "github",
            "citation_url": "https://github.com/acme/app/commit/abc123def456",
            "arrived_at": now_ms(),
            "as_of": "2026-09-12T08:00:00Z",
        },
    )


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-xsrc-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


CFG = {"url": SITE, "email": "coe@qualizeal.com", "token": "secret", "projects": ["V1"]}


def _ingest(p, items):
    intake = Intake(p)
    worker = IngestWorker(p, intake)
    for it in items:
        it.meta.setdefault("ontology", "quality-assurance")
        intake.submit(it)
    return worker.drain()


class Base(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        # two Jira issues (both Done): pull writes facts + returns issue items,
        # which we ingest so each issue is resolvable and a known key.
        self._ingest_jira()
        # one commit that references V1-42 only
        _ingest(self.p, [_commit_item("Fix retry logic\n\nImplements the backoff from V1-42.")])
        self.rel = relmod.scan(self.p, T)
        self.principal = demo.principal_for(self.p, T, "developer")

    def _ingest_jira(self):
        # pull() writes facts + returns items; ingest the issue documents too so
        # the issue is resolvable and countable as a known key.
        conn = JiraLiveConnector(T, dict(CFG), transport=jira_transport)
        items, _ = conn.pull(None)
        _ingest(self.p, items)


class TestScan(Base):
    def test_commit_is_linked_to_the_issue_it_mentions(self):
        self.assertIn("V1-42", self.rel["issue_keys"])
        edges = self.p.relationships.mentions_of(T, "issue", "V1-42", subject_kinds=["commit"])
        self.assertTrue(edges, "the commit mentioning V1-42 should be linked to it")
        self.assertEqual(edges[0]["subject_kind"], "commit")
        self.assertIn("acme/app", edges[0]["subject_id"])
        # V1-99 is mentioned by no code
        self.assertFalse(
            self.p.relationships.mentions_of(T, "issue", "V1-99", subject_kinds=["commit"])
        )


class TestVerify(Base):
    def test_agreement_cites_both_sources(self):
        res = cross_source.verify(
            self.p, self.principal, "the Jira task V1-42 shows complete — check the repo and verify"
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.verdict, "agree")
        self.assertIn("V1-42", res.text)
        # a citation from Jira and one from the repo
        srcs = {(c.coordinate.locator or {}).get("source") for c in res.citations}
        self.assertIn("jira", srcs)
        self.assertIn("repo", srcs)

    def test_discrepancy_is_specific(self):
        res = cross_source.verify(
            self.p, self.principal, "V1-99 is marked done — cross-check the repository"
        )
        self.assertEqual(res.verdict, "discrepancy")
        self.assertIn("no commit or pull request referencing V1-99", res.text)

    def test_unknown_key_is_insufficient_not_asserted(self):
        res = cross_source.verify(self.p, self.principal, "verify V1-777 against the repo")
        self.assertEqual(res.verdict, "insufficient")
        self.assertIn("could not find", res.text.lower())


class TestRouting(Base):
    def test_governed_answer_routes_to_cross_source(self):
        svc = AnswerService(self.p)
        ans = svc.ask(
            self.principal, "the Jira task V1-42 shows complete, check the repo and verify"
        )
        self.assertEqual(ans.kind.value, "answer")
        self.assertEqual((ans.why or {}).get("verdict"), "agree")
        self.assertIn("V1-42", ans.answer_text)
        self.assertTrue(ans.citations)


if __name__ == "__main__":
    unittest.main()
