"""Read-side views over the ``fabric-data`` files (T47/T48).

One module turns the analysis outputs other tracks write — ``data/facts.json``,
``data/capabilities.json``, ``data/dependencies.json``, ``analysis/<repo>/*``,
``tables/<doc>/<sheet>.sqlite``, ``images/<doc>/<name>.json`` — into the
payloads the Curator Repositories / Tables / Insights panels, the Admin
Sources cards, the Workspace corpus strip and the MCP facts tools all show.
Because every surface calls the same function, the number an MCP caller gets
from ``query_facts`` is the number the Curator sees on screen, by construction.

Every reader tolerates a missing file (an empty list / zero, never an
exception) so a fabric that has not run the analysis workflows yet renders
honest empty panels rather than a broken page. Nothing here writes.
"""

from __future__ import annotations

import os
import re
import sqlite3

from . import fabric_data as fd

__all__ = [
    "facts",
    "capabilities",
    "dependencies",
    "repositories",
    "repository",
    "recent_activity",
    "tables",
    "table_sample",
    "insights",
    "source_counts",
    "corpus_tiles",
    "image_description",
    "query_facts",
    "citation",
    "coverage_matrix",
]

_SAMPLE_ROWS = 5
_ACTIVITY_MAX = 10


# --------------------------------------------------------------------------
# raw files
# --------------------------------------------------------------------------
def facts() -> dict:
    """``data/facts.json`` or an empty skeleton."""
    f = fd.read_json(fd.data_path("facts.json"), {}) or {}
    return f if isinstance(f, dict) else {}


def coverage_matrix() -> dict:
    """T83 — the audience coverage matrix the Admin heatmap renders. Served from
    the generated ``data/coverage.json`` when present; otherwise computed once
    over the self-contained coverage corpus (``eval/coverage.py``) and written
    there so later reads are cheap. Degrades to an empty matrix with an ``error``
    if the eval package cannot run in this environment."""
    rep = fd.read_json(fd.data_path("coverage.json"))
    if isinstance(rep, dict) and rep.get("matrix"):
        return rep
    try:
        from eval import coverage as _cov

        rep = _cov.run()
        try:
            fd.write_json(fd.data_path("coverage.json", mkdir=True), rep)
        except OSError:
            pass
        return rep
    except Exception as e:  # noqa: BLE001 — surface, never crash the console
        return {
            "matrix": [],
            "personas": [],
            "data_types": [],
            "summary": {"green": 0, "amber": 0, "coral": 0, "na": 0},
            "coral_held": [],
            "passed": True,
            "error": f"coverage unavailable: {e}",
        }


def capabilities() -> list[dict]:
    """``data/capabilities.json`` rows: ``{repo, capability, confidence, evidence, attributes}``."""
    rows = fd.read_json(fd.data_path("capabilities.json"), []) or []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def dependencies() -> dict:
    """``data/dependencies.json`` (``{"owner/repo": [{name, version, ecosystem, licence, …}]}``)."""
    d = fd.read_json(fd.data_path("dependencies.json"), {}) or {}
    return d if isinstance(d, dict) else {}


def citation(path: str, note: str = "") -> dict:
    """A file citation: where the number on screen came from."""
    out = {"source": "fabric-data", "path": path, "exists": os.path.exists(path)}
    if note:
        out["note"] = note
    return out


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


# --------------------------------------------------------------------------
# repositories
# --------------------------------------------------------------------------
def _cap_index() -> dict[str, list[dict]]:
    by_repo: dict[str, list[dict]] = {}
    for c in capabilities():
        by_repo.setdefault(str(c.get("repo", "")), []).append(c)
    return by_repo


def _enterprise_score(caps: list[dict]) -> float | None:
    for c in caps:
        if c.get("capability") == "enterprise_readiness":
            score = (c.get("attributes") or {}).get("score")
            try:
                return float(score) if score is not None else None
            except (TypeError, ValueError):
                return None
    return None


def repositories() -> list[dict]:
    """The Curator *Repositories* rows — one per repository in ``facts.json``.

    ``[{repo, description, primary_language, languages, commits, prs_merged,
    contributors_count, deployments_count, capabilities: [names],
    enterprise_score, pushed_at, has_card, has_architecture}]``, sorted by
    repository name.
    """
    caps = _cap_index()
    out = []
    for repo, r in sorted((facts().get("repositories") or {}).items()):
        r = r or {}
        rc = caps.get(repo, [])
        slug = fd.repo_slug(repo)
        langs = r.get("languages") or {}
        out.append(
            {
                "repo": repo,
                "description": r.get("description") or "",
                "primary_language": r.get("primary_language") or "",
                "languages": {
                    k: {"bytes": int((v or {}).get("bytes") or 0), "share": (v or {}).get("share")}
                    for k, v in langs.items()
                },
                "commits": int((r.get("commits") or {}).get("total") or 0),
                "prs_merged": int((r.get("pull_requests") or {}).get("merged") or 0),
                "contributors_count": int(
                    r.get("contributors_count") or len(r.get("contributors") or [])
                ),
                "deployments_count": int((r.get("deployments") or {}).get("count") or 0),
                "capabilities": sorted(
                    {str(c.get("capability")) for c in rc if c.get("capability")}
                ),
                "enterprise_score": _enterprise_score(rc),
                "pushed_at": r.get("pushed_at"),
                "as_of": r.get("as_of"),
                "has_card": os.path.exists(fd.path("analysis", slug, "card.md")),
                "has_architecture": os.path.exists(fd.path("analysis", slug, "architecture.md")),
            }
        )
    return out


def _languages_bar(langs: dict) -> list[dict]:
    total = sum(int((v or {}).get("bytes") or 0) for v in langs.values())
    bar = []
    for name, v in langs.items():
        share = (v or {}).get("share")
        if share is None and total:
            share = int((v or {}).get("bytes") or 0) / total
        bar.append({"name": name, "share": round(float(share or 0), 4)})
    bar.sort(key=lambda x: -x["share"])
    return bar


def recent_activity(platform, tenant: str, repo: str) -> dict:
    """Recent pull requests and commits for ``repo`` from the passages the
    GitHub connector ingested (documents whose uri is ``github://<repo>/…`` and
    names a pull or a commit). Empty lists with a ``note`` when none — never a
    synthetic record."""
    prs: list[dict] = []
    commits: list[dict] = []
    if platform is None:
        return {
            "recent_prs": prs,
            "recent_commits": commits,
            "note": "no fabric store attached; PRs and commits come from ingested passages",
        }
    prefix = f"github://{repo}/"
    for d in platform.documents.list(tenant):
        uri = d.get("uri") or ""
        if not uri.startswith(prefix):
            continue
        tail = uri[len(prefix) :].lower()
        is_pr = re.match(r"^(pulls?|pull_requests?|prs?)/", tail) is not None
        is_commit = re.match(r"^commits?/", tail) is not None
        if not (is_pr or is_commit):
            continue
        text = ""
        try:
            ps = platform.passages.by_document(tenant, d["id"])
            text = (ps[0].text if ps else "") or ""
        except Exception:  # pragma: no cover - store variants
            text = ""
        entry = {
            "document_id": d.get("id"),
            "title": d.get("title") or uri,
            "uri": uri,
            "ingested_at": d.get("ingested_at"),
            "snippet": text[:240],
        }
        (prs if is_pr else commits).append(entry)
    prs.sort(key=lambda x: x.get("ingested_at") or 0, reverse=True)
    commits.sort(key=lambda x: x.get("ingested_at") or 0, reverse=True)
    out = {"recent_prs": prs[:_ACTIVITY_MAX], "recent_commits": commits[:_ACTIVITY_MAX]}
    if not prs and not commits:
        out["note"] = (
            "no pull-request or commit passages ingested for this repository yet "
            "(the GitHub connector writes them under github://<repo>/pulls/ and /commits/)"
        )
    return out


def repository(repo: str, platform=None, tenant: str = "") -> dict | None:
    """The repository card overlay: facts, languages bar, capability evidence,
    dependencies with licences, the architecture and card markdown, the
    contributors and the recent PRs / commits. ``None`` when ``facts.json``
    does not know the repository."""
    repos = facts().get("repositories") or {}
    if repo not in repos:
        return None
    r = repos[repo] or {}
    slug = fd.repo_slug(repo)
    caps = [
        {
            "capability": c.get("capability"),
            "confidence": c.get("confidence"),
            "evidence": c.get("evidence") or [],
            "attributes": c.get("attributes") or {},
        }
        for c in _cap_index().get(repo, [])
    ]
    deps = [
        {
            "name": d.get("name"),
            "version": d.get("version"),
            "ecosystem": d.get("ecosystem"),
            "licence": d.get("licence") or d.get("license") or "",
            "category": d.get("category") or "",
            "manifest_path": d.get("manifest_path") or "",
        }
        for d in (dependencies().get(repo) or [])
        if isinstance(d, dict)
    ]
    out = {
        "repo": repo,
        "facts": {
            "description": r.get("description") or "",
            "primary_language": r.get("primary_language") or "",
            "commits": (r.get("commits") or {}).get("total"),
            "pull_requests": r.get("pull_requests") or {},
            "releases": r.get("releases") or [],
            "deployments": r.get("deployments") or {},
            "workflows": r.get("workflows") or [],
            "contributors_count": r.get("contributors_count") or len(r.get("contributors") or []),
            "pushed_at": r.get("pushed_at"),
            "as_of": r.get("as_of"),
        },
        "languages_bar": _languages_bar(r.get("languages") or {}),
        "capabilities": caps,
        "dependencies": deps,
        "architecture_md": _read_text(fd.path("analysis", slug, "architecture.md")),
        "card_md": _read_text(fd.path("analysis", slug, "card.md")),
        "contributors": r.get("contributors") or [],
    }
    out.update(recent_activity(platform, tenant, repo))
    return out


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------
def _sqlite_path(doc_id: str, sheet: str) -> str:
    return fd.path("tables", doc_id, f"{sheet}.sqlite")


def table_sample(doc_id: str, sheet: str, n: int = _SAMPLE_ROWS) -> list[list]:
    """The first ``n`` rows of the extracted sheet (read-only), or ``[]``."""
    path = _sqlite_path(doc_id, sheet)
    if not os.path.isfile(path):
        return []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = con.execute(f"SELECT * FROM t LIMIT {int(n)}")
            return [list(row) for row in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return []


def tables() -> list[dict]:
    """The Curator *Tables* rows: every extracted sheet with its columns, row
    count and a small read-only sample (what the static showcase previews)."""
    out = []
    for t in facts().get("tables") or []:
        if not isinstance(t, dict):
            continue
        doc_id, sheet = str(t.get("doc_id") or ""), str(t.get("sheet") or "")
        out.append(
            {
                "doc_id": doc_id,
                "doc_title": t.get("doc_title") or doc_id,
                "sheet": sheet,
                "columns": [
                    {"name": c.get("name"), "type": c.get("type")}
                    if isinstance(c, dict)
                    else {"name": str(c), "type": ""}
                    for c in (t.get("columns") or [])
                ],
                "rows": int(t.get("rows") or 0),
                "path": t.get("path") or "",
                "available": os.path.isfile(_sqlite_path(doc_id, sheet)),
                "sample": table_sample(doc_id, sheet),
            }
        )
    return out


# --------------------------------------------------------------------------
# insights
# --------------------------------------------------------------------------
def insights() -> dict:
    """Capabilities across repositories plus reuse candidates.

    ``capabilities``: ``{capability: [{repo, confidence}]}`` (highest confidence
    first). ``reuse``: ``[{repo, symbol, path, why}]`` from
    ``analysis/<repo>/summary.json`` ``reuse_candidates`` when present, else
    from the ``caching`` capability's ``attributes.reusable``.
    """
    by_cap: dict[str, list[dict]] = {}
    caching: dict[str, list] = {}
    for c in capabilities():
        name, repo = c.get("capability"), c.get("repo")
        if not name or not repo:
            continue
        by_cap.setdefault(str(name), []).append(
            {"repo": str(repo), "confidence": float(c.get("confidence") or 0)}
        )
        if name == "caching":
            caching.setdefault(str(repo), []).extend(
                (c.get("attributes") or {}).get("reusable") or []
            )
    for rows in by_cap.values():
        rows.sort(key=lambda x: (-x["confidence"], x["repo"]))
    reuse: list[dict] = []
    for repo in sorted((facts().get("repositories") or {}).keys() | caching.keys()):
        summary = fd.read_json(fd.path("analysis", fd.repo_slug(repo), "summary.json"), {})
        cands = (summary or {}).get("reuse_candidates") if isinstance(summary, dict) else None
        if not cands:
            cands = caching.get(repo) or []
        for cand in cands:
            if isinstance(cand, dict):
                reuse.append(
                    {
                        "repo": repo,
                        "symbol": cand.get("symbol") or cand.get("name") or "",
                        "path": cand.get("path") or "",
                        "why": cand.get("why") or cand.get("reason") or "",
                    }
                )
            elif isinstance(cand, str):
                reuse.append({"repo": repo, "symbol": cand, "path": "", "why": "reusable"})
    return {"capabilities": dict(sorted(by_cap.items())), "reuse": reuse}


# --------------------------------------------------------------------------
# sources + corpus tiles
# --------------------------------------------------------------------------
def _max_as_of(items) -> str | None:
    vals = [str(i.get("as_of")) for i in items if isinstance(i, dict) and i.get("as_of")]
    return max(vals) if vals else None


def source_counts() -> dict:
    """The per-source counts the Admin Sources cards show, from ``facts.json``."""
    f = facts()
    repos = f.get("repositories") or {}
    jira = f.get("jira_projects") or {}
    conf = f.get("confluence_spaces") or {}
    gh_issues = 0
    for r in repos.values():
        r = r or {}
        iss = r.get("issues")
        if isinstance(iss, dict):
            gh_issues += int(iss.get("total") or 0)
        elif isinstance(iss, int | float):
            gh_issues += int(iss)
    return {
        "github": {
            "counts": {
                "repositories": len(repos),
                "commits": sum(
                    int(((r or {}).get("commits") or {}).get("total") or 0) for r in repos.values()
                ),
                "prs": sum(
                    int(((r or {}).get("pull_requests") or {}).get("total") or 0)
                    for r in repos.values()
                ),
                "issues": gh_issues,
            },
            "as_of": _max_as_of(repos.values()),
        },
        "jira": {
            "counts": {
                "projects": len(jira),
                "issues": sum(
                    int(((j or {}).get("issues") or {}).get("total") or 0) for j in jira.values()
                ),
            },
            "as_of": _max_as_of(jira.values()),
        },
        "confluence": {
            "counts": {
                "spaces": len(conf),
                "pages": sum(int((c or {}).get("pages") or 0) for c in conf.values()),
            },
            "as_of": _max_as_of(conf.values()),
        },
    }


def _count_images() -> int:
    root = fd.path("images")
    n = 0
    if os.path.isdir(root):
        for _dirpath, _dirs, files in os.walk(root):
            n += sum(1 for f in files if f.endswith(".json"))
    return n


def corpus_tiles() -> dict:
    """The five extra Workspace tiles: repositories, jira_projects,
    confluence_spaces, tables, images."""
    f = facts()
    return {
        "repositories": len(f.get("repositories") or {}),
        "jira_projects": len(f.get("jira_projects") or {}),
        "confluence_spaces": len(f.get("confluence_spaces") or {}),
        "tables": len(f.get("tables") or []),
        "images": _count_images(),
    }


def image_description(doc_id: str, name: str) -> dict | None:
    """``images/<doc>/<name>.json`` — the stored description of one image."""
    path = fd.path("images", doc_id, f"{name}.json")
    out = fd.read_json(path, None)
    return out if isinstance(out, dict) else None


# --------------------------------------------------------------------------
# query_facts — the MCP facts tool
# --------------------------------------------------------------------------
_METRICS = {
    "commits": ("commit",),
    "prs_merged": ("merged",),
    "pull_requests": ("pull request", "pull-request", " pr", "prs"),
    "contributors_count": ("contributor",),
    "deployments_count": ("deploy",),
    "releases": ("release",),
    "languages": ("language",),
    "workflows": ("workflow", "ci ", "pipeline"),
    "capabilities": ("capabilit",),
    "enterprise_score": ("enterprise", "readiness"),
    "pushed_at": ("last push", "pushed", "last commit date", "updated"),
}


def query_facts(question: str) -> dict:
    """Answer a numbers question straight from ``facts.json`` — the SAME rows
    the Curator Repositories panel renders (``repositories()``), so an MCP
    caller and the UI can never disagree.

    Result: ``{question, repositories: [rows], jira_projects, confluence_spaces,
    tables, focus: {repos, metrics}, answer}`` — the ``answer`` is a compact
    per-repository extract of the metrics the question names (all metrics when
    it names none); ``citations`` name the files read.
    """
    q = (question or "").lower()
    rows = repositories()
    f = facts()
    named = [r for r in rows if r["repo"].lower() in q or r["repo"].split("/")[-1].lower() in q]
    focus_rows = named or rows
    metrics = [m for m, keys in _METRICS.items() if any(k in q for k in keys)]
    if "pull_requests" in metrics and "prs_merged" not in metrics:
        metrics.append("prs_merged")
    full = {r["repo"]: (f.get("repositories") or {}).get(r["repo"]) or {} for r in focus_rows}
    answer = []
    for r in focus_rows:
        raw = full[r["repo"]]
        item: dict = {"repo": r["repo"]}
        for m in metrics or list(_METRICS):
            if m == "pull_requests":
                item["pull_requests"] = raw.get("pull_requests") or {}
            elif m == "releases":
                item["releases"] = raw.get("releases") or []
            elif m == "workflows":
                item["workflows"] = raw.get("workflows") or []
            else:
                item[m] = r.get(m)
        answer.append(item)
    jira = f.get("jira_projects") or {}
    conf = f.get("confluence_spaces") or {}
    result = {
        "question": question,
        "repositories": rows,
        "jira_projects": {
            k: {
                "issues": ((v or {}).get("issues") or {}).get("total"),
                "sprint": (v or {}).get("sprint"),
            }
            for k, v in jira.items()
        },
        "confluence_spaces": {k: {"pages": (v or {}).get("pages")} for k, v in conf.items()},
        "tables": len(f.get("tables") or []),
        "focus": {"repos": [r["repo"] for r in named], "metrics": metrics},
        "answer": answer,
    }
    return {
        "result": result,
        "citations": [
            citation(fd.data_path("facts.json"), "repository, Jira and Confluence facts"),
            citation(fd.data_path("capabilities.json"), "capability names and enterprise score"),
        ],
    }
