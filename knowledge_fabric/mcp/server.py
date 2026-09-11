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

from .. import fabric_data as fd
from .. import fabric_views
from ..answer.search import discover as discover_assets
from ..answer.service import AnswerService
from ..contracts.types import Principal

SERVER_NAME = "qualizeal-knowledge-fabric"
INSTRUCTIONS = (
    "The QualiZeal Knowledge Fabric. Ask grounded, cited questions of the "
    "organisation's knowledge (products, code, policies, learning material), "
    "discover reusable assets across the fabric, or read how large the corpus "
    "is. Every answer is grounded and cited; when the evidence is weak the "
    "fabric declines or asks back rather than guessing. The facts tools "
    "(query_facts, get_repository, list_capabilities, get_dependencies, "
    "get_pull_requests, get_commits, explain_architecture, describe_image) read "
    "the analysed fabric-data files directly; search_code, run_table_query, "
    "jira_search and confluence_search go through the answer tool API and say "
    "'tool unavailable' when it is not installed rather than inventing a result."
)

#: every tool the server registers (T31 + T48), in registration order
TOOL_NAMES = (
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
)


# --------------------------------------------------------------------------
# T48 — tool implementations. Plain functions so they are unit-testable without
# the optional ``mcp`` package; ``build_server`` wraps each one. Every tool
# returns ``{"result", "citations"}``; the ones that need the T42/T43 answer
# tool API return ``{"error": "tool unavailable: <why>"}`` when it is absent —
# never a fabricated result.
# --------------------------------------------------------------------------
def _answer_tools():
    """``knowledge_fabric.answer.tools`` (another track) or ``None``."""
    try:
        from ..answer import tools as _tools  # optional at this stage
    except Exception:
        return None
    return _tools


def _unavailable(name: str, why: str) -> dict:
    return {"error": f"tool unavailable: {why}", "tool": name, "result": None, "citations": []}


def _via_tools(name: str, fn_name: str, *args, **kwargs) -> dict:
    """Call ``answer.tools.<fn_name>`` when present; else an honest unavailable."""
    tools = _answer_tools()
    fn = getattr(tools, fn_name, None) if tools else None
    if not callable(fn):
        return _unavailable(name, f"knowledge_fabric.answer.tools.{fn_name} is not installed")
    try:
        out = fn(*args, **kwargs)
    except (ValueError, PermissionError, FileNotFoundError, KeyError) as e:
        return {"error": str(e), "tool": name, "result": None, "citations": []}
    if isinstance(out, dict) and "result" in out:
        out.setdefault("citations", [])
        return out
    return {"result": out, "citations": []}


def tool_query_facts(question: str) -> dict:
    return fabric_views.query_facts(question)


def tool_get_repository(repo: str, platform=None, tenant: str = "") -> dict:
    card = fabric_views.repository(repo, platform, tenant)
    if card is None:
        known = sorted((fabric_views.facts().get("repositories") or {}).keys())
        return {
            "error": f"repository '{repo}' is not in facts.json",
            "result": None,
            "citations": [fabric_views.citation(fd.data_path("facts.json"))],
            "known_repositories": known,
        }
    slug = fd.repo_slug(repo)
    return {
        "result": card,
        "citations": [
            fabric_views.citation(fd.data_path("facts.json"), "facts + contributors"),
            fabric_views.citation(fd.data_path("capabilities.json"), "capability evidence"),
            fabric_views.citation(fd.data_path("dependencies.json"), "dependencies + licences"),
            fabric_views.citation(fd.path("analysis", slug, "architecture.md")),
            fabric_views.citation(fd.path("analysis", slug, "card.md")),
        ],
    }


def tool_list_capabilities(capability: str = "") -> dict:
    ins = fabric_views.insights()
    caps = ins["capabilities"]
    want = (capability or "").strip().lower()
    if want:
        caps = {k: v for k, v in caps.items() if want in k.lower()}
    return {
        "result": {"capabilities": caps, "reuse": ins["reuse"], "filter": want},
        "citations": [fabric_views.citation(fd.data_path("capabilities.json"))],
    }


def tool_get_dependencies(repo: str) -> dict:
    deps = fabric_views.dependencies()
    rows = deps.get(repo)
    if rows is None:
        return {
            "error": f"no dependency manifest parsed for '{repo}'",
            "result": None,
            "citations": [fabric_views.citation(fd.data_path("dependencies.json"))],
            "known_repositories": sorted(deps.keys()),
        }
    licences: dict[str, int] = {}
    for d in rows:
        lic = str((d or {}).get("licence") or (d or {}).get("license") or "unknown")
        licences[lic] = licences.get(lic, 0) + 1
    return {
        "result": {"repo": repo, "dependencies": rows, "count": len(rows), "by_licence": licences},
        "citations": [fabric_views.citation(fd.data_path("dependencies.json"))],
    }


def tool_search_code(
    platform, tenant: str, query: str, repo: str = "", language: str = "", symbol: str = ""
) -> dict:
    return _via_tools(
        "search_code",
        "search_code",
        platform,
        tenant,
        query,
        repo=repo or None,
        language=language or None,
        symbol=symbol or None,
    )


def _activity(platform, tenant: str, repo: str, kind: str, limit: int, state: str = "all") -> dict:
    facts_repo = (fabric_views.facts().get("repositories") or {}).get(repo)
    if facts_repo is None:
        return {
            "error": f"repository '{repo}' is not in facts.json",
            "result": None,
            "citations": [fabric_views.citation(fd.data_path("facts.json"))],
        }
    act = fabric_views.recent_activity(platform, tenant, repo)
    key = "recent_prs" if kind == "pull_requests" else "recent_commits"
    items = act.get(key) or []
    if kind == "pull_requests" and state and state != "all":
        items = [i for i in items if state.lower() in (i.get("snippet") or "").lower()[:80]]
    result = {
        "repo": repo,
        kind: items[: max(1, int(limit))],
        "counts": facts_repo.get("pull_requests" if kind == "pull_requests" else "commits") or {},
    }
    if kind == "pull_requests":
        result["state"] = state
    if act.get("note"):
        result["note"] = act["note"]
    return {
        "result": result,
        "citations": [
            fabric_views.citation(fd.data_path("facts.json"), "counts"),
            {"source": "fabric-store", "note": "ingested github:// passages", "items": len(items)},
        ],
    }


def tool_get_pull_requests(
    platform, tenant: str, repo: str, state: str = "all", limit: int = 20
) -> dict:
    return _activity(platform, tenant, repo, "pull_requests", limit, state)


def tool_get_commits(platform, tenant: str, repo: str, limit: int = 20) -> dict:
    return _activity(platform, tenant, repo, "commits", limit)


def tool_explain_architecture(repo: str) -> dict:
    slug = fd.repo_slug(repo)
    arch = fd.path("analysis", slug, "architecture.md")
    card = fd.path("analysis", slug, "card.md")
    summary = fd.read_json(fd.path("analysis", slug, "summary.json"), {}) or {}
    known = repo in (fabric_views.facts().get("repositories") or {})
    arch_md = fabric_views._read_text(arch)
    card_md = fabric_views._read_text(card)
    if not known and not arch_md and not card_md:
        return {
            "error": f"no analysis for '{repo}' (not in facts.json, no analysis/{slug}/)",
            "result": None,
            "citations": [fabric_views.citation(arch), fabric_views.citation(card)],
        }
    caps = [
        {"capability": c.get("capability"), "confidence": c.get("confidence")}
        for c in fabric_views.capabilities()
        if c.get("repo") == repo
    ]
    return {
        "result": {
            "repo": repo,
            "architecture_md": arch_md,
            "card_md": card_md,
            "summary": summary if isinstance(summary, dict) else {},
            "capabilities": caps,
            "note": "" if arch_md else "architecture.md not generated yet for this repository",
        },
        "citations": [fabric_views.citation(arch), fabric_views.citation(card)],
    }


def tool_run_table_query(doc_id: str, sheet: str, sql: str, max_rows: int = 200) -> dict:
    if not str(sql or "").lstrip().lower().startswith(("select", "with")):
        return {"error": "only SELECT queries are allowed", "result": None, "citations": []}
    out = _via_tools("run_table_query", "run_table_query", doc_id, sheet, sql, max_rows=max_rows)
    if "error" not in out:
        out.setdefault("citations", []).append(
            fabric_views.citation(fd.path("tables", doc_id, f"{sheet}.sqlite"))
        )
    return out


def tool_jira_search(jql: str, fields: str = "summary,status") -> dict:
    return _via_tools("jira_search", "jira_search", jql, fields)


def tool_confluence_search(cql: str) -> dict:
    return _via_tools("confluence_search", "confluence_search", cql)


def tool_describe_image(doc_id: str, name: str) -> dict:
    path = fd.path("images", doc_id, f"{name}.json")
    desc = fabric_views.image_description(doc_id, name)
    if desc is None:
        return {
            "error": f"no stored description for image '{name}' of document '{doc_id}'",
            "result": None,
            "citations": [fabric_views.citation(path)],
        }
    return {
        "result": {"doc_id": doc_id, "name": name, **desc},
        "citations": [fabric_views.citation(path)],
    }


def tool_ask_fabric(
    platform,
    tenant: str,
    question: str,
    designation: str = "",
    previous_questions: list[str] | None = None,
    svc: AnswerService | None = None,
) -> dict:
    """Run the agent (``answer.agent.run``) as the caller's role profile with the
    two previous questions as context; when the agent module is not installed
    the same governed ``AnswerService`` path answers (real, cited, never a
    stub) and the result says which engine ran."""
    prin = _agent_principal(platform, tenant, designation)
    turns = [{"question": q} for q in (previous_questions or [])[-2:] if q]
    context = {"turns": turns, "history": [t["question"] for t in turns]} if turns else None
    engine = "answer_service"
    answer = None
    try:
        from ..answer import agent as _agent  # optional at this stage
    except Exception:
        _agent = None
    if _agent is not None and callable(getattr(_agent, "run", None)):
        try:
            answer = _agent.run(platform, prin, question, context)
            engine = "agent"
        except _agent.AgentUnavailableError:
            # Explicit keyless mode (KF_MODEL_MODE=extractive/off): the agent
            # needs the provider; the governed extractive core still answers
            # and the result says so. A configured-but-failing provider is
            # NOT caught here — that raises loudly (T35).
            answer = None
    if answer is None:
        svc = svc or AnswerService(platform)
        answer = svc.ask(prin, question, context=context)
    payload = answer.to_dict() if hasattr(answer, "to_dict") else dict(answer)
    steps = ((payload.get("why") or {}).get("steps")) if isinstance(payload, dict) else None
    return {
        "result": {
            **payload,
            "engine": engine,
            "previous_questions": [t["question"] for t in turns],
        },
        "citations": payload.get("citations") or [],
        "steps": steps or [],
    }


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
            **fabric_views.corpus_tiles(),
        }

    # ---- T48: facts / code / activity / tables / live / agent tools -------
    @server.tool(
        description=(
            "Answer a numbers question straight from the analysed facts "
            "(facts.json): commits, pull requests, contributors, deployments, "
            "releases, languages, workflows, capabilities and enterprise score per "
            "repository, plus Jira project and Confluence space counts. The same "
            "rows the Curator Repositories panel shows."
        )
    )
    def query_facts(question: str) -> dict:
        """Query the analysed facts.

        Args:
            question: A natural-language numbers question, e.g. "how many
                commits does owner/repo have" or "which repositories deploy".
        """
        return tool_query_facts(question)

    @server.tool(
        description=(
            "The full repository card: facts, languages, capability evidence, "
            "dependencies with licences, the architecture summary and card, "
            "contributors and the recent pull requests / commits ingested."
        )
    )
    def get_repository(repo: str) -> dict:
        """Read one repository's card.

        Args:
            repo: The repository as owner/name.
        """
        return tool_get_repository(repo, platform, tenant)

    @server.tool(
        description=(
            "Capabilities detected across repositories (rag, caching, "
            "dashboard_ui, enterprise_readiness, …) with the confidence per "
            "repository, plus reuse candidates; optionally filtered by name."
        )
    )
    def list_capabilities(capability: str = "") -> dict:
        """List capabilities across the fabric.

        Args:
            capability: Optional substring filter on the capability name.
        """
        return tool_list_capabilities(capability)

    @server.tool(
        description=(
            "Parsed dependencies of a repository (name, version, ecosystem, licence, category)."
        )
    )
    def get_dependencies(repo: str) -> dict:
        """Read a repository's dependencies.

        Args:
            repo: The repository as owner/name.
        """
        return tool_get_dependencies(repo)

    @server.tool(
        description=(
            "Search the ingested code (symbols, paths, snippets) with optional "
            "repo / language / symbol filters. Says 'tool unavailable' when the "
            "code tool API is not installed rather than guessing."
        )
    )
    def search_code(query: str, repo: str = "", language: str = "", symbol: str = "") -> dict:
        """Search code.

        Args:
            query: Free-text query.
            repo: Optional owner/name filter.
            language: Optional language filter.
            symbol: Optional symbol-name filter.
        """
        return tool_search_code(platform, tenant, query, repo, language, symbol)

    @server.tool(
        description=(
            "Recent pull requests of a repository from the ingested GitHub "
            "passages, with the pull-request counts from facts.json."
        )
    )
    def get_pull_requests(repo: str, state: str = "all", limit: int = 20) -> dict:
        """List pull requests.

        Args:
            repo: The repository as owner/name.
            state: all | open | closed | merged.
            limit: Maximum items (default 20).
        """
        return tool_get_pull_requests(platform, tenant, repo, state, limit)

    @server.tool(
        description=(
            "Recent commits of a repository from the ingested GitHub passages, "
            "with the commit total from facts.json."
        )
    )
    def get_commits(repo: str, limit: int = 20) -> dict:
        """List commits.

        Args:
            repo: The repository as owner/name.
            limit: Maximum items (default 20).
        """
        return tool_get_commits(platform, tenant, repo, limit)

    @server.tool(
        description=(
            "Explain a repository's architecture: the generated architecture "
            "summary, the repository card, the analysis summary and the "
            "capabilities detected."
        )
    )
    def explain_architecture(repo: str) -> dict:
        """Explain a repository's architecture.

        Args:
            repo: The repository as owner/name.
        """
        return tool_explain_architecture(repo)

    @server.tool(
        description=(
            "Run a read-only SELECT over an extracted spreadsheet sheet "
            "(tables/<doc>/<sheet>.sqlite, single table t). Says 'tool "
            "unavailable' when the table tool API is not installed."
        )
    )
    def run_table_query(doc_id: str, sheet: str, sql: str) -> dict:
        """Query an extracted sheet.

        Args:
            doc_id: The document id the sheet was extracted from.
            sheet: The sheet name.
            sql: A SELECT statement over table t.
        """
        return tool_run_table_query(doc_id, sheet, sql)

    @server.tool(
        description=(
            "Search Jira live with JQL through the governed connector. Says "
            "'tool unavailable' when the live tool API is not installed."
        )
    )
    def jira_search(jql: str, fields: str = "summary,status") -> dict:
        """Search Jira.

        Args:
            jql: The JQL query.
            fields: Comma-separated fields to return.
        """
        return tool_jira_search(jql, fields)

    @server.tool(
        description=(
            "Search Confluence live with CQL through the governed connector. "
            "Says 'tool unavailable' when the live tool API is not installed."
        )
    )
    def confluence_search(cql: str) -> dict:
        """Search Confluence.

        Args:
            cql: The CQL query.
        """
        return tool_confluence_search(cql)

    @server.tool(
        description="The stored description of one image of a document (images/<doc>/<name>.json)."
    )
    def describe_image(doc_id: str, name: str) -> dict:
        """Describe an image.

        Args:
            doc_id: The document id.
            name: The image name (file stem).
        """
        return tool_describe_image(doc_id, name)

    @server.tool(
        description=(
            "Ask the fabric through the agent: it checks the facts, code, tables "
            "and live sources it needs and answers with citations, framed for "
            "the caller's designation, with the previous questions as context."
        )
    )
    def ask_fabric(
        question: str, designation: str = "", previous_questions: list[str] | None = None
    ) -> dict:
        """Ask the fabric via the agent.

        Args:
            question: The natural-language question.
            designation: Optional org designation (e.g. "Developer", "CTO")
                that frames the answer; it never widens what is retrievable.
            previous_questions: Up to two previous questions of this
                conversation, oldest first, used as context.
        """
        return tool_ask_fabric(platform, tenant, question, designation, previous_questions, svc)

    return server
