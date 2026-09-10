"""Agent tool interface as an MCP server (Section 13.4).

Exposes the answer service (and a read-only curation view) as MCP tools over
JSON-RPC 2.0 on stdio. Agents authenticate with their OWN identity (a token
minted for a service principal) and are subject to the SAME gate, policy and
budget as humans — there is no privileged path (I7). Every agent call is
traced and audited exactly like a human call.

This is a minimal, dependency-free MCP implementation (initialize /
tools/list / tools/call) so it runs anywhere; swapping in the official MCP
SDK is a surface change only.
"""

from __future__ import annotations

import json
import sys

from ..answer.service import AnswerService
from ..app import Platform

PROTOCOL = "2024-11-05"

TOOLS = [
    {
        "name": "kf_ask",
        "description": "Ask the Knowledge Fabric a grounded, cited question. Returns an "
        "answer with citations, a clarifying question, or a declared gap.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "agent bearer token (own identity)"},
                "question": {"type": "string"},
            },
            "required": ["token", "question"],
        },
    },
    {
        "name": "kf_gaps",
        "description": "List the tenant's gap backlog (unanswered questions).",
        "inputSchema": {
            "type": "object",
            "properties": {"token": {"type": "string"}},
            "required": ["token"],
        },
    },
]


class MCPServer:
    def __init__(self, platform: Platform | None = None):
        self.p = platform or Platform(db_path="./data/kf.db")
        self.svc = AnswerService(self.p)

    def handle(self, req: dict) -> dict | None:
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            return _ok(
                rid,
                {
                    "protocolVersion": PROTOCOL,
                    "serverInfo": {"name": "knowledge-fabric", "version": "1.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return _ok(rid, {"tools": TOOLS})
        if method == "tools/call":
            params = req.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            try:
                prin = self.p.idp.authenticate({"token": args.get("token", "")})
            except PermissionError as e:
                return _ok(
                    rid,
                    {"content": [{"type": "text", "text": f"auth error: {e}"}], "isError": True},
                )
            if name == "kf_ask":
                ans = self.svc.ask(prin, args.get("question", ""))
                return _ok(rid, {"content": [{"type": "text", "text": json.dumps(ans.to_dict())}]})
            if name == "kf_gaps":
                return _ok(
                    rid,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(self.p.curation.list(prin.tenant, "gap")),
                            }
                        ]
                    },
                )
            return _err(rid, -32601, f"unknown tool {name}")
        return _err(rid, -32601, f"unknown method {method}")

    def run_stdio(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self.handle(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, msg):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


if __name__ == "__main__":
    MCPServer().run_stdio()
