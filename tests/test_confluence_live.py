"""T98 — Confluence configured and answered, all offline.

A fake Confluence transport serves the CoE space and its pages. We assert:

* ``confluence`` ingests each page as a document with paragraph passages and
  writes ``facts.json["confluence_spaces"][KEY] = {pages, last_updated, as_of}``;
* "how many pages in space <X>" answers **from facts**, exact, with freshness;
* a page's paragraph citation **expands to the paragraph in context** — the T93
  ``passages.context`` machinery returns the cited passage plus its neighbours,
  applied here to a Confluence page;
* ``live_cql`` serves the agent's ad-hoc current-questions search;
* :mod:`connectors.provisioning` configures the spaces reproducibly.
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
from knowledge_fabric.connectors.confluence import ConfluenceConnector  # noqa: E402
from knowledge_fabric.ingestion.sync import SyncManager  # noqa: E402
from tests.util import T, seeded  # noqa: E402

SITE = "https://qualizeal-team-aicoe.atlassian.net"
SPACE_KEY = "AICOE"
SPACE_ID = "9001"

PAGES = [
    {
        "id": "111",
        "title": "QMentisAI Onboarding",
        "body": {
            "storage": {
                "value": (
                    "<p>QMentisAI is the CoE testing platform for AI systems.</p>"
                    "<p>New engineers request access through the AICOE portal on day one.</p>"
                    "<p>Onboarding completes once the first evaluation run is green.</p>"
                )
            }
        },
        "version": {"number": 3, "createdAt": "2026-09-10T12:00:00.000Z", "authorId": "u1"},
        "_links": {"webui": "/spaces/AICOE/pages/111/QMentisAI-Onboarding"},
    },
    {
        "id": "112",
        "title": "ValidAIte Release Checklist",
        "body": {"storage": {"value": "<p>Every ValidAIte release passes the SLA gate.</p>"}},
        "version": {"number": 1, "createdAt": "2026-09-09T09:00:00.000Z", "authorId": "u2"},
        "_links": {"webui": "/spaces/AICOE/pages/112/ValidAIte-Release-Checklist"},
    },
]

SEARCH = {
    "results": [
        {
            "title": "QMentisAI Onboarding",
            "excerpt": "New engineers request access through the AICOE portal",
            "lastModified": "2026-09-10T12:00:00.000Z",
            "content": {
                "id": "111",
                "type": "page",
                "_links": {"webui": "/spaces/AICOE/pages/111"},
            },
        }
    ]
}


def fake_transport(url, headers, timeout=30):
    parsed = urllib.parse.urlparse(url)
    path = parsed.path

    def ok(payload):
        return 200, json.dumps(payload).encode()

    if path == "/wiki/api/v2/spaces":
        return ok({"results": [{"id": SPACE_ID, "key": SPACE_KEY, "name": "AI CoE"}], "_links": {}})
    if path == "/wiki/api/v2/pages":
        return ok({"results": PAGES, "_links": {}})
    if path.startswith("/wiki/api/v2/pages/") and path.endswith("/attachments"):
        return ok({"results": [], "_links": {}})
    if path == "/wiki/rest/api/search":
        return ok(SEARCH)
    return 404, b'{"error":"not found"}'


CFG = {
    "url": SITE + "/wiki",
    "email": "coe@qualizeal.com",
    "token": "secret",
    "spaces": [SPACE_KEY],
    "attachments": False,
}


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-confluence-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


class Base(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.res = SyncManager(self.p).sync(T, "confluence", dict(CFG), transport=fake_transport)


class TestConfluenceFacts(Base):
    def test_pages_ingested_and_facts_written(self):
        self.assertGreaterEqual(self.res["ingested"], len(PAGES))
        block = factsmod.load_facts()["confluence_spaces"][SPACE_KEY]
        self.assertEqual(block["pages"], len(PAGES))
        self.assertTrue(block["last_updated"])
        self.assertTrue(block["as_of"])

    def test_page_count_answers_from_facts(self):
        from knowledge_fabric.tenants import demo

        principal = demo.principal_for(self.p, T, "developer")
        res = aggregate.analyse(self.p, principal, f"how many pages are in space {SPACE_KEY}")
        self.assertIsNotNone(res)
        self.assertEqual(res.pattern, "confluence")
        self.assertIn(f"{len(PAGES)} pages", res.text)
        self.assertIn("as of", res.text)


class TestPageCitationExpands(Base):
    def _qmentis_passages(self):
        return [
            p
            for p in self.p.passages.for_tenant(T)
            if "qmentisai" in (p.text or "").lower() or "aicoe portal" in (p.text or "").lower()
        ]

    def test_a_page_paragraph_expands_to_its_neighbours(self):
        pas = self._qmentis_passages()
        self.assertTrue(pas, "the Confluence page should ingest as paragraph passages")
        # the middle paragraph of the 3-paragraph page: expanding it returns the
        # sibling paragraphs before and after (the T93 citation-expand path).
        middle = next((p for p in pas if "aicoe portal" in (p.text or "").lower()), pas[0])
        ctx = self.p.passages.context(T, middle.id, radius=1)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["passage"].id, middle.id)
        self.assertTrue(ctx["before"] or ctx["after"])  # a real paragraph neighbourhood

    def test_live_cql_serves_current_questions(self):
        c = ConfluenceConnector(T, dict(CFG), transport=fake_transport)
        hits = c.live_cql("space = AICOE AND text ~ 'onboarding'")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], "111")
        self.assertIn("AICOE", hits[0]["url"])


class TestProvisioning(unittest.TestCase):
    def test_configure_spaces_is_reproducible(self):
        p = seeded([T], model_mode="extractive")
        from knowledge_fabric.connectors import admin

        provisioning.configure_confluence(p, T, ["AICOE", "ENG"])
        cfg = admin.effective_config(p, T, "confluence", {})
        self.assertEqual(cfg["spaces"], ["AICOE", "ENG"])
        self.assertTrue(cfg["url"].endswith("/wiki"))


if __name__ == "__main__":
    unittest.main()
