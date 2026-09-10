"""The QualiZeal Knowledge Fabric as an MCP server (T31, Section 12 / I7).

Exposes the ONE governed answer path — the same `AnswerService` the HTTP
surfaces and the showcase use — over the Model Context Protocol, so an agent or
an IDE can ask the fabric a question, discover reusable assets, or read the
corpus size through a standard tool interface. Every tool runs through the same
permission-before-ranking, grounding gate and citation post-check as a human
ask, so an MCP caller gets the same grounded, cited answers and the same honest
declines — never an ungoverned back door (I7).

The ``mcp`` package is an OPTIONAL runtime dependency (the ``mcp`` extra): the
import is guarded inside ``build_server`` so the shipped process never
hard-depends on it, and a caller without the extra gets a clear error instead of
an import crash at module load (matches the licence gate's optional-runtime
rule).
"""

from __future__ import annotations

import os

from ..answer.search import discover as discover_assets
from ..answer.service import AnswerService
from ..contracts.types import Principal

SERVER_NAME = "qualizeal-knowledge-fabric"
INSTRUCTIONS = (
    "The QualiZeal Knowledge Fabric. Ask grounded, cited questions of the "
    "organisation's knowledge (products, code, policies, learning material), "
    "discover reusable assets across the fabric, or read how large the corpus "
    "is. Every answer is grounded and cited; when the evidence is weak the "
    "fabric declines or asks back rather than guessing."
)


def _agent_principal(platform, tenant: str, designation: str = "") -> Principal:
    """Mint the machine principal MCP calls run as: an agent with public scope
    (least privilege). The optional ``designation`` conditions answer framing
    (T27) without ever widening what is retrievable."""
    token = platform.idp.mint(
        Principal(
            subject="mcp-agent",
            tenant=tenant,
            roles=["agent"],
            scopes=["public"],
            agent=True,
            designation=designation,
        )
    )
    return platform.idp.authenticate({"token": token})


def build_server(platform=None, tenant: str | None = None):
    """Build the MCP server, binding its tools to ``platform`` (an in-memory
    fabric for tests, or the configured store in production). ``mcp`` is imported
    here, not at module top, so importing this module never hard-depends on the
    optional extra."""
    from mcp.server.mcpserver import MCPServer  # optional-runtime (mcp extra), guarded

    tenant = tenant or os.environ.get("KF_TENANT", "qualizeal")
    if platform is None:
        from ..app import Platform

        platform = Platform(db_path=os.environ.get("KF_DB", "./data/kf.db"))
    svc = AnswerService(platform)
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)

    @server.tool(
        description=(
            "Ask the QualiZeal Knowledge Fabric a question and get a grounded, "
            "cited answer. Returns the answer text with inline [n] citation "
            "markers, the citations (each resolving to a document + coordinate), "
            "the grounding score, the model-selector level, and — when the "
            "evidence is weak — a clarify or gap instead of a guess."
        )
    )
    def ask(question: str, designation: str = "", k: int = 6) -> dict:
        """Ask a question.

        Args:
            question: The natural-language question.
            designation: Optional org designation (e.g. "Developer", "CTO") that
                conditions how the answer is framed and pitched (T27). It never
                widens what is retrievable.
            k: How many passages to retrieve (default 6).
        """
        prin = _agent_principal(platform, tenant, designation)
        return svc.ask(prin, question, k=k).to_dict()

    @server.tool(
        description=(
            "Discover reusable assets across the organisation — code, tests, "
            "policies, learning material — for a capability or need "
            '(e.g. "sso auth code I can reuse", "an automation script for '
            'ingestion"). Returns a ranked, deduped list of assets, each with a '
            "link to its exact place when there is one."
        )
    )
    def discover(query: str, k: int = 6) -> dict:
        """Search the fabric for reusable assets.

        Args:
            query: What you are looking for.
            k: Maximum assets to return (default 6).
        """
        prin = _agent_principal(platform, tenant)
        d = discover_assets(platform, tenant, query, prin.accessible_acls(), k=k)
        return {
            "query": d.query,
            "searched": d.searched,
            "hits": [
                {
                    "title": h.title,
                    "kind": h.kind,
                    "snippet": h.snippet,
                    "score": round(h.score, 4),
                    "source": h.source,
                    "url": h.url,
                    "path": h.path,
                    "symbol": h.symbol,
                }
                for h in d.hits
            ],
        }

    @server.tool(
        description=(
            "Read how large the accessible corpus is — documents, passages, "
            "entities, relationships and distinct sources — so a caller can see "
            "what knowledge is available before asking."
        )
    )
    def corpus() -> dict:
        """Corpus statistics for the fabric."""
        docs = platform.documents.list(tenant)
        nodes, edges = platform.graph_repo.counts(tenant)
        domains = len({d.get("source", "") for d in docs if d.get("source")})
        return {
            "tenant": tenant,
            "documents": len(docs),
            "passages": platform.passages.count(tenant),
            "entities": nodes,
            "relationships": edges,
            "domains": domains,
        }

    return server
