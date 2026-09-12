"""Read-only tools for the answering agent (T43).

Every tool returns ``{"result": ..., "citations": [{title, url, document_id,
locator}]}``; ``TOOL_SPECS`` is the Messages-API tool list (JSON
``input_schema`` each) and ``execute`` dispatches one ``tool_use`` block. Tools
never write: the table query is SELECT-only on a read-only connection, the
GitHub endpoints are allow-listed GETs over ingested repositories, Jira and
Confluence go through injected read-only callables (or report themselves
unavailable), and ``calculate`` is an ``ast`` evaluator — no ``eval``.

Access control is the caller's principal: passage search and page reads run
under its ACLs exactly as the answer service does (I6).
"""

from __future__ import annotations

import ast
import datetime as _dt
import json
import operator as _op
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .. import facts as factsmod
from . import aggregate, tables

# --------------------------------------------------------------------------
# tool definitions (the exact Messages-API wire shape)
# --------------------------------------------------------------------------
TOOL_SPECS: list[dict] = [
    {
        "name": "query_facts",
        "description": (
            "Exact, as-of-dated facts from the fabric: repository counts (commits, "
            "contributors, PRs, languages, deployments), capabilities (which repos have RAG, "
            "caching, auth…), licences, enterprise readiness, Jira issue counts, Confluence "
            "spaces, sheet aggregates, symbol examples. Try this FIRST for any count or "
            "inventory question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question, verbatim."}
            },
            "required": ["question"],
        },
    },
    {
        "name": "search_passages",
        "description": (
            "Hybrid (lexical + vector) retrieval over the ingested documents the asker may "
            "read. Returns passages with document titles and locations to cite."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "area": {
                    "type": "string",
                    "description": "Optional source area (github, confluence, jira, internal…).",
                },
                "source": {"type": "string", "description": "Optional exact source name."},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_page",
        "description": (
            "Read one page (or the whole document when page is omitted) of an ingested "
            "document by id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}, "page": {"type": "integer"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "search_code",
        "description": (
            "Search analysed code symbols (functions, classes) and code passages across "
            "repositories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "repo": {"type": "string", "description": "owner/name to restrict to."},
                "language": {"type": "string"},
                "symbol": {"type": "string", "description": "An exact symbol name when known."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_symbol",
        "description": (
            "The source of one symbol in a repository (lines from the clone or code passage, "
            "else signature + docstring)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "symbol": {"type": "string"}},
            "required": ["repo", "symbol"],
        },
    },
    {
        "name": "describe_table",
        "description": (
            "Columns, row count and a sample of an ingested spreadsheet sheet (table name is "
            "always t)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}, "sheet": {"type": "string"}},
            "required": ["doc_id", "sheet"],
        },
    },
    {
        "name": "run_table_query",
        "description": (
            "Run ONE SELECT over table t of a sheet (aggregates, GROUP BY, sub-selects allowed; "
            "max 200 rows)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string"},
                "sheet": {"type": "string"},
                "sql": {"type": "string"},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["doc_id", "sheet", "sql"],
        },
    },
    {
        "name": "github_api",
        "description": (
            "Live GitHub read for an ingested repository. Allowed endpoints: /repos/{o}/{r} and "
            "/repos/{o}/{r}/{commits|pulls|issues|releases|deployments|languages|contributors}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"endpoint": {"type": "string"}},
            "required": ["endpoint"],
        },
    },
    {
        "name": "jira_search",
        "description": (
            "Live, read-only Jira JQL search (at most 100 issues). Reports unavailable when no "
            "live connection exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jql": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["jql"],
        },
    },
    {
        "name": "confluence_search",
        "description": (
            "Live, read-only Confluence CQL search. Reports unavailable when no live "
            "connection exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"cql": {"type": "string"}},
            "required": ["cql"],
        },
    },
    {
        "name": "corroborate",
        "description": (
            "Cross-source verification (T99): given a claim that names a Jira issue key "
            "(e.g. 'V1-42 is done — check the repo'), report whether the repository corroborates "
            "the Jira status — agreement, or the specific discrepancy — citing BOTH sources. Uses "
            "the relationships graph of commits/PRs that mention the issue. Use when asked to "
            "verify, cross-check or confirm one source against another."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "claim": {
                    "type": "string",
                    "description": "The claim to verify, naming a Jira issue key.",
                }
            },
            "required": ["claim"],
        },
    },
    {
        "name": "calculate",
        "description": (
            "Arithmetic and date math, evaluated safely. Numbers, + - * / // % **, min/max/abs/"
            "round/sum/avg, date('YYYY-MM-DD'), today(), days_between(a, b), date_add(d, days)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the person a clarifying question with 2-4 short options. Ends the run; use "
            "only when the sources cannot settle the ambiguity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 4,
                },
            },
            "required": ["question", "options"],
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_SPECS]

# The no-blind-gap order: facts → retrieval → table query → code search → live
# source API → ask the user. The agent's system prompt states it; the loop
# enforces it when the model would decline without having tried the next one.
SEARCH_ORDER = [
    "query_facts",
    "search_passages",
    "run_table_query",
    "search_code",
    "github_api",
    "jira_search",
    "confluence_search",
    "corroborate",
    "ask_user",
]

GITHUB_ENDPOINT = re.compile(
    r"^/repos/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?:/(?P<sub>commits|pulls|issues|releases|deployments|languages|contributors))?/?$"
)


class ToolError(ValueError):
    """A tool refused its input (surfaced to the model as ``is_error``)."""


@dataclass
class ToolContext:
    """What a tool run needs: the platform, the asker, the conversation, and
    any live connections injected by the host (never constructed here)."""

    platform: object
    principal: object
    context: dict | None = None
    live_jql: object = None
    live_cql: object = None
    github_transport: object = None
    github_token: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def tenant(self) -> str:
        return self.principal.tenant


def _ok(result, citations=None) -> dict:
    return {"result": result, "citations": list(citations or [])}


# --------------------------------------------------------------------------
# the tools
# --------------------------------------------------------------------------
def query_facts(p, tenant: str, question: str, principal=None, context=None, live_jql=None) -> dict:
    res = aggregate.analyse(p, principal, question, context, live_jql=live_jql)
    if res is None:
        return _ok({"found": False, "note": "no fact pattern matched; try search_passages"})
    d = res.to_dict()
    if res.clarify:
        return _ok({"found": False, "clarify": res.clarify, "options": res.chips})
    return _ok(
        {"found": True, "text": d["text"], "pattern": d["pattern"], "live": d["live"]},
        d["citations"],
    )


def _rrf(*hit_lists, k=60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for hits in hit_lists:
        for rank, (pid, _s) in enumerate(hits):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def _passage_hit(p, tenant, pas, score) -> tuple[dict, dict]:
    d = p.documents.get(tenant, pas.document_id) or {}
    loc = dict(pas.coordinate.locator or {})
    title = d.get("title") or loc.get("path") or pas.document_id
    citation = {
        "title": title,
        "url": loc.get("url", "") or d.get("uri", ""),
        "document_id": pas.document_id,
        "locator": loc,
        "kind": pas.coordinate.kind.value,
        "passage_id": pas.id,
        "snippet": (pas.abstract or pas.text)[:200],
    }
    hit = {
        "document_id": pas.document_id,
        "title": title,
        "source": d.get("source", ""),
        "location": pas.coordinate.render(),
        "text": pas.text[:1200],
        "score": round(float(score), 4),
    }
    return hit, citation


def search_passages(p, principal, query: str, area=None, source=None, top_k: int = 6) -> dict:
    tenant = principal.tenant
    acc = principal.accessible_acls()
    top_k = max(1, min(int(top_k or 6), 20))
    lex = p.lindex.search(tenant, query, top_k * 3, acc)
    vec = []
    try:
        qvec = p.embedder.embed([query])[0]
        vec = p.vindex.search(tenant, qvec, top_k * 3, acc)
    except Exception:  # a store without a vector index — lexical only
        vec = []
    hits, cites = [], []
    for pid, score in _rrf(vec, lex):
        pas = p.passages.get(tenant, pid)
        if not pas or not (set(p.passages.acl_of(tenant, pid)) & set(acc)):
            continue
        d = p.documents.get(tenant, pas.document_id) or {}
        if source and (d.get("source") or "") != source:
            continue
        if (
            area
            and area.lower() not in ((d.get("source") or "") + " " + (d.get("uri") or "")).lower()
        ):
            continue
        h, c = _passage_hit(p, tenant, pas, score)
        hits.append(h)
        cites.append(c)
        if len(hits) >= top_k:
            break
    return _ok({"hits": hits, "count": len(hits)}, cites)


def read_page(p, principal, doc_id: str, page=None) -> dict:
    tenant = principal.tenant
    acc = set(principal.accessible_acls())
    d = p.documents.get(tenant, doc_id)
    if not d:
        raise ToolError(f"no document {doc_id!r}")
    try:
        doc_acl = set(json.loads(d.get("acl") or "[]"))
    except ValueError:
        doc_acl = {"public"}
    if not (doc_acl & acc):
        raise ToolError("not permitted to read this document")
    pas = [x for x in p.passages.by_document(tenant, doc_id) if x.superseded_by is None]
    if page is not None:
        pas = [x for x in pas if str((x.coordinate.locator or {}).get("page", "")) == str(page)]
    pas.sort(
        key=lambda x: (
            int((x.coordinate.locator or {}).get("page", 0) or 0),
            int((x.coordinate.locator or {}).get("paragraph", 0) or 0),
            int((x.coordinate.locator or {}).get("line", 0) or 0),
        )
    )
    text = "\n\n".join(x.text for x in pas)[:12000]
    cites = [_passage_hit(p, tenant, x, 1.0)[1] for x in pas[:1]]
    return _ok(
        {
            "document_id": doc_id,
            "title": d.get("title"),
            "page": page,
            "text": text,
            "passages": len(pas),
        },
        cites,
    )


def search_code(
    p, tenant: str, query: str, repo=None, language=None, symbol=None, principal=None
) -> dict:
    syms = aggregate.find_symbols(query, repo=repo, language=language, symbol=symbol, limit=8)
    hits, cites = [], []
    for s in syms:
        cites.append(aggregate.citation_to_dict(aggregate.symbol_citation(s)))
        hits.append(
            {
                k: s.get(k)
                for k in (
                    "repo",
                    "path",
                    "language",
                    "symbol",
                    "qualified",
                    "kind",
                    "signature",
                    "docstring",
                    "start_line",
                    "end_line",
                    "url",
                )
            }
        )
    # code passages ingested into the fabric (ACL-filtered) complement the analysis files
    if principal is not None and len(hits) < 8:
        acc = set(principal.accessible_acls())
        qtoks = [
            t
            for t in re.findall(r"[a-z0-9_]+", (query + " " + (symbol or "")).lower())
            if len(t) >= 3
        ]
        for pas in p.passages.for_tenant(tenant):
            if pas.coordinate.kind.value != "symbol_line":
                continue
            loc = pas.coordinate.locator or {}
            if repo and repo not in str(loc.get("url", "")) + str(loc.get("path", "")):
                continue
            if not (set(p.passages.acl_of(tenant, pas.id)) & acc):
                continue
            name = str(loc.get("symbol", "")).lower()
            path = str(loc.get("path", "")).lower()
            score = sum(
                (3 if t == name else 2 if t in name else 0) + (1 if t in path else 0) for t in qtoks
            )
            if score <= 0:
                continue
            h, c = _passage_hit(p, tenant, pas, score)
            h.update(
                {"symbol": loc.get("symbol"), "path": loc.get("path"), "line": loc.get("line")}
            )
            hits.append(h)
            cites.append(c)
            if len(hits) >= 8:
                break
    return _ok({"hits": hits, "count": len(hits)}, cites)


def get_symbol(p, tenant: str, repo: str, symbol: str, principal=None) -> dict:
    syms = aggregate.find_symbols(symbol, repo=repo, symbol=symbol, limit=1)
    if not syms:
        raise ToolError(f"no symbol {symbol!r} in {repo!r}")
    s = syms[0]
    acc = principal.accessible_acls() if principal is not None else None
    code = aggregate.symbol_code(p, tenant, s, acc)
    return _ok(
        {
            **{
                k: s.get(k)
                for k in (
                    "repo",
                    "path",
                    "language",
                    "symbol",
                    "qualified",
                    "kind",
                    "signature",
                    "docstring",
                    "start_line",
                    "end_line",
                    "url",
                )
            },
            "code": code[:6000],
        },
        [aggregate.citation_to_dict(aggregate.symbol_citation(s))],
    )


def describe_table(doc_id: str, sheet: str) -> dict:
    try:
        d = tables.describe_table(doc_id, sheet)
    except tables.TableQueryError as e:
        raise ToolError(str(e)) from e
    c = d.pop("citation")
    return _ok(d, [c])


def run_table_query(doc_id: str, sheet: str, sql: str, max_rows: int = 200) -> dict:
    try:
        r = tables.run_table_query(doc_id, sheet, sql, max_rows=max_rows)
    except tables.TableQueryError as e:
        raise ToolError(str(e)) from e
    c = r.pop("citation")
    return _ok(r, [c])


def github_api(endpoint: str, transport=None, token: str = "", per_page: int = 30) -> dict:
    m = GITHUB_ENDPOINT.match((endpoint or "").strip())
    if not m:
        raise ToolError(
            "endpoint not allowed; use /repos/{o}/{r}[/commits|pulls|issues|releases|"
            "deployments|languages|contributors]"
        )
    repo = f"{m.group('owner')}/{m.group('repo')}"
    ingested = set(factsmod.load_facts().get("repositories") or {})
    if repo not in ingested:
        raise ToolError(f"{repo} is not an ingested repository")
    tok = token or os.environ.get("KF_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not tok and transport is None:
        return _ok({"available": False, "reason": "no GitHub token (KF_GITHUB_TOKEN/GITHUB_TOKEN)"})
    url = f"https://api.github.com{m.group(0).rstrip('/')}"
    if m.group("sub"):
        url += f"?per_page={int(per_page)}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "qualizeal-fabric"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    opener = transport or urllib.request.urlopen
    try:
        with opener(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise ToolError(
            f"GitHub returned HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
        ) from e
    except (urllib.error.URLError, OSError) as e:
        raise ToolError(f"GitHub unreachable: {e}") from e
    if isinstance(data, list):
        data = data[: int(per_page)]
    sub = m.group("sub") or ""
    return _ok(
        {"endpoint": m.group(0), "data": data, "fetched_at": _now()},
        [
            {
                "title": f"{repo} {sub}".strip(),
                "url": f"https://github.com/{repo}",
                "document_id": repo,
                "locator": {"endpoint": m.group(0), "live": True, "repo": repo},
            }
        ],
    )


def jira_search(jql: str, fields=None, live_jql=None) -> dict:
    if live_jql is None:
        return _ok({"available": False, "reason": "no live Jira connection configured"})
    if not jql or not jql.strip():
        raise ToolError("empty JQL")
    raw = live_jql(jql, list(fields or ["summary", "status", "priority", "assignee", "issuetype"]))
    issues = raw.get("issues", []) if isinstance(raw, dict) else list(raw or [])
    total = raw.get("total", len(issues)) if isinstance(raw, dict) else len(issues)
    issues = issues[:100]
    base = (os.environ.get("KF_JIRA_BASE_URL") or os.environ.get("JIRA_BASE_URL") or "").rstrip("/")
    cites = []
    for it in issues[:10]:
        key = it.get("key", "")
        cites.append(
            {
                "title": f"Jira {key}",
                "url": f"{base}/browse/{key}" if base else f"jira://browse/{key}",
                "document_id": f"jira:{key}",
                "locator": {"key": key, "jql": jql, "live": True},
            }
        )
    return _ok(
        {
            "available": True,
            "jql": jql,
            "total": total,
            "returned": len(issues),
            "issues": issues,
            "fetched_at": _now(),
        },
        cites,
    )


def confluence_search(cql: str, live_cql=None) -> dict:
    if live_cql is None:
        return _ok({"available": False, "reason": "no live Confluence connection configured"})
    if not cql or not cql.strip():
        raise ToolError("empty CQL")
    raw = live_cql(cql)
    results = raw.get("results", []) if isinstance(raw, dict) else list(raw or [])
    results = results[:50]
    base = (
        os.environ.get("KF_CONFLUENCE_BASE_URL") or os.environ.get("CONFLUENCE_BASE_URL") or ""
    ).rstrip("/")
    cites = []
    for r in results[:10]:
        pid = str(r.get("id", ""))
        title = r.get("title", pid)
        url = r.get("url") or (f"{base}/wiki/pages/{pid}" if base else f"confluence://pages/{pid}")
        cites.append(
            {
                "title": title,
                "url": url,
                "document_id": f"confluence:{pid}",
                "locator": {"id": pid, "cql": cql, "live": True},
            }
        )
    return _ok(
        {
            "available": True,
            "cql": cql,
            "returned": len(results),
            "results": results,
            "fetched_at": _now(),
        },
        cites,
    )


# ---- calculate: a safe ast evaluator (numbers + date math) ----------------
_BIN = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
}
_UN = {ast.USub: _op.neg, ast.UAdd: _op.pos}


def _date(s) -> _dt.date:
    if isinstance(s, _dt.date):
        return s
    return _dt.date.fromisoformat(str(s).strip()[:10])


def _avg(*xs):
    xs = list(xs[0]) if len(xs) == 1 and isinstance(xs[0], list | tuple) else list(xs)
    return sum(xs) / len(xs) if xs else 0


_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": lambda *a: sum(a[0] if len(a) == 1 else a),
    "avg": _avg,
    "date": _date,
    "today": lambda: _dt.date.today(),
    "days_between": lambda a, b: (_date(b) - _date(a)).days,
    "date_add": lambda d, n: _date(d) + _dt.timedelta(days=int(n)),
}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float | str):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and isinstance(b, int | float) and abs(b) > 64:
            raise ToolError("exponent too large")
        if isinstance(a, _dt.date) and isinstance(b, _dt.date) and isinstance(node.op, ast.Sub):
            return (a - b).days
        if (
            isinstance(a, _dt.date)
            and isinstance(b, int | float)
            and type(node.op) in (ast.Add, ast.Sub)
        ):
            return _BIN[type(node.op)](a, _dt.timedelta(days=int(b)))
        if isinstance(a, str) or isinstance(b, str):
            raise ToolError("strings only inside date()")
        return _BIN[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        if node.keywords:
            raise ToolError("keyword arguments are not supported")
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    if isinstance(node, ast.Tuple | ast.List):
        return [_eval(e) for e in node.elts]
    raise ToolError(f"unsupported expression element: {type(node).__name__}")


def calculate(expression: str) -> dict:
    expr = (expression or "").strip()
    if not expr or len(expr) > 500:
        raise ToolError("expression must be 1-500 characters")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"cannot parse: {e.msg}") from e
    try:
        value = _eval(tree)
    except (ZeroDivisionError, ValueError, TypeError, OverflowError) as e:
        raise ToolError(f"cannot evaluate: {e}") from e
    shown = value.isoformat() if isinstance(value, _dt.date) else value
    return _ok(
        {"expression": expr, "value": shown},
        [
            {
                "title": "calculation",
                "url": "",
                "document_id": "calc",
                "locator": {"expression": expr},
            }
        ],
    )


def corroborate(p, principal, claim: str, live_jql=None) -> dict:
    from . import cross_source

    res = cross_source.verify(p, principal, claim, live_jql=live_jql)
    if res is None:
        return _ok(
            {
                "found": False,
                "note": "no Jira issue key to verify; name a key like V1-42 in the claim",
            }
        )
    return _ok(
        {
            "found": True,
            "verdict": res.verdict,
            "text": res.text,
            "entity": res.entity,
            "jira_status": (res.per_source.get("jira") or {}).get("status"),
            "repo_references": len((res.per_source.get("repo") or {}).get("references") or []),
        },
        [aggregate.citation_to_dict(c) for c in res.citations],
    )


def ask_user(question: str, options=None) -> dict:
    opts = [str(o) for o in (options or []) if str(o).strip()][:4]
    if not question or len(opts) < 2:
        raise ToolError("ask_user needs a question and 2-4 options")
    return _ok({"ask_user": True, "question": question, "options": opts})


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
def execute(name: str, inp: dict, ctx: ToolContext) -> dict:
    """Run one tool by name with a parsed ``input`` dict. Raises ``ToolError``
    for refused input (the loop returns it as ``is_error``)."""
    inp = dict(inp or {})
    p, pr = ctx.platform, ctx.principal
    if name == "query_facts":
        return query_facts(
            p, ctx.tenant, str(inp.get("question", "")), pr, ctx.context, ctx.live_jql
        )
    if name == "search_passages":
        return search_passages(
            p,
            pr,
            str(inp.get("query", "")),
            inp.get("area"),
            inp.get("source"),
            inp.get("top_k") or 6,
        )
    if name == "read_page":
        return read_page(p, pr, str(inp.get("doc_id", "")), inp.get("page"))
    if name == "search_code":
        return search_code(
            p,
            ctx.tenant,
            str(inp.get("query", "")),
            inp.get("repo"),
            inp.get("language"),
            inp.get("symbol"),
            pr,
        )
    if name == "get_symbol":
        return get_symbol(p, ctx.tenant, str(inp.get("repo", "")), str(inp.get("symbol", "")), pr)
    if name == "describe_table":
        return describe_table(str(inp.get("doc_id", "")), str(inp.get("sheet", "")))
    if name == "run_table_query":
        return run_table_query(
            str(inp.get("doc_id", "")),
            str(inp.get("sheet", "")),
            str(inp.get("sql", "")),
            int(inp.get("max_rows") or 200),
        )
    if name == "github_api":
        return github_api(str(inp.get("endpoint", "")), ctx.github_transport, ctx.github_token)
    if name == "jira_search":
        return jira_search(str(inp.get("jql", "")), inp.get("fields"), ctx.live_jql)
    if name == "confluence_search":
        return confluence_search(str(inp.get("cql", "")), ctx.live_cql)
    if name == "corroborate":
        return corroborate(p, pr, str(inp.get("claim", "")), ctx.live_jql)
    if name == "calculate":
        return calculate(str(inp.get("expression", "")))
    if name == "ask_user":
        return ask_user(str(inp.get("question", "")), inp.get("options"))
    raise ToolError(f"unknown tool {name!r}")


def n_results(name: str, out: dict) -> int:
    """How many results a tool produced (for ``Checked <tool> · <n> results``)."""
    r = out.get("result")
    if not isinstance(r, dict):
        return len(r) if isinstance(r, list) else (1 if r is not None else 0)
    for k in ("count", "row_count", "returned", "passages"):
        if k in r:
            return int(r[k] or 0)
    if "found" in r:
        return len(out.get("citations") or []) if r["found"] else 0
    if isinstance(r.get("data"), list):
        return len(r["data"])
    if r.get("available") is False:
        return 0
    return len(out.get("citations") or []) or 1


def available_live(ctx: ToolContext) -> set[str]:
    """Which live-source tools have credentials/callables in this run."""
    live = set()
    if ctx.live_jql is not None:
        live.add("jira_search")
    if ctx.live_cql is not None:
        live.add("confluence_search")
    if (
        ctx.github_transport is not None
        or ctx.github_token
        or os.environ.get("KF_GITHUB_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    ):
        live.add("github_api")
    return live
