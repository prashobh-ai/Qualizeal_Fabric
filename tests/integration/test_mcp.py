"""T31 — the Knowledge Fabric MCP server.

The server exposes the ONE governed answer path over the Model Context Protocol.
These tests build it over an in-memory fabric and drive the tools the way an MCP
client would: list the tools, then call them. They skip cleanly when the
optional ``mcp`` extra is not installed (the CI ``mcp`` job installs it).

Async SDK calls are driven with ``asyncio.run`` inside sync tests, so the suite
needs no async pytest plugin.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest

os.environ.setdefault("KF_MODEL_MODE", "off")

mcp = None
try:  # the mcp extra is optional (guarded, like the server itself)
    import mcp as _mcp  # noqa: F401

    mcp = _mcp
except Exception:  # pragma: no cover - exercised only without the extra
    mcp = None

from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from tests.util import T, seeded  # noqa: E402

_QMENTIS = (
    "# QMentisAI\n\nQMentisAI is an AI test-intelligence platform. "
    "QMentisAI pricing is usage-based.\n"
)
_AUTH = '''"""Auth helpers for single sign-on."""


def mint_session_token(subject, roles, scopes):
    """Mint a signed session token for single sign-on."""
    return {"subject": subject}
'''


def _text(result) -> str:
    """The text payload of a CallToolResult, across SDK shapes."""
    content = getattr(result, "content", result)
    first = content[0]
    return getattr(first, "text", str(first))


@unittest.skipIf(mcp is None, "mcp extra not installed")
class TestMcpServer(unittest.TestCase):
    def setUp(self):
        from knowledge_fabric.mcp import build_server

        self.p = seeded([T])
        intake, worker = Intake(self.p), IngestWorker(self.p, None)
        worker.intake = intake
        intake.submit(
            intake.canonical(
                T,
                "internal",
                "internal://q/qm.md",
                "QMentisAI",
                _QMENTIS.encode(),
                mime="text/markdown",
                acl=["public"],
            )
        )
        intake.submit(
            intake.canonical(
                T,
                "github",
                "github://acme/app/auth.py",
                "auth.py",
                _AUTH.encode(),
                mime="text/x-python;code",
                acl=["public"],
            )
        )
        worker.drain()
        self.server = build_server(platform=self.p, tenant=T)

    def _call(self, name, args):
        return asyncio.run(self.server.call_tool(name, args))

    def test_lists_the_governed_tools(self):
        tools = asyncio.run(self.server.list_tools())
        names = {t.name for t in tools}
        # T31 core + the T48 fabric tools; every one is governed (ACL + budget).
        self.assertEqual(
            names,
            {
                "ask",
                "discover",
                "corpus",
                "query_facts",
                "get_repository",
                "list_capabilities",
                "get_dependencies",
                "search_code",
                "get_pull_requests",
                "get_commits",
                "explain_architecture",
                "run_table_query",
                "jira_search",
                "confluence_search",
                "describe_image",
                "ask_fabric",
                "provider_status",
                "fabric_communities",
                "knowledge_gaps",
                "list_known_questions",
            },
        )
        # every tool carries a description and an input schema (MCP conformance);
        # the schema attribute is inputSchema on the wire, input_schema in the
        # 2.x Python objects — accept either.
        for t in tools:
            self.assertTrue(t.description)
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
            self.assertTrue(schema)

    def test_ask_returns_a_grounded_cited_answer(self):
        a = json.loads(_text(self._call("ask", {"question": "what is QMentisAI"})))
        self.assertEqual(a["kind"], "answer")
        self.assertTrue(a["citations"])
        self.assertEqual(a["citations"][0]["document_title"], "QMentisAI")
        self.assertGreater(a["grounding_score"], 0.0)

    def test_ask_declines_out_of_corpus(self):
        a = json.loads(_text(self._call("ask", {"question": "what is the capital of France"})))
        self.assertIn(a["kind"], ("gap", "clarify"))

    def test_ask_designation_conditions_framing(self):
        # T27 rides through MCP: a CTO's answer is framed as an executive headline.
        a = json.loads(
            _text(self._call("ask", {"question": "what is QMentisAI", "designation": "CTO"}))
        )
        self.assertEqual(a["role_view"]["lens"], "executive")

    def test_discover_returns_ranked_assets(self):
        d = json.loads(_text(self._call("discover", {"query": "sso auth code i can reuse"})))
        self.assertIn("indexed", d["searched"])
        self.assertTrue(d["hits"])
        self.assertIn("kind", d["hits"][0])

    def test_corpus_reports_counts(self):
        c = json.loads(_text(self._call("corpus", {})))
        for key in ("documents", "passages", "entities", "relationships", "domains"):
            self.assertIn(key, c)
        self.assertGreater(c["documents"], 0)

    def test_entrypoint_is_importable(self):
        from knowledge_fabric.mcp.__main__ import main

        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
