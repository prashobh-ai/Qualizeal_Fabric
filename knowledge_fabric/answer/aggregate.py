"""Level 0 — facts and aggregates, answered BEFORE retrieval (T42).

Questions about *counts, inventories and exact figures* — how many commits,
which repositories have RAG, the licences in a repo, the average of a sheet
column, the open bugs in a Jira project — are not prose questions. Retrieval
over passages would at best find a sentence that *mentions* a number; the
fabric-data files (``data/facts.json``, ``capabilities.json``,
``dependencies.json``, ``analysis/<repo>/``, ``tables/<doc>/<sheet>.sqlite``)
hold the exact figure with an as-of time. This module answers those questions
at Level 0: no model, exact numbers, every count stamped ``as of <time>``, and
a citation to the source (the repository, the Jira project, the sheet file, the
analysis document).

Names resolve by fuzzy match (difflib + token overlap): "the app repo", "QZ",
"the payroll sheet". Two candidates that fit equally → a CLARIFY answer with
chips. "its" / "there" resolve against the last one or two turns.

``try_answer`` returns ``None`` whenever the question is not one of these
shapes, so the prose path runs unchanged.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import os
import re
from dataclasses import dataclass, field

from .. import fabric_data as fd
from .. import facts as factsmod
from ..adapters.model import model_for_tier
from ..contracts.types import (
    Answer,
    AnswerKind,
    Citation,
    Coordinate,
    CoordinateKind,
    new_id,
    now_ms,
)
from . import personas, tables

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------
CAPABILITY_TERMS: dict[str, list[str]] = {
    "rag": ["rag", "retrieval augmented generation", "retrieval-augmented", "retrieval augmented"],
    "knowledge_graph": ["knowledge graph", "knowledge graphs", "graph database"],
    "knowledge_fabric": ["knowledge fabric", "knowledge fabrics"],
    "caching": ["caching", "cache", "caches", "cache layer"],
    "auth": ["auth", "authentication", "sso", "single sign-on", "single sign on", "login", "oauth"],
    "dashboard_ui": ["dashboard", "dashboards", "dashboard ui", "frontend", "front-end", "ui"],
    "api_service": ["api service", "rest api", "api", "apis", "web service"],
    "agent": ["agent", "agents", "agentic"],
    "data_pipeline": ["data pipeline", "data pipelines", "pipeline", "pipelines", "etl"],
    "ml_model": ["ml model", "ml models", "machine learning", "model training", "trained model"],
    "enterprise_readiness": ["enterprise readiness", "enterprise ready", "enterprise-ready"],
}
_CAP_LABEL = {
    "rag": "RAG",
    "knowledge_graph": "a knowledge graph",
    "knowledge_fabric": "a knowledge fabric",
    "caching": "caching",
    "auth": "authentication",
    "dashboard_ui": "a dashboard UI",
    "api_service": "an API service",
    "agent": "an agent",
    "data_pipeline": "a data pipeline",
    "ml_model": "an ML model",
    "enterprise_readiness": "enterprise readiness",
}

_PRONOUN = re.compile(
    r"\b(its|it|there|that (?:repo|repository|project|space|sheet|one)|"
    r"the (?:repo|repository|project|space|sheet)|this (?:repo|repository|project))\b",
    re.I,
)
_COUNT_Q = re.compile(
    r"\b(how many|how much|number of|count of|do we have|have we (?:ever )?"
    r"(?:built|made|done|implemented|got|used|created|shipped)|is there (?:any|an?)|"
    r"are there(?: any)?|did we (?:ever )?(?:build|make|implement|create)|"
    r"which (?:repos|repositories|projects) (?:have|use|used|implement)|"
    r"any|implementations?|built before|we have|examples? of)\b",
    re.I,
)
_AGG_WORDS = {
    "count": ("how many", "count", "number of", "count of", "rows"),
    "sum": ("total", "sum", "sum of", "total of"),
    "avg": ("average", "avg", "mean"),
    "max": ("max", "maximum", "highest", "largest", "biggest", "latest"),
    "min": ("min", "minimum", "lowest", "smallest", "earliest"),
}
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "for",
    "and",
    "or",
    "is",
    "are",
    "what",
    "which",
    "how",
    "many",
    "much",
    "do",
    "does",
    "did",
    "we",
    "have",
    "has",
    "was",
    "were",
    "there",
    "any",
    "to",
    "at",
    "by",
    "with",
    "from",
    "its",
    "it",
    "that",
    "this",
    "repo",
    "repository",
    "project",
    "sheet",
    "file",
    "table",
    "spreadsheet",
    "workbook",
    "space",
    "me",
    "show",
    "list",
    "please",
    "total",
    "number",
    "count",
    "average",
    "avg",
    "mean",
    "max",
    "maximum",
    "min",
    "minimum",
    "sum",
    "rows",
    "open",
    "closed",
    "merged",
    "now",
    "current",
    "currently",
    "today",
}
_WORD = re.compile(r"[a-z0-9][a-z0-9_.\-/]*")


def _tokens(s: str) -> list[str]:
    return _WORD.findall((s or "").lower())


def _content_tokens(s: str) -> list[str]:
    return [t for t in _tokens(s) if t not in _STOP and len(t) > 1]


# --------------------------------------------------------------------------
# fuzzy name resolution
# --------------------------------------------------------------------------
@dataclass
class Resolved:
    kind: str  # ok | ambiguous | none
    name: str = ""
    score: float = 0.0
    options: list[str] = field(default_factory=list)


def _alias_score(ql: str, qtoks: list[str], alias: str) -> float:
    a = alias.lower().strip()
    if not a:
        return 0.0
    if len(a) >= 2 and re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", ql):
        return 1.0
    atoks = _tokens(a)
    if not atoks or not qtoks:
        return 0.0
    # token overlap with a fuzzy per-token match (difflib)
    best_per = []
    for at in atoks:
        if at in _STOP and len(atoks) > 1:
            continue
        best = 0.0
        for qt in qtoks:
            if qt == at:
                best = 1.0
                break
            r = difflib.SequenceMatcher(None, qt, at).ratio()
            if r > best:
                best = r
        best_per.append(best)
    if not best_per:
        return 0.0
    score = sum(best_per) / len(best_per)
    return score if score >= 0.8 else 0.0


def resolve_name(question: str, candidates: dict[str, list[str]], floor: float = 0.8) -> Resolved:
    """Pick the candidate ``question`` names. ``candidates`` maps a canonical
    name to its aliases. Two candidates that fit within 0.1 of each other →
    ``ambiguous`` with both as options."""
    ql = (question or "").lower()
    qtoks = [t for t in _tokens(ql) if t not in _STOP]
    scored = []
    for name, aliases in candidates.items():
        s = max((_alias_score(ql, qtoks, a) for a in [name, *aliases]), default=0.0)
        if s >= floor:
            scored.append((s, name))
    if not scored:
        return Resolved("none")
    scored.sort(key=lambda x: (-x[0], x[1]))
    top_s, top = scored[0]
    close = [n for s, n in scored if top_s - s < 0.1]
    if len(close) > 1:
        return Resolved("ambiguous", top, top_s, close[:4])
    return Resolved("ok", top, top_s)


def repo_aliases(repo: str) -> list[str]:
    name = repo.split("/")[-1]
    return sorted(
        {repo, name, name.replace("-", " "), name.replace("_", " "), name.replace("-", "")}
    )


def _repo_candidates(facts: dict) -> dict[str, list[str]]:
    return {r: repo_aliases(r) for r in facts.get("repositories", {})}


def _project_candidates(facts: dict) -> dict[str, list[str]]:
    out = {}
    for key, block in facts.get("jira_projects", {}).items():
        aliases = [key, key.lower()]
        if isinstance(block, dict) and block.get("name"):
            aliases.append(str(block["name"]))
        out[key] = aliases
    return out


def _space_candidates(facts: dict) -> dict[str, list[str]]:
    out = {}
    for key, block in facts.get("confluence_spaces", {}).items():
        aliases = [key, key.lower()]
        if isinstance(block, dict) and block.get("name"):
            aliases.append(str(block["name"]))
        out[key] = aliases
    return out


def _sheet_key(t: dict) -> str:
    return f"{t.get('doc_id')}::{t.get('sheet')}"


def _sheet_candidates(facts: dict) -> dict[str, list[str]]:
    out = {}
    for t in facts.get("tables", []):
        title = str(t.get("doc_title") or t.get("doc_id") or "")
        base = re.sub(r"\.(xlsx|xlsm|xls|csv|tsv|ods)$", "", title, flags=re.I)
        sheet = str(t.get("sheet") or "")
        aliases = {
            title,
            base,
            sheet,
            f"{base} {sheet}",
            base.replace("_", " "),
            base.replace("-", " "),
        }
        out[_sheet_key(t)] = sorted(a for a in aliases if a)
    return out


# --------------------------------------------------------------------------
# helpers: time, numbers, citations
# --------------------------------------------------------------------------
def as_of_text(block: dict | None) -> str:
    """``as of 2026-09-11 08:00 UTC`` from a block's ``as_of`` (or now)."""
    raw = (block or {}).get("as_of") if isinstance(block, dict) else None
    d = _parse_time(raw) or _dt.datetime.now(_dt.UTC)
    return "as of " + d.astimezone(_dt.UTC).strftime("%Y-%m-%d %H:%M UTC")


def _parse_time(raw) -> _dt.datetime | None:
    if not raw:
        return None
    try:
        if isinstance(raw, int | float):
            v = float(raw)
            return _dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, _dt.UTC)
        s = str(raw).strip().replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=_dt.UTC)
    except (ValueError, TypeError, OSError):
        return None


def _age_minutes(block: dict | None) -> float:
    d = _parse_time((block or {}).get("as_of")) if isinstance(block, dict) else None
    if d is None:
        return float("inf")
    return (_dt.datetime.now(_dt.UTC) - d).total_seconds() / 60.0


def _n(v) -> str:
    try:
        if isinstance(v, bool):
            return str(v)
        if isinstance(v, int):
            return f"{v:,}"
        f = float(v)
        return f"{int(f):,}" if f.is_integer() else f"{f:,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _repo_url(repo: str) -> str:
    return f"https://github.com/{repo}"


def _blob_url(repo: str, path: str, line=None) -> str:
    u = f"https://github.com/{repo}/blob/HEAD/{path.lstrip('/')}"
    return f"{u}#L{int(line)}" if line else u


def _jira_url(key: str, block: dict) -> str:
    if isinstance(block, dict) and block.get("url"):
        return str(block["url"])
    base = (os.environ.get("KF_JIRA_BASE_URL") or os.environ.get("JIRA_BASE_URL") or "").rstrip("/")
    return f"{base}/projects/{key}" if base else f"jira://projects/{key}"


def _space_url(key: str, block: dict) -> str:
    if isinstance(block, dict) and block.get("url"):
        return str(block["url"])
    base = (
        os.environ.get("KF_CONFLUENCE_BASE_URL") or os.environ.get("CONFLUENCE_BASE_URL") or ""
    ).rstrip("/")
    return f"{base}/wiki/spaces/{key}" if base else f"confluence://spaces/{key}"


def cite(
    title: str,
    url: str,
    document_id: str,
    locator: dict | None = None,
    kind: CoordinateKind = CoordinateKind.PAGE_PARAGRAPH,
    snippet: str = "",
) -> Citation:
    loc = {"url": url, **(locator or {})}
    return Citation(
        document_id=document_id,
        document_title=title,
        coordinate=Coordinate(kind, loc),
        passage_id="",
        snippet=snippet[:200],
    )


def citation_to_dict(c: Citation) -> dict:
    """The tool-shaped citation (``{title, url, document_id, locator}``)."""
    return {
        "title": c.document_title,
        "url": c.coordinate.locator.get("url", ""),
        "document_id": c.document_id,
        "locator": dict(c.coordinate.locator),
        "kind": c.coordinate.kind.value,
        "snippet": c.snippet,
    }


def citation_from_dict(d: dict) -> Citation:
    kind = CoordinateKind(d.get("kind") or "page_paragraph")
    loc = dict(d.get("locator") or {})
    if d.get("url") and "url" not in loc:
        loc["url"] = d["url"]
    return Citation(
        document_id=str(d.get("document_id") or ""),
        document_title=str(d.get("title") or ""),
        coordinate=Coordinate(kind, loc),
        passage_id=str(d.get("passage_id") or ""),
        snippet=str(d.get("snippet") or "")[:200],
    )


# --------------------------------------------------------------------------
# a facts result: text + citations + explanation (+ optional clarify)
# --------------------------------------------------------------------------
@dataclass
class FactsResult:
    text: str = ""
    citations: list[Citation] = field(default_factory=list)
    explain: str = ""
    pattern: str = ""
    clarify: str = ""
    chips: list[str] = field(default_factory=list)
    live: bool = False

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "citations": [citation_to_dict(c) for c in self.citations],
            "explain": self.explain,
            "pattern": self.pattern,
            "clarify": self.clarify,
            "chips": list(self.chips),
            "live": self.live,
        }


def _clarify(reason: str, options: list[str]) -> FactsResult:
    return FactsResult(clarify=reason, chips=options[:4], pattern="clarify")


# --------------------------------------------------------------------------
# entity resolution incl. two-turn context
# --------------------------------------------------------------------------
def _turn_questions(context: dict | None) -> list[str]:
    if not context:
        return []
    turns = context.get("turns") or [
        {"question": q} for q in (context.get("previous_questions") or context.get("history") or [])
    ]
    out = []
    for t in list(turns)[-2:]:
        if isinstance(t, dict):
            for k in ("subject", "question", "understood_as"):
                if t.get(k):
                    out.append(str(t[k]))
            for d in t.get("answer_docs") or []:
                out.append(str(d))
        else:
            out.append(str(t))
    return out


def _resolve_kind(question: str, cands: dict[str, list[str]], context) -> Resolved:
    r = resolve_name(question, cands)
    if r.kind != "none":
        return r
    if context and _PRONOUN.search(question):
        for prev in reversed(_turn_questions(context)):
            pr = resolve_name(prev, cands)
            if pr.kind == "ok":
                return Resolved("ok", pr.name, pr.score)
    return r


def resolve_entities(question: str, facts: dict, context=None) -> dict[str, Resolved]:
    return {
        "repo": _resolve_kind(question, _repo_candidates(facts), context),
        "project": _resolve_kind(question, _project_candidates(facts), context),
        "space": _resolve_kind(question, _space_candidates(facts), context),
        "sheet": _resolve_kind(question, _sheet_candidates(facts), context),
    }


def _best_entity(ents: dict[str, Resolved]) -> tuple[str, Resolved] | None:
    ok = [(k, r) for k, r in ents.items() if r.kind in ("ok", "ambiguous")]
    if not ok:
        return None
    ok.sort(key=lambda kr: -kr[1].score)
    return ok[0]


def _capability_in(ql: str) -> str | None:
    best, best_len = None, 0
    for cap, terms in CAPABILITY_TERMS.items():
        for t in terms:
            if (
                re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", ql)
                and len(t) > best_len
            ):
                best, best_len = cap, len(t)
    return best


# --------------------------------------------------------------------------
# symbols (shared with the agent's search_code / get_symbol)
# --------------------------------------------------------------------------
def all_symbols(repo: str | None = None) -> list[dict]:
    if repo:
        return factsmod.symbols_for(repo)
    out = []
    for slug in factsmod.analysed_repos():
        out.extend(fd.read_jsonl(fd.path("analysis", slug, "symbols.jsonl")))
    return out


def find_symbols(
    query: str,
    repo: str | None = None,
    language: str | None = None,
    symbol: str | None = None,
    limit=8,
) -> list[dict]:
    """Rank symbols by name/qualified/path/docstring match to ``query``."""
    qtoks = [t for t in _tokens(query) if t not in _STOP]
    want = (symbol or "").lower()
    scored = []
    for s in all_symbols(repo):
        if language and str(s.get("language", "")).lower() != language.lower():
            continue
        name = str(s.get("symbol", "")).lower()
        qual = str(s.get("qualified", "")).lower()
        path = str(s.get("path", "")).lower()
        doc = str(s.get("docstring", "")).lower()
        score = 0.0
        if want:
            if name == want or qual == want or qual.endswith("." + want):
                score += 10
            else:
                r = difflib.SequenceMatcher(None, name, want).ratio()
                if r >= 0.85:
                    score += 6 * r
        for t in qtoks:
            if t == name:
                score += 5
            elif t in name or t in qual:
                score += 2
            elif difflib.SequenceMatcher(None, t, name).ratio() >= 0.85:
                score += 3
            if t in path:
                score += 1
            if t in doc:
                score += 0.5
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], x[1].get("repo", ""), x[1].get("path", "")))
    return [s for _, s in scored[:limit]]


def _clone_roots() -> list[str]:
    roots = []
    if os.environ.get("KF_CLONE_ROOT"):
        roots.append(os.environ["KF_CLONE_ROOT"])
    roots += [fd.path("repos"), fd.path("clones"), fd.path("corpus", "repos")]
    return roots


def symbol_code(platform, tenant: str, sym: dict, accessible: list[str] | None = None) -> str:
    """The symbol's source lines — from an ingested code passage when one
    matches, else the clone on disk, else its signature + docstring."""
    path = str(sym.get("path", ""))
    name = str(sym.get("symbol", ""))
    if platform is not None and tenant:
        try:
            for pas in platform.passages.for_tenant(tenant):
                if pas.coordinate.kind.value != "symbol_line":
                    continue
                loc = pas.coordinate.locator or {}
                if str(loc.get("symbol", "")) == name and str(loc.get("path", "")).endswith(path):
                    if accessible is not None and not (
                        set(platform.passages.acl_of(tenant, pas.id)) & set(accessible)
                    ):
                        continue
                    return pas.text
        except Exception:  # a store without code passages — fall through
            pass
    slug = fd.repo_slug(str(sym.get("repo", "")))
    try:
        s0, s1 = int(sym.get("start_line") or 0), int(sym.get("end_line") or 0)
    except (TypeError, ValueError):
        s0 = s1 = 0
    if s0:
        for root in _clone_roots():
            fp = os.path.join(root, slug, path)
            if os.path.isfile(fp):
                try:
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        lines = f.read().splitlines()
                    return "\n".join(lines[s0 - 1 : max(s0, s1)])
                except OSError:
                    continue
    sig = str(sym.get("signature") or name)
    doc = str(sym.get("docstring") or "").strip()
    return sig + (f'\n    """{doc}"""' if doc else "")


def symbol_citation(sym: dict) -> Citation:
    repo, path, line = str(sym.get("repo", "")), str(sym.get("path", "")), sym.get("start_line")
    url = sym.get("url") or _blob_url(repo, path, line)
    return cite(
        f"{repo}:{path}",
        url,
        f"{repo}:{path}",
        {"path": path, "symbol": sym.get("symbol", ""), "line": line, "repo": repo},
        CoordinateKind.SYMBOL_LINE,
        str(sym.get("signature") or sym.get("symbol") or ""),
    )


# --------------------------------------------------------------------------
# the patterns
# --------------------------------------------------------------------------
_EXAMPLE_Q = re.compile(
    r"(?:examples?|samples?|implementations?) of (?:the |an? )?`?([A-Za-z_][\w.]*)`?"
    r"|how (?:is|was|do we|did we) `?([A-Za-z_][\w.]*)`? implemented"
    r"|show (?:me )?(?:the |an? )?`?([A-Za-z_][\w.]*)`? (?:implementation|code|function|class)"
    r"|show (?:me )?(?:some |any )?`?([A-Za-z_][\w.]*)`? (?:examples?|samples?|usages?)",
    re.I,
)


def _p_examples(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    m = _EXAMPLE_Q.search(q)
    if not m:
        return None
    name = next(g for g in m.groups() if g)
    if name.lower() in _STOP or name.lower() in ("rag", "caching", "auth"):
        return None
    repo = ents["repo"].name if ents["repo"].kind == "ok" else None
    syms = find_symbols(name, repo=repo, symbol=name, limit=12)
    syms = [s for s in syms if s.get("symbol")]
    if not syms:
        return None
    # exact/qualified hits only; up to 3, distinct repos first
    picked, seen = [], set()
    for s in syms:
        key = (s.get("repo"), s.get("path"), s.get("symbol"))
        if key in seen:
            continue
        seen.add(key)
        picked.append(s)
        if len(picked) == 3:
            break
    acc = principal.accessible_acls() if principal is not None else None
    parts = [f"Found {len(picked)} implementation(s) of `{name}`:"]
    cites = []
    for i, s in enumerate(picked, 1):
        code = symbol_code(p, principal.tenant if principal else "", s, acc)
        lang = str(s.get("language") or "").lower()
        cites.append(symbol_citation(s))
        parts.append(
            f"{i}. `{s.get('qualified') or s.get('symbol')}` in {s.get('repo')} — "
            f"{s.get('path')}:{s.get('start_line')} [{i}]\n```{lang}\n{code}\n```"
        )
    return FactsResult(
        "\n".join(parts), cites, f"Matched the symbol `{name}` in symbols.jsonl.", "examples"
    )


def _p_reusable(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    if not re.search(r"\breusable\b|\breuse\b|\bre-usable\b", ql):
        return None
    cap = _capability_in(ql)
    rows = [c for c in caps if (cap is None or c.get("capability") == cap)]
    items, cites = [], []
    for c in rows:
        for r in (c.get("attributes") or {}).get("reusable") or []:
            if isinstance(r, dict):
                sym, path, line = r.get("symbol") or r.get("name"), r.get("path", ""), r.get("line")
            else:
                sym, path, line = str(r), "", None
            if not path and c.get("evidence"):
                ev = c["evidence"][0]
                path, line = ev.get("path", ""), ev.get("line")
            items.append((c["repo"], c.get("capability"), sym, path, line))
    if not items:
        return None
    label = _CAP_LABEL.get(cap, cap) if cap else "any capability"
    parts = [f"Reusable components for {label} ({len(items)}, {as_of_text(None)}):"]
    for i, (repo, capn, sym, path, line) in enumerate(items[:10], 1):
        parts.append(f"• `{sym}` — {repo} ({capn}) {path}{':' + str(line) if line else ''} [{i}]")
        cites.append(
            cite(
                f"{repo}:{path or sym}",
                _blob_url(repo, path, line) if path else _repo_url(repo),
                f"{repo}:{path or sym}",
                {"path": path, "symbol": sym, "line": line, "repo": repo},
                CoordinateKind.SYMBOL_LINE,
                str(sym),
            )
        )
    return FactsResult("\n".join(parts), cites, "Listed capabilities.json reusable[].", "reusable")


def _p_css(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    if not re.search(r"\bcss\b|\bstylesheets?\b|\bstyle sheets?\b", ql):
        return None
    files, cites = [], []
    for c in caps:
        if c.get("capability") != "dashboard_ui":
            continue
        for f in (c.get("attributes") or {}).get("css_files") or []:
            files.append((c["repo"], str(f)))
    if not files:
        return FactsResult(
            f"No CSS templates are recorded in any repository ({as_of_text(None)}) [1].",
            [
                cite(
                    "capabilities.json",
                    "",
                    "data/capabilities.json",
                    {"path": "data/capabilities.json"},
                )
            ],
            "dashboard_ui.css_files is empty across capabilities.json.",
            "css",
        )
    parts = [
        f"Yes — {len(files)} CSS file(s) across {len({r for r, _ in files})} repositories "
        f"({as_of_text(None)}):"
    ]
    for i, (repo, f) in enumerate(files[:12], 1):
        parts.append(f"• {f} — {repo} [{i}]")
        cites.append(
            cite(
                f"{repo}:{f}",
                _blob_url(repo, f),
                f"{repo}:{f}",
                {"path": f, "repo": repo},
                CoordinateKind.SYMBOL_LINE,
                f,
            )
        )
    return FactsResult("\n".join(parts), cites, "Listed dashboard_ui.css_files.", "css")


def _p_licences(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    if not re.search(r"licen[cs]e|dependenc|librar|packages?\b|third[- ]party", ql):
        return None
    r = ents["repo"]
    if r.kind == "ambiguous":
        return _clarify("Which repository do you mean?", r.options)
    if r.kind != "ok":
        return None
    deps = factsmod.load_dependencies().get(r.name) or []
    if not deps:
        return None
    by_lic: dict[str, list[dict]] = {}
    for d in deps:
        by_lic.setdefault(str(d.get("licence") or d.get("license") or "unknown"), []).append(d)
    manifests = sorted({str(d.get("manifest_path") or "") for d in deps if d.get("manifest_path")})
    cites = [
        cite(
            f"{r.name}:{m}",
            _blob_url(r.name, m),
            f"{r.name}:{m}",
            {"path": m, "repo": r.name},
            CoordinateKind.SYMBOL_LINE,
            m,
        )
        for m in manifests
    ] or [cite(r.name, _repo_url(r.name), r.name, {"repo": r.name})]
    lic_filter = None
    m = re.search(r"\b(mit|apache|bsd|gpl|lgpl|mpl|agpl)\b", ql)
    if m:
        lic_filter = m.group(1)
    parts = [
        f"{r.name} declares {len(deps)} licensed libraries across {len(by_lic)} licences "
        f"({as_of_text(facts['repositories'].get(r.name))}) [1]:"
    ]
    for lic, rows in sorted(by_lic.items(), key=lambda kv: -len(kv[1])):
        if lic_filter and lic_filter not in lic.lower():
            continue
        names = ", ".join(
            f"{d.get('name')} {d.get('version') or ''}".strip()
            + (f" ({d.get('category')})" if d.get("category") else "")
            for d in rows[:8]
        )
        more = f" +{len(rows) - 8} more" if len(rows) > 8 else ""
        parts.append(f"• {lic} ({len(rows)}): {names}{more} [1]")
    return FactsResult("\n".join(parts), cites, "Grouped dependencies.json by licence.", "licences")


def _readiness_pct(score, present, checklist) -> int:
    """The readiness score as a percentage. Detectors may record it as a
    fraction (0.8) or a percentage (80); with no score at all the checklist
    ratio stands in."""
    if score is None or score == "":
        return round(100 * len(present) / len(checklist)) if checklist else 0
    v = float(score)
    return round(v * 100) if 0 <= v <= 1 else round(v)


def _p_enterprise(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    if not re.search(
        r"enterprise|production[- ]?ready|production[- ]grade|scalable|ready for production|"
        r"scal(e|ability)",
        ql,
    ):
        return None
    r = ents["repo"]
    if r.kind == "ambiguous":
        return _clarify("Which repository do you mean?", r.options)
    if r.kind != "ok":
        return None
    row = next(
        (
            c
            for c in caps
            if c.get("repo") == r.name and c.get("capability") == "enterprise_readiness"
        ),
        None,
    )
    if not row:
        return None
    attrs = row.get("attributes") or {}
    score = attrs.get("score")
    checklist = attrs.get("checklist") or []
    present = [c for c in checklist if c.get("present")]
    missing = [c for c in checklist if not c.get("present")]
    pct = _readiness_pct(score, present, checklist)
    verdict = "largely ready" if pct >= 70 else ("partly ready" if pct >= 40 else "not yet ready")
    cites = [cite(r.name, _repo_url(r.name), r.name, {"repo": r.name, "score": score})]
    for c in present[:6]:
        ev = c.get("evidence") or {}
        path = ev.get("path") if isinstance(ev, dict) else str(ev)
        if path:
            cites.append(
                cite(
                    f"{r.name}:{path}",
                    _blob_url(r.name, path, ev.get("line") if isinstance(ev, dict) else None),
                    f"{r.name}:{path}",
                    {"path": path, "repo": r.name, "item": c.get("item")},
                    CoordinateKind.SYMBOL_LINE,
                    str(c.get("item")),
                )
            )
    parts = [
        f"{r.name} scores {_n(pct)}% on enterprise readiness — "
        f"{verdict} "
        f"({len(present)}/{len(checklist)} checklist items present, "
        f"{as_of_text(facts['repositories'].get(r.name))}) [1]."
    ]
    if present:
        parts.append("Present: " + ", ".join(str(c.get("item")) for c in present) + " [1].")
    if missing:
        parts.append("Missing: " + ", ".join(str(c.get("item")) for c in missing) + " [1].")
    return FactsResult(
        " ".join(parts), cites, "Read enterprise_readiness score + checklist.", "enterprise"
    )


def _p_architecture(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    tech = bool(
        re.search(
            r"retrieval (?:technique|method|strateg|approach)|what retrieval|"
            r"retrieval (?:was|is|were|are) used|retrieval used|how (?:does|do) \S+ retrieve",
            ql,
        )
    )
    arch = bool(
        re.search(
            r"architect|how (?:is|was) (?:it|\S+) (?:built|designed|structured)|design of", ql
        )
    )
    if not (tech or arch):
        return None
    r = ents["repo"]
    if r.kind == "ambiguous":
        return _clarify("Which repository do you mean?", r.options)
    if r.kind != "ok":
        return None
    slug = fd.repo_slug(r.name)
    doc = factsmod.architecture_for(r.name)
    rag = next((c for c in caps if c.get("repo") == r.name and c.get("capability") == "rag"), None)
    techniques = list(((rag or {}).get("attributes") or {}).get("retrieval_techniques") or [])
    if not techniques and doc:
        m = re.search(r"retrieval[_ ]techniques?\s*[:\-]\s*([^\n]+)", doc, re.I)
        if m:
            techniques = [
                t.strip(" `*") for t in re.split(r",|;|\band\b", m.group(1)) if t.strip(" `*")
            ]
    if not doc and not techniques:
        return None
    arch_cite = cite(
        f"{r.name} — architecture",
        _repo_url(r.name),
        f"analysis/{slug}/architecture.md",
        {"path": f"analysis/{slug}/architecture.md", "repo": r.name},
    )
    when = as_of_text(facts["repositories"].get(r.name))
    if tech:
        if not techniques:
            return FactsResult(
                f"The architecture document for {r.name} records no retrieval techniques "
                f"({when}) [1].",
                [arch_cite],
                "No retrieval_techniques listed.",
                "retrieval_techniques",
            )
        cites = [arch_cite]
        if rag and rag.get("evidence"):
            ev = rag["evidence"][0]
            cites.append(
                cite(
                    f"{r.name}:{ev.get('path')}",
                    _blob_url(r.name, ev.get("path", ""), ev.get("line")),
                    f"{r.name}:{ev.get('path')}",
                    {"path": ev.get("path"), "line": ev.get("line"), "repo": r.name},
                    CoordinateKind.SYMBOL_LINE,
                    str(ev.get("snippet", "")),
                )
            )
        tail = " [2]" if len(cites) > 1 else ""
        return FactsResult(
            f"{r.name} uses {len(techniques)} retrieval technique(s) ({when}): "
            + ", ".join(techniques)
            + f" [1]{tail}.",
            cites,
            "Read rag.attributes.retrieval_techniques + architecture.md.",
            "retrieval_techniques",
        )
    # architecture summary: the first prose paragraph(s) of architecture.md
    paras = [
        pp.strip()
        for pp in re.split(r"\n\s*\n", doc)
        if pp.strip() and not pp.strip().startswith("#")
    ]
    summary = " ".join(paras[:2])[:900] if paras else "No architecture summary is recorded."
    text = f"Architecture of {r.name} ({when}): {summary} [1]"
    if techniques:
        text += f" Retrieval techniques: {', '.join(techniques)} [1]."
    return FactsResult(text, [arch_cite], "Read analysis/<repo>/architecture.md.", "architecture")


def _p_who(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    if not re.search(
        r"\bwho (?:worked|works|owns|owned|is the owner|contributed|contributes|maintains|"
        r"is assigned|has|built|wrote)\b|\bowner\b|\bcontributors?\b|\bassignees?\b",
        ql,
    ):
        return None
    best = _best_entity(ents)
    if not best:
        return None
    kind, r = best
    if r.kind == "ambiguous":
        return _clarify(f"Which {kind} do you mean?", r.options)
    if kind == "repo":
        block = facts["repositories"].get(r.name) or {}
        contribs = list(block.get("contributors") or [])
        if not contribs and not block.get("contributors_count"):
            return None
        contribs.sort(key=lambda c: -int(c.get("contributions") or 0))
        top = ", ".join(f"{c.get('login')} ({_n(c.get('contributions', 0))})" for c in contribs[:5])
        n = block.get("contributors_count") or len(contribs)
        text = (
            f"{r.name} has {_n(n)} contributors ({as_of_text(block)}); the most active: {top} [1]."
            if top
            else f"{r.name} has {_n(n)} contributors ({as_of_text(block)}) [1]."
        )
        return FactsResult(
            text,
            [cite(r.name, _repo_url(r.name), r.name, {"repo": r.name})],
            "Read repositories.<repo>.contributors.",
            "who",
        )
    if kind == "project":
        block = facts["jira_projects"].get(r.name) or {}
        by = (block.get("issues") or {}).get("by_assignee") or {}
        if not by:
            return None
        items = sorted(by.items(), key=lambda kv: -int(kv[1] or 0))
        text = (
            f"Jira project {r.name} issues by assignee ({as_of_text(block)}): "
            + ", ".join(f"{k} ({_n(v)})" for k, v in items[:8])
            + " [1]."
        )
        return FactsResult(
            text,
            [
                cite(
                    f"Jira {r.name}",
                    _jira_url(r.name, block),
                    f"jira:{r.name}",
                    {"project": r.name},
                )
            ],
            "Read jira_projects.<key>.by_assignee.",
            "who",
        )
    return None


_REPO_FACT = re.compile(
    r"\b(commits?|contributors?|pull requests?|prs?|merged|languages?|deployments?|deploys?|"
    r"releases?|workflows?|pushed|last push|description|what (?:is|does)|primary language)\b"
)


def _p_repo_facts(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    r = ents["repo"]
    if r.kind == "ambiguous" and _REPO_FACT.search(ql):
        return _clarify("Which repository do you mean?", r.options)
    if r.kind != "ok":
        return None
    block = facts["repositories"].get(r.name) or {}
    if not block:
        return None
    when = as_of_text(block)
    c = cite(r.name, _repo_url(r.name), r.name, {"repo": r.name})
    prs = block.get("pull_requests") or {}
    parts = []
    if re.search(r"\bcommits?\b", ql):
        parts.append(
            f"{r.name} has {_n((block.get('commits') or {}).get('total', 0))} commits ({when}) [1]."
        )
    if re.search(r"\bcontributors?\b", ql):
        n = block.get("contributors_count") or len(block.get("contributors") or [])
        parts.append(f"{r.name} has {_n(n)} contributors ({when}) [1].")
    if re.search(r"\bmerged\b", ql):
        parts.append(f"{r.name} has {_n(prs.get('merged', 0))} merged pull requests ({when}) [1].")
    elif re.search(r"\bopen (?:prs?|pull requests?)\b", ql):
        parts.append(f"{r.name} has {_n(prs.get('open', 0))} open pull requests ({when}) [1].")
    elif re.search(r"\b(?:prs?|pull requests?)\b", ql):
        parts.append(
            f"{r.name} has {_n(prs.get('total', 0))} pull requests — "
            f"{_n(prs.get('open', 0))} open, "
            f"{_n(prs.get('merged', 0))} merged, {_n(prs.get('closed', 0))} closed ({when}) [1]."
        )
    if re.search(r"\bprimary language\b", ql):
        parts.append(
            f"The primary language of {r.name} is "
            f"{block.get('primary_language') or 'unknown'} ({when}) [1]."
        )
    elif re.search(r"\blanguages?\b", ql):
        langs = block.get("languages") or {}
        items = sorted(langs.items(), key=lambda kv: -float((kv[1] or {}).get("share", 0) or 0))
        listed = ", ".join(
            f"{k} ({round(float((v or {}).get('share', 0) or 0) * 100)}%)" for k, v in items
        )
        parts.append(
            f"{r.name} uses {_n(len(langs))} languages ({when}): {listed or 'none recorded'} [1]."
        )
    if re.search(r"\bdeploy", ql):
        d = block.get("deployments") or {}
        latest = d.get("latest") or {}
        envs = ", ".join(str(e) for e in d.get("environments") or []) or "no environments"
        tail = (
            f"; latest: {latest.get('environment', '')} {latest.get('created_at', '')}".rstrip(": ")
            if latest
            else ""
        )
        parts.append(
            f"{r.name} has {_n(d.get('count', 0))} deployments across {envs}{tail} ({when}) [1]."
        )
    if re.search(r"\breleases?\b", ql):
        rel = block.get("releases") or []
        latest = rel[0] if rel else {}
        name = latest.get("tag_name") or latest.get("name") if isinstance(latest, dict) else latest
        parts.append(
            f"{r.name} has {_n(len(rel))} releases"
            + (f"; latest {name}" if name else "")
            + f" ({when}) [1]."
        )
    if re.search(r"\bworkflows?\b", ql):
        wf = block.get("workflows") or []
        names = ", ".join((w.get("name") if isinstance(w, dict) else str(w)) for w in wf[:8])
        parts.append(
            f"{r.name} has {_n(len(wf))} workflows"
            + (f": {names}" if names else "")
            + f" ({when}) [1]."
        )
    if re.search(r"\b(?:pushed|last push)\b", ql):
        parts.append(
            f"{r.name} was last pushed at {block.get('pushed_at') or 'unknown'} ({when}) [1]."
        )
    if re.search(r"\b(?:description|what (?:is|does))\b", ql) and block.get("description"):
        parts.append(f"{r.name}: {block.get('description')} ({when}) [1].")
    if not parts:
        return None
    return FactsResult(
        " ".join(parts), [c], "Read repositories.<repo> from facts.json.", "repo_facts"
    )


_OPEN_LIKE = {
    "done",
    "closed",
    "resolved",
    "cancelled",
    "canceled",
    "won't do",
    "wont do",
    "released",
}


def _live_issue_field(issue: dict, name: str) -> str:
    f = issue.get("fields") if isinstance(issue.get("fields"), dict) else issue
    v = f.get(name)
    if isinstance(v, dict):
        return str(v.get("name") or v.get("displayName") or v.get("value") or "")
    return str(v or "")


def _jira_value_in(ql: str, issues: dict):
    """``(field, value, count)`` when the question names a value from one of the
    breakdown tables ("critical", "in progress", "bug", "alice"); the longest
    match wins. ``open`` is handled by the status-category logic, not here."""
    best = None
    for fld in ("priority", "status", "type", "assignee"):
        for value, n in (issues.get(f"by_{fld}") or {}).items():
            v = str(value).lower().strip()
            if not v or v == "open":
                continue
            if re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", ql) and (
                best is None or len(v) > len(best[1])
            ):
                best = (fld, v, int(n or 0))
    return best


def _p_jira(p, principal, q, ql, facts, caps, ents, context, live_jql=None) -> FactsResult | None:
    r = ents["project"]
    jira_words = re.search(
        r"\b(bugs?|issues?|tickets?|stories|story|tasks?|epics?|sprint|backlog|jira)\b", ql
    )
    if r.kind == "ambiguous" and jira_words:
        return _clarify("Which Jira project do you mean?", r.options)
    if r.kind != "ok":
        if jira_words and len(facts["jira_projects"]) == 1:
            r = Resolved("ok", next(iter(facts["jira_projects"])), 0.8)
        else:
            return None
    if not jira_words and not re.search(
        r"\bhow many\b|\bopen\b|\bby (status|priority|assignee|type)\b", ql
    ):
        return None
    block = facts["jira_projects"].get(r.name) or {}
    issues = block.get("issues") or {}
    when = as_of_text(block)
    c = cite(f"Jira {r.name}", _jira_url(r.name, block), f"jira:{r.name}", {"project": r.name})
    if re.search(r"\bsprint\b", ql):
        sp = block.get("sprint")
        text = (
            f"Jira project {r.name}: the current sprint is {sp.get('name')} "
            f"({sp.get('state')}) ({when}) [1]."
            if isinstance(sp, dict) and sp
            else f"Jira project {r.name} has no active sprint ({when}) [1]."
        )
        return FactsResult(text, [c], "Read jira_projects.<key>.sprint.", "jira")
    want_open = bool(re.search(r"\bopen\b|\bunresolved\b|\boutstanding\b", ql))
    want_bugs = bool(re.search(r"\bbugs?\b", ql))
    wants_now = bool(
        re.search(r"\b(now|current|currently|today|right now|at the moment|open)\b", ql)
    )
    group = None
    m = re.search(r"\bby (status|priority|assignee|type|issue type)\b", ql)
    if m:
        group = {
            "status": "by_status",
            "priority": "by_priority",
            "assignee": "by_assignee",
            "type": "by_type",
            "issue type": "by_type",
        }[m.group(1)]
    live = live_jql or getattr(p, "live_jql", None)
    if wants_now and live is not None and _age_minutes(block) > 60:
        jql = f"project = {r.name}"
        if want_bugs:
            jql += " AND issuetype = Bug"
        if want_open:
            jql += " AND statusCategory != Done"
        try:
            raw = live(jql, ["status", "priority", "assignee", "issuetype"])
            found = raw.get("issues", []) if isinstance(raw, dict) else list(raw or [])
            total = raw.get("total", len(found)) if isinstance(raw, dict) else len(found)
            now = (
                "as of " + _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M UTC") + " (live JQL)"
            )
            lc = cite(
                f"Jira {r.name} (live)",
                _jira_url(r.name, block),
                f"jira:{r.name}",
                {"project": r.name, "jql": jql, "live": True},
            )
            noun = ("open " if want_open else "") + ("bugs" if want_bugs else "issues")
            text = f"Jira project {r.name} has {_n(total)} {noun} {now} [1]."
            if group:
                fld = {
                    "by_status": "status",
                    "by_priority": "priority",
                    "by_assignee": "assignee",
                    "by_type": "issuetype",
                }[group]
                counts: dict[str, int] = {}
                for it in found:
                    k = _live_issue_field(it, fld) or "unassigned"
                    counts[k] = counts.get(k, 0) + 1
                text += (
                    " "
                    + group.replace("by_", "By ").capitalize()
                    + ": "
                    + ", ".join(
                        f"{k} ({_n(v)})" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
                    )
                    + " [1]."
                )
            return FactsResult(
                text,
                [lc],
                f"Facts were {int(_age_minutes(block))} min old; ran live JQL `{jql}`.",
                "jira",
                live=True,
            )
        except Exception as e:  # the live source failed — say so, answer from facts
            when = f"{when}; live JQL failed: {str(e)[:80]}"
    by_status = issues.get("by_status") or {}
    by_type = issues.get("by_type") or {}
    if not group:
        hit = _jira_value_in(ql, issues)
        if hit:
            field, value, n = hit
            noun = "bugs" if want_bugs else "issues"
            text = f"Jira project {r.name} has {_n(n)} {noun} with {field} {value} ({when}) [1]."
            if want_bugs:
                text += f" The {field} breakdown covers every issue type, not bugs alone."
            return FactsResult(
                text, [c], f"Read jira_projects.<key>.issues.by_{field}[{value!r}].", "jira"
            )
    if group:
        table = issues.get(group) or {}
        if not table:
            return None
        items = sorted(table.items(), key=lambda kv: -int(kv[1] or 0))
        label = group.replace("by_", "by ")
        text = (
            f"Jira project {r.name} issues {label} ({when}): "
            + ", ".join(f"{k} ({_n(v)})" for k, v in items)
            + " [1]."
        )
        return FactsResult(text, [c], f"Read jira_projects.<key>.issues.{group}.", "jira")
    if want_bugs:
        n = by_type.get("Bug", by_type.get("bug", 0))
        if want_open:
            text = (
                f"Jira project {r.name} has {_n(n)} bugs in total ({when}); open counts by status: "
                + ", ".join(
                    f"{k} ({_n(v)})"
                    for k, v in by_status.items()
                    if str(k).lower() not in _OPEN_LIKE
                )
                + " [1]."
            )
        else:
            text = f"Jira project {r.name} has {_n(n)} bugs ({when}) [1]."
        return FactsResult(text, [c], "Read jira_projects.<key>.issues.by_type.", "jira")
    if want_open:
        n = sum(int(v or 0) for k, v in by_status.items() if str(k).lower() not in _OPEN_LIKE)
        text = (
            f"Jira project {r.name} has {_n(n)} open issues ({when}): "
            + ", ".join(
                f"{k} ({_n(v)})" for k, v in by_status.items() if str(k).lower() not in _OPEN_LIKE
            )
            + " [1]."
        )
        return FactsResult(text, [c], "Summed non-done statuses in by_status.", "jira")
    text = f"Jira project {r.name} has {_n(issues.get('total', 0))} issues ({when})"
    if by_status:
        text += "; by status: " + ", ".join(f"{k} ({_n(v)})" for k, v in by_status.items())
    return FactsResult(text + " [1].", [c], "Read jira_projects.<key>.issues.total.", "jira")


def _p_confluence(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    r = ents["space"]
    if r.kind == "ambiguous" and re.search(r"\bpages?\b|\bspace\b|\bconfluence\b", ql):
        return _clarify("Which Confluence space do you mean?", r.options)
    if r.kind != "ok" or not re.search(r"\bpages?\b|\bspace\b|\bconfluence\b|\bupdated\b", ql):
        return None
    block = facts["confluence_spaces"].get(r.name) or {}
    text = (
        f"Confluence space {r.name} has {_n(block.get('pages', 0))} pages; last updated "
        f"{block.get('last_updated') or 'unknown'} ({as_of_text(block)}) [1]."
    )
    return FactsResult(
        text,
        [
            cite(
                f"Confluence {r.name}",
                _space_url(r.name, block),
                f"confluence:{r.name}",
                {"space": r.name},
            )
        ],
        "Read confluence_spaces.<key>.",
        "confluence",
    )


def _agg_of(ql: str) -> str | None:
    found = None
    for agg, words in _AGG_WORDS.items():
        for w in words:
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", ql):
                if found is None or len(w) > len(found[1]):
                    found = (agg, w)
    return found[0] if found else None


def _resolve_column(phrase: str, columns: list[dict]) -> str | None:
    if not phrase.strip():
        return None
    cands = {
        c["name"]: [c["name"].replace("_", " "), c["name"].lower()]
        for c in columns
        if c.get("name")
    }
    r = resolve_name(phrase, cands, floor=0.75)
    return r.name if r.kind == "ok" else None


def _p_sheet(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    r = ents["sheet"]
    if r.kind == "ambiguous":
        return _clarify("Which sheet do you mean?", [o.replace("::", " · ") for o in r.options])
    if r.kind != "ok":
        return None
    agg = _agg_of(ql)
    if not agg:
        return None
    doc_id, sheet = r.name.split("::", 1)
    entry = next((t for t in facts["tables"] if _sheet_key(t) == r.name), {})
    columns = entry.get("columns") or []
    if not columns:
        try:
            columns = tables.describe_table(doc_id, sheet)["columns"]
        except tables.TableQueryError:
            return None
    title = f"{entry.get('doc_title') or doc_id} · {sheet}"
    # column phrase: the words after the aggregate word up to "in/of/for/from/on"
    col = None
    m = re.search(
        r"(?:how many|count of|number of|total of|total|sum of|sum|average|avg|mean|max(?:imum)?|"
        r"min(?:imum)?|highest|lowest|largest|smallest)\s+(?:the\s+)?(.+?)(?:\s+(?:in|of|for|from|on|per|by)\b|\?|$)",
        ql,
    )
    if m:
        col = _resolve_column(m.group(1), columns)
    if col is None:
        # any column named anywhere in the question
        for c in columns:
            if re.search(
                r"(?<![a-z0-9])" + re.escape(c["name"].lower().replace("_", " ")) + r"(?![a-z0-9])",
                ql.replace("_", " "),
            ):
                col = c["name"]
                break
    group = None
    gm = re.search(r"\b(?:by|per|grouped by)\s+(?:the\s+)?([a-z_ ]+?)(?:\?|$| in\b| of\b)", ql)
    if gm:
        group = _resolve_column(gm.group(1), columns)
    if agg != "count" and col is None:
        return None
    qcol = f'"{col}"' if col else "*"
    fn = {"count": "COUNT", "sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN"}[agg]
    if group:
        sql = (
            f'SELECT "{group}", {fn}({qcol}) AS value FROM t GROUP BY "{group}" ORDER BY value DESC'
        )
    else:
        sql = f"SELECT {fn}({qcol}) AS value, COUNT(*) AS n FROM t"
    try:
        res = tables.run_table_query(doc_id, sheet, sql)
    except tables.TableQueryError:
        return None
    when = as_of_text(entry)
    c = citation_from_dict(res["citation"])
    label = {
        "count": "count",
        "sum": "total",
        "avg": "average",
        "max": "maximum",
        "min": "minimum",
    }[agg]
    if group:
        listed = ", ".join(f"{row[0]} ({_n(row[1])})" for row in res["rows"][:20])
        text = (
            f"The {label}{' of ' + col if col else ''} in {title} by {group} ({when}): "
            f"{listed} [1]."
        )
    else:
        row = res["rows"][0] if res["rows"] else [0, 0]
        if agg == "count":
            text = (
                f"{title} has {_n(row[0])} rows"
                + (f" with a value in {col}" if col else "")
                + f" ({when}) [1]."
            )
        else:
            text = (
                f"The {label} of {col} in {title} is {_n(row[0])} across {_n(row[1])} rows "
                f"({when}) [1]."
            )
    return FactsResult(text, [c], f"Ran `{res['sql']}` on the sheet.", "sheet")


def _p_capability(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    cap = _capability_in(ql)
    if cap is None or not (_COUNT_Q.search(ql) or re.search(r"\bwhich repos", ql)):
        return None
    rows = [
        c for c in caps if c.get("capability") == cap and float(c.get("confidence") or 0) >= 0.5
    ]
    total_repos = len(facts.get("repositories") or {}) or len({c.get("repo") for c in caps})
    label = _CAP_LABEL.get(cap, cap)
    when = as_of_text(facts.get("repositories", {}).get(rows[0]["repo"]) if rows else None)
    if not rows:
        return FactsResult(
            f"No — none of the {_n(total_repos)} repositories implement {label} ({when}) [1].",
            [
                cite(
                    "capabilities.json",
                    "",
                    "data/capabilities.json",
                    {"path": "data/capabilities.json"},
                )
            ],
            f"No capabilities.json row for {cap} at confidence ≥ 0.5.",
            "capability",
        )
    cites, parts = [], []
    parts.append(f"Yes — {len(rows)} of {_n(total_repos)} repositories implement {label} ({when}):")
    for i, c in enumerate(sorted(rows, key=lambda c: -float(c.get("confidence") or 0)), 1):
        ev = (c.get("evidence") or [{}])[0]
        attrs = c.get("attributes") or {}
        extra = ""
        if cap == "rag" and attrs.get("retrieval_techniques"):
            extra = " — " + ", ".join(attrs["retrieval_techniques"])
        parts.append(
            f"• {c['repo']} (confidence {float(c.get('confidence') or 0):.2f}){extra} [{i}]"
        )
        path = ev.get("path", "")
        cites.append(
            cite(
                f"{c['repo']}:{path}" if path else c["repo"],
                _blob_url(c["repo"], path, ev.get("line")) if path else _repo_url(c["repo"]),
                f"{c['repo']}:{path}" if path else c["repo"],
                {"path": path, "line": ev.get("line"), "repo": c["repo"], "capability": cap},
                CoordinateKind.SYMBOL_LINE if path else CoordinateKind.PAGE_PARAGRAPH,
                str(ev.get("snippet", "")),
            )
        )
    return FactsResult(
        "\n".join(parts), cites, f"Counted capabilities.json rows for {cap}.", "capability"
    )


def _p_inventory(p, principal, q, ql, facts, caps, ents, context) -> FactsResult | None:
    if not re.search(r"\bhow many\b|\bnumber of\b|\bcount\b", ql):
        return None
    if re.search(r"\b(repos|repositories)\b", ql):
        repos = facts.get("repositories") or {}
        blk = next(iter(repos.values()), None)
        text = (
            f"The fabric covers {_n(len(repos))} repositories ({as_of_text(blk)}): "
            + ", ".join(sorted(repos))
            + " [1]."
        )
        return FactsResult(
            text,
            [cite("facts.json", "", "data/facts.json", {"path": "data/facts.json"})],
            "Counted repositories in facts.json.",
            "inventory",
        )
    if re.search(r"\b(documents?|docs|files|pages)\b", ql):
        d = facts.get("documents") or {}
        if not d.get("total"):
            return None
        by = d.get("by_type") if re.search(r"\btype\b", ql) else d.get("by_area")
        text = f"The fabric holds {_n(d.get('total', 0))} documents ({as_of_text(d)})"
        if by:
            text += (
                "; "
                + ("by type: " if re.search(r"\btype\b", ql) else "by area: ")
                + ", ".join(
                    f"{k} ({_n(v)})" for k, v in sorted(by.items(), key=lambda kv: -int(kv[1] or 0))
                )
            )
        return FactsResult(
            text + " [1].",
            [
                cite(
                    "facts.json",
                    "",
                    "data/facts.json",
                    {"path": "data/facts.json", "key": "documents"},
                )
            ],
            "Read documents counts in facts.json.",
            "inventory",
        )
    return None


_PATTERNS = [
    _p_examples,
    _p_reusable,
    _p_css,
    _p_licences,
    _p_enterprise,
    _p_architecture,
    _p_who,
    _p_repo_facts,
    _p_jira,
    _p_confluence,
    _p_sheet,
    _p_capability,
    _p_inventory,
]


def analyse(
    platform, principal, question: str, context=None, *, live_jql=None
) -> FactsResult | None:
    """Run the pattern ladder; a ``FactsResult`` (answer or clarify) or None."""
    q = (question or "").strip()
    if not q:
        return None
    ql = q.lower()
    facts = factsmod.load_facts()
    caps = factsmod.load_capabilities()
    has_any = any(
        [
            facts.get("repositories"),
            facts.get("jira_projects"),
            facts.get("confluence_spaces"),
            facts.get("tables"),
            caps,
            (facts.get("documents") or {}).get("total"),
            factsmod.analysed_repos(),
        ]
    )
    if not has_any:
        return None
    ents = resolve_entities(q, facts, context)
    for fn in _PATTERNS:
        if fn is _p_jira:
            res = fn(platform, principal, q, ql, facts, caps, ents, context, live_jql=live_jql)
        else:
            res = fn(platform, principal, q, ql, facts, caps, ents, context)
        if res is not None:
            return res
    return None


def sources_summary(facts: dict | None = None) -> str:
    """``14 repositories, Jira project QZ, Confluence space ENG and 612 documents``."""
    f = facts or factsmod.load_facts()
    parts = [f"{_n(len(f.get('repositories') or {}))} repositories"]
    pj = sorted(f.get("jira_projects") or {})
    if pj:
        parts.append(("Jira project " if len(pj) == 1 else "Jira projects ") + ", ".join(pj))
    sp = sorted(f.get("confluence_spaces") or {})
    if sp:
        parts.append(
            ("Confluence space " if len(sp) == 1 else "Confluence spaces ") + ", ".join(sp)
        )
    parts.append(f"{_n((f.get('documents') or {}).get('total', 0))} documents")
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# --------------------------------------------------------------------------
# the service hook
# --------------------------------------------------------------------------
def try_answer(platform, principal, question: str, context=None, *, live_jql=None) -> Answer | None:
    """Answer at Level 0 from facts, or None so the prose path runs. Policy and
    rate limit are checked here exactly as the prose path checks them."""
    res = analyse(platform, principal, question, context, live_jql=live_jql)
    if res is None:
        return None
    p = platform
    tenant = principal.tenant
    if p.policy.check(principal, "ask", {}).decision.value == "deny":
        return None  # the prose path reports the denial
    if not p.policy.rate_check(tenant, principal.subject):
        return None
    trace_id = new_id("traj_")
    from ..stores import versioning

    dsv = versioning.current_dataset(p, tenant)
    with p.telemetry.span(
        "answer",
        {
            "tenant": tenant,
            "trace_id": trace_id,
            "stage": "answer",
            "subject": principal.subject,
            "roles": principal.roles,
            "lang": "en",
            "persona": personas.persona_for(principal.designation),
            "designation": principal.designation or "",
            "scope": "restricted" if "restricted" in (principal.scopes or []) else "public",
            "context_resolved": 1 if (context and _PRONOUN.search(question)) else 0,
        },
    ) as span:
        if res.clarify:
            why = {
                "level_name": "clarify",
                "explain": res.clarify,
                "reasons": [{"code": "facts_ambiguous"}],
            }
            span.set(
                kind="clarify",
                level="clarify",
                tier="none",
                citations_count=0,
                why=why,
                model_name=model_for_tier("none"),
                complexity="simple",
                dataset_version=dsv,
            )
            p.audit.write(
                tenant,
                principal.subject,
                principal.agent,
                "ask",
                "answer",
                "clarify:facts",
                trace_id,
                now_ms(),
            )
            return Answer(
                AnswerKind.CLARIFY,
                "",
                [],
                0.0,
                trace_id,
                0.0,
                0,
                "none",
                grounding_score=0.0,
                clarify_back=res.clarify,
                tenant=tenant,
                level=0,
                why=why,
                lang="en",
                model_name=model_for_tier("none"),
                suggestions=res.chips,
                complexity="simple",
                dataset_version=dsv,
            )
        why = {
            "level_name": "facts",
            "explain": res.explain,
            "reasons": [{"code": "facts", "detail": res.pattern, "signal": True}],
            "signals": {
                "retrieval": 1.0,
                "semantic": 1.0,
                "coverage": 1.0,
                "agreement": 1.0,
                "resolvable": 1.0,
            },
            "retrieved": len(res.citations),
            "complexity": "simple",
            "model_name": model_for_tier("none"),
            "pattern": res.pattern,
            "live": res.live,
        }
        span.set(
            kind="answer",
            level="facts",
            tier="none",
            citations_count=len(res.citations),
            sources=[c.document_title for c in res.citations],
            why=why,
            grounding=1.0,
            model_name=model_for_tier("none"),
            complexity="simple",
            dataset_version=dsv,
        )
        p.audit.write(
            tenant,
            principal.subject,
            principal.agent,
            "ask",
            "answer",
            "answered:facts",
            trace_id,
            now_ms(),
        )
        return Answer(
            AnswerKind.ANSWER,
            res.text,
            res.citations,
            1.0,
            trace_id,
            0.0,
            0,
            "none",
            grounding_score=1.0,
            tenant=tenant,
            level=0,
            why=why,
            lang="en",
            model_name=model_for_tier("none"),
            complexity="simple",
            dataset_version=dsv,
        )
