"""T101 — the v2 MCP tools: jira_status, corroborate, and ask_fabric's provider
label, all offline through the governed tool functions."""

from __future__ import annotations

import os
import tempfile
import unittest

import pytest

os.environ.setdefault("KF_MODEL_MODE", "extractive")

from knowledge_fabric import fabric_data as fd  # noqa: E402
from knowledge_fabric import relationships as relmod  # noqa: E402
from knowledge_fabric.contracts.types import RawItem  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.mcp import server  # noqa: E402
from tests.util import T, seeded  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_data_root():
    prev = os.environ.get("KF_DATA_ROOT")
    os.environ["KF_DATA_ROOT"] = tempfile.mkdtemp(prefix="kf-mcp-v2-")
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("KF_DATA_ROOT", None)
        else:
            os.environ["KF_DATA_ROOT"] = prev


def _ingest(p, items):
    intake = Intake(p)
    worker = IngestWorker(p, intake)
    for it in items:
        it.meta.setdefault("ontology", "quality-assurance")
        intake.submit(it)
    worker.drain()


class TestJiraStatus(unittest.TestCase):
    def test_jira_status_from_facts_with_in_progress(self):
        fd.write_json(
            fd.data_path("facts.json", mkdir=True),
            {
                "jira_projects": {
                    "V1": {
                        "issues": {
                            "total": 10,
                            "by_status": {"To Do": 2, "In Progress": 3, "In Review": 1, "Done": 4},
                        },
                        "board": {
                            "id": 34,
                            "columns": [
                                {"name": "In Progress", "statuses": ["In Progress", "In Review"]}
                            ],
                        },
                        "sprint": {"name": "S7", "state": "active"},
                        "as_of": "2026-09-12T08:00:00Z",
                    }
                }
            },
        )
        out = server.tool_jira_status("V1")
        self.assertTrue(out["result"]["found"])
        self.assertEqual(out["result"]["in_progress"], 4)  # 3 + 1, the board column
        self.assertEqual(out["result"]["total"], 10)
        self.assertTrue(out["citations"])

    def test_unknown_project_is_not_fabricated(self):
        fd.write_json(fd.data_path("facts.json", mkdir=True), {"jira_projects": {}})
        out = server.tool_jira_status("ZZZ")
        self.assertFalse(out["result"]["found"])


class TestCorroborateAndAskFabric(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        _ingest(
            self.p,
            [
                RawItem(
                    tenant=T,
                    source="jira_live",
                    source_version="1",
                    uri="jira://V1/V1-42",
                    mime="text/markdown",
                    title="V1-42 · retry logic",
                    bytes_=b"# V1-42\n\nAdd backoff to the retry path.",
                    meta={
                        "acl": ["public"],
                        "source_kind": "jira",
                        "citation_url": "https://example.atlassian.net/browse/V1-42",
                        "as_of": "2026-09-12T08:00:00Z",
                        "jira": {"status": "Done"},
                    },
                ),
                RawItem(
                    tenant=T,
                    source="github_live",
                    source_version="1",
                    uri="github://acme/app/commits/abc123def456",
                    mime="text/markdown",
                    title="Fix retry logic",
                    bytes_=b"Fix retry logic\n\nImplements the backoff from V1-42.",
                    meta={
                        "acl": ["public"],
                        "citation_url": "https://github.com/acme/app/commit/abc123def456",
                        "as_of": "2026-09-12T08:00:00Z",
                    },
                ),
            ],
        )
        relmod.scan(self.p, T)

    def test_corroborate_reports_agreement_with_both_sources(self):
        out = server.tool_corroborate(
            "the Jira task V1-42 shows complete, check the repo and verify", "", self.p, T
        )
        self.assertTrue(out["result"]["found"])
        self.assertEqual(out["result"]["verdict"], "agree")
        self.assertGreaterEqual(out["result"]["repo_references"], 1)
        self.assertTrue(out["citations"])

    def test_ask_fabric_names_the_provider(self):
        out = server.tool_ask_fabric(self.p, T, "what is the retry logic for V1-42")
        self.assertIn("provider", out["result"])
        # keyless run → the extractive core or the open-source label, never a
        # fabricated live model
        self.assertIn(out["result"]["provider"], ("Extractive", "Open-source LLM", "Claude"))


if __name__ == "__main__":
    unittest.main()
