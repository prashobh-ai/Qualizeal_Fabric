"""T48 — the MCP tools over the fabric-data files.

The tool FUNCTIONS are unit-tested directly (no ``mcp`` package needed); the
server-construction tests are skipped when the optional extra is absent. The
key invariant: ``query_facts`` returns the same numbers the Curator
Repositories panel shows — both read the same fixture through the same view.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

mcp = None
try:  # the mcp extra is optional (guarded, like the server itself)
    import mcp as _mcp  # noqa: F401

    mcp = _mcp
except Exception:  # pragma: no cover - exercised only without the extra
    mcp = None

from knowledge_fabric import fabric_views  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.mcp import server as mcp_server  # noqa: E402
from tests.fixtures import fabric_data_fixture as fx  # noqa: E402
from tests.util import T, seeded  # noqa: E402


def _has_answer_tools(name: str) -> bool:
    try:
        from knowledge_fabric.answer import tools  # noqa: F401
    except Exception:
        return False
    return callable(getattr(tools, name, None))


class _Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="kf-t48-")
        fx.write_fabric(cls.tmp)
        cls.prev_env = fx.point_env(cls.tmp)
        cls.p = seeded([T])
        intake, worker = Intake(cls.p), IngestWorker(cls.p, None)
        worker.intake = intake
        intake.submit(
            intake.canonical(
                T,
                "github",
                f"github://{fx.REPO_A}/pulls/57",
                "PR #57",
                b"Pull request #57 (merged) hardens the API ledger.",
                mime="text/markdown",
                acl=["public"],
            )
        )
        worker.drain()

    @classmethod
    def tearDownClass(cls):
        fx.restore_env(cls.prev_env)
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestFactsTools(_Fixture):
    def test_query_facts_matches_the_curator_repositories_ui(self):
        ui_rows = fabric_views.repositories()  # what GET /curator/repositories serves
        out = mcp_server.tool_query_facts("how many commits and merged PRs does fabric-core have")
        self.assertIn("result", out)
        self.assertIn("citations", out)
        self.assertEqual(out["result"]["repositories"], ui_rows)
        self.assertEqual(out["result"]["focus"]["repos"], [fx.REPO_A])
        self.assertIn("commits", out["result"]["focus"]["metrics"])
        self.assertIn("prs_merged", out["result"]["focus"]["metrics"])
        ans = out["result"]["answer"]
        self.assertEqual(len(ans), 1)
        self.assertEqual(ans[0]["commits"], ui_rows[0]["commits"])
        self.assertEqual(ans[0]["prs_merged"], ui_rows[0]["prs_merged"])
        self.assertTrue(all(c["exists"] for c in out["citations"]))

    def test_query_facts_without_a_repo_covers_every_repo(self):
        out = mcp_server.tool_query_facts("how many contributors across our repositories")
        self.assertEqual(
            [a["repo"] for a in out["result"]["answer"]],
            [r["repo"] for r in fabric_views.repositories()],
        )
        self.assertEqual(out["result"]["jira_projects"]["REL"]["issues"], 120)
        self.assertEqual(out["result"]["confluence_spaces"]["QE"]["pages"], 34)

    def test_get_repository(self):
        out = mcp_server.tool_get_repository(fx.REPO_A, self.p, T)
        self.assertEqual(out["result"]["facts"]["commits"], 412)
        self.assertEqual(len(out["result"]["recent_prs"]), 1)
        self.assertTrue(any(c["path"].endswith("architecture.md") for c in out["citations"]))
        miss = mcp_server.tool_get_repository("nobody/nothing")
        self.assertIn("error", miss)
        self.assertEqual(miss["known_repositories"], [fx.REPO_A, fx.REPO_B])

    def test_list_capabilities_and_dependencies(self):
        out = mcp_server.tool_list_capabilities()
        self.assertEqual(
            set(out["result"]["capabilities"]),
            {"rag", "caching", "enterprise_readiness", "dashboard_ui"},
        )
        out = mcp_server.tool_list_capabilities("rag")
        self.assertEqual(list(out["result"]["capabilities"]), ["rag"])
        deps = mcp_server.tool_get_dependencies(fx.REPO_A)
        self.assertEqual(deps["result"]["count"], 2)
        self.assertEqual(deps["result"]["by_licence"], {"MIT": 1, "BSD-3-Clause": 1})
        self.assertIn("error", mcp_server.tool_get_dependencies("nobody/nothing"))

    def test_pull_requests_and_commits(self):
        prs = mcp_server.tool_get_pull_requests(self.p, T, fx.REPO_A, limit=5)
        self.assertEqual(len(prs["result"]["pull_requests"]), 1)
        self.assertEqual(prs["result"]["counts"]["merged"], 57)
        merged = mcp_server.tool_get_pull_requests(self.p, T, fx.REPO_A, state="merged")
        self.assertEqual(len(merged["result"]["pull_requests"]), 1)
        opened = mcp_server.tool_get_pull_requests(self.p, T, fx.REPO_A, state="open")
        self.assertEqual(opened["result"]["pull_requests"], [])
        commits = mcp_server.tool_get_commits(self.p, T, fx.REPO_A)
        self.assertEqual(commits["result"]["commits"], [])
        self.assertEqual(commits["result"]["counts"], {"total": 412})
        self.assertIn("error", mcp_server.tool_get_commits(self.p, T, "nobody/nothing"))

    def test_explain_architecture_and_describe_image(self):
        out = mcp_server.tool_explain_architecture(fx.REPO_A)
        self.assertIn("# Architecture", out["result"]["architecture_md"])
        self.assertEqual(
            out["result"]["summary"]["reuse_candidates"][0]["symbol"], "AnswerService.ask"
        )
        self.assertEqual(
            {c["capability"] for c in out["result"]["capabilities"]},
            {"rag", "caching", "enterprise_readiness"},
        )
        b = mcp_server.tool_explain_architecture(fx.REPO_B)
        self.assertEqual(b["result"]["architecture_md"], "")
        self.assertIn("not generated", b["result"]["note"])
        self.assertIn("error", mcp_server.tool_explain_architecture("nobody/nothing"))
        img = mcp_server.tool_describe_image(fx.DOC_ID, "chart1")
        self.assertEqual(img["result"]["description"], "chart1 of the budget")
        self.assertIn("error", mcp_server.tool_describe_image(fx.DOC_ID, "nope"))


class TestToolApiBackedTools(_Fixture):
    """search_code / run_table_query / jira_search / confluence_search call the
    T42/T43 tool API when present and say 'tool unavailable' otherwise."""

    def _check(self, name, out):
        if _has_answer_tools(name):
            self.assertTrue("result" in out or "error" in out)
        else:
            self.assertIn("error", out)
            self.assertTrue(out["error"].startswith("tool unavailable"), out["error"])
            self.assertIsNone(out["result"])

    def test_search_code(self):
        self._check(
            "search_code", mcp_server.tool_search_code(self.p, T, "answer service", repo=fx.REPO_A)
        )

    def test_run_table_query(self):
        self._check(
            "run_table_query",
            mcp_server.tool_run_table_query(fx.DOC_ID, fx.SHEET, "SELECT * FROM t"),
        )
        bad = mcp_server.tool_run_table_query(fx.DOC_ID, fx.SHEET, "DELETE FROM t")
        self.assertEqual(bad["error"], "only SELECT queries are allowed")

    def test_jira_and_confluence(self):
        self._check("jira_search", mcp_server.tool_jira_search("project = REL"))
        self._check("confluence_search", mcp_server.tool_confluence_search("space = QE"))


class TestAskFabric(_Fixture):
    def test_ask_fabric_runs_governed_path_with_context(self):
        out = mcp_server.tool_ask_fabric(
            self.p,
            T,
            "what is the acceptance criteria for coverage?",
            designation="CTO",
            previous_questions=["what is the release policy?", "who owns coverage?"],
        )
        self.assertIn("result", out)
        self.assertIn("citations", out)
        r = out["result"]
        self.assertIn(r["kind"], ("answer", "clarify", "gap"))
        self.assertIn(r["engine"], ("agent", "answer_service"))
        self.assertEqual(
            r["previous_questions"], ["what is the release policy?", "who owns coverage?"]
        )
        self.assertEqual(r["role_view"]["lens"], "executive")  # the designation framed it
        self.assertIsInstance(out["steps"], list)


@unittest.skipIf(mcp is None, "mcp extra not installed")
class TestServerRegistration(_Fixture):
    def setUp(self):
        self.server = mcp_server.build_server(platform=self.p, tenant=T)

    def _call(self, name, args):
        r = asyncio.run(self.server.call_tool(name, args))
        content = getattr(r, "content", r)
        return json.loads(getattr(content[0], "text", str(content[0])))

    def test_every_tool_is_registered_with_a_schema(self):
        tools = asyncio.run(self.server.list_tools())
        names = {t.name for t in tools}
        self.assertEqual(names, set(mcp_server.TOOL_NAMES))
        for t in tools:
            self.assertTrue(t.description)
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
            self.assertTrue(schema)

    def test_query_facts_over_the_wire_matches_the_ui(self):
        out = self._call("query_facts", {"question": "commits in test-harness"})
        self.assertEqual(out["result"]["answer"][0]["commits"], 33)
        self.assertEqual(out["result"]["repositories"], fabric_views.repositories())

    def test_unavailable_tool_is_honest_over_the_wire(self):
        out = self._call("jira_search", {"jql": "project = REL"})
        self.assertTrue("result" in out or "error" in out)
        if not _has_answer_tools("jira_search"):
            self.assertTrue(out["error"].startswith("tool unavailable"))

    def test_ask_fabric_over_the_wire(self):
        out = self._call(
            "ask_fabric",
            {
                "question": "what is the acceptance criteria for coverage?",
                "designation": "Developer",
                "previous_questions": ["what is the release policy?"],
            },
        )
        self.assertIn(out["result"]["kind"], ("answer", "clarify", "gap"))
        self.assertEqual(out["result"]["role_view"]["lens"], "builder")


if __name__ == "__main__":
    unittest.main()
