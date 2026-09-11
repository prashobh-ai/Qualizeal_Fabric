"""Architecture summary (T38) — the one model call per changed repository.

Input: the card, the README, the tree to depth 3, the 30 most-connected
symbols with docstrings, and the capability evidence. Purpose
``repo_summary`` on ``model_large``. The reply must be the JSON::

    {purpose, architecture, components[{name, path, role}], data_flow,
     retrieval_techniques[], reuse_candidates[{symbol, path, why}], risks[],
     citations[]}

validated here (types, required keys, every cited path present in the tree);
an invalid reply is reported and NOT stored. The rendered
``analysis/<repo>/architecture.md`` cites a path on every sentence and is
ingested as ``<repo> — architecture`` (``source_kind: analysis``,
authority 65). Without a configured provider the step returns
``{"model": "skipped (extractive)"}`` — deterministically, never a stub.
"""

from __future__ import annotations

import json
import os
import re

from .. import fabric_data as fd
from . import callgraph as _callgraph
from . import cards as _cards

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
REQUIRED = {
    "purpose": str,
    "architecture": str,
    "components": list,
    "data_flow": str,
    "retrieval_techniques": list,
    "reuse_candidates": list,
    "risks": list,
    "citations": list,
}


def tree(clone_dir: str, depth: int = 3, limit: int = 400) -> list[str]:
    """Relative paths to ``depth`` (directories end with ``/``), sorted."""
    out: list[str] = []
    for root, dirs, files in os.walk(clone_dir):
        rel = os.path.relpath(root, clone_dir)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if level >= depth:
            dirs[:] = []
        prefix = "" if rel == "." else rel.replace(os.sep, "/") + "/"
        for d in dirs:
            out.append(prefix + d + "/")
        for f in sorted(files):
            out.append(prefix + f)
        if len(out) >= limit:
            break
    return out[:limit]


def readme_text(clone_dir: str, limit: int = 6000) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = os.path.join(clone_dir, name)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    return f.read()[:limit]
            except OSError:
                return ""
    return ""


def build_input(
    clone_dir: str, card_md: str, symbols: list[dict], graph: dict, caps: list[dict]
) -> str:
    top = _callgraph.most_connected(graph, symbols, n=30) if graph else []
    sym_lines = [
        f"- {s.get('qualified') or s.get('symbol')} ({s.get('path')}:{s.get('start_line')}): "
        f"{(s.get('docstring') or '').splitlines()[0][:160] if s.get('docstring') else '—'}"
        for s in top
    ]
    cap_lines = []
    for c in caps:
        for e in (c.get("evidence") or [])[:3]:
            cap_lines.append(
                f"- {c.get('capability')}: {e.get('path')}:{e.get('line')} "
                f"{e.get('snippet', '')[:100]}"
            )
    return "\n\n".join(
        [
            "## Repository card\n" + card_md,
            "## README (truncated)\n" + (readme_text(clone_dir) or "(none)"),
            "## Tree (depth 3)\n" + "\n".join(tree(clone_dir)),
            "## Most-connected symbols\n" + ("\n".join(sym_lines) or "(none)"),
            "## Capability evidence\n" + ("\n".join(cap_lines) or "(none)"),
        ]
    )


SYSTEM = (
    "You are documenting a software repository for an engineering knowledge base. "
    "Use ONLY the material provided. Reply with ONE JSON object and nothing else, with keys: "
    "purpose (string), architecture (string, 3-6 sentences), components (array of "
    "{name, path, role}), data_flow (string), retrieval_techniques (array of strings from "
    "bm25 dense hybrid rrf mmr rerank graph_expansion multi_query hyde parent_child, empty "
    "when none), reuse_candidates (array of {symbol, path, why}), risks (array of strings), "
    "citations (array of repository paths you relied on). Every path must appear in the tree "
    "or the symbol list. Never invent files, symbols or techniques."
)


def validate(data, paths: set[str]) -> list[str]:
    """Problems with a summary reply (empty list = valid)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["reply is not a JSON object"]
    for key, typ in REQUIRED.items():
        if key not in data:
            problems.append(f"missing {key}")
        elif not isinstance(data[key], typ):
            problems.append(f"{key} is not {typ.__name__}")
    if problems:
        return problems
    for i, c in enumerate(data["components"]):
        if not isinstance(c, dict) or not all(
            isinstance(c.get(k), str) for k in ("name", "path", "role")
        ):
            problems.append(f"components[{i}] needs name/path/role strings")
        elif not _known(c["path"], paths):
            problems.append(f"components[{i}].path {c['path']!r} not in the repository")
    for i, r in enumerate(data["reuse_candidates"]):
        if not isinstance(r, dict) or not all(
            isinstance(r.get(k), str) for k in ("symbol", "path", "why")
        ):
            problems.append(f"reuse_candidates[{i}] needs symbol/path/why strings")
        elif not _known(r["path"], paths):
            problems.append(f"reuse_candidates[{i}].path {r['path']!r} not in the repository")
    for i, p in enumerate(data["citations"]):
        if not isinstance(p, str) or not _known(p, paths):
            problems.append(f"citations[{i}] {p!r} not in the repository")
    if not data["citations"]:
        problems.append("citations is empty")
    for t in data["retrieval_techniques"]:
        if t not in (
            "bm25",
            "dense",
            "hybrid",
            "rrf",
            "mmr",
            "rerank",
            "graph_expansion",
            "multi_query",
            "hyde",
            "parent_child",
        ):
            problems.append(f"unknown retrieval technique {t!r}")
    return problems


def _known(path: str, paths: set[str]) -> bool:
    p = (path or "").strip().lstrip("./")
    if not p:
        return False
    return p in paths or p + "/" in paths or any(k.startswith(p + "/") for k in paths)


def render(repo: str, data: dict) -> str:
    """architecture.md — every sentence carries a path citation."""
    cites = data.get("citations") or []
    main = cites[0] if cites else ""
    lines = [f"# {repo} — architecture", "", f"**Purpose.** {data['purpose']} ({main})", ""]
    lines.append("## Architecture")
    for sent in re.split(r"(?<=[.!?])\s+", data["architecture"].strip()):
        if sent:
            lines.append(f"{sent} ({main})")
    lines += ["", "## Components"]
    for c in data["components"]:
        lines.append(f"- **{c['name']}** — {c['role']} ({c['path']})")
    lines += ["", "## Data flow", f"{data['data_flow']} ({main})", ""]
    lines.append(
        "Retrieval techniques: "
        + (", ".join(data["retrieval_techniques"]) if data["retrieval_techniques"] else "none")
        + f" ({main})"
    )
    lines += ["", "## Reuse candidates"]
    for r in data["reuse_candidates"] or []:
        lines.append(f"- `{r['symbol']}` ({r['path']}) — {r['why']}")
    if not data["reuse_candidates"]:
        lines.append(f"- none identified ({main})")
    lines += ["", "## Risks"]
    for risk in data["risks"] or []:
        lines.append(f"- {risk} ({main})")
    if not data["risks"]:
        lines.append(f"- none recorded ({main})")
    lines += ["", "## Citations", ", ".join(cites), ""]
    return "\n".join(lines)


def write(repo: str, md: str) -> str:
    p = fd.path("analysis", fd.repo_slug(repo), "architecture.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(md)
    return p


def summarise(
    platform,
    tenant: str,
    repo: str,
    clone_dir: str,
    facts: dict | None,
    card_md: str,
    symbols: list[dict],
    graph: dict,
    caps: list[dict],
    *,
    acl: list[str] | None = None,
    branch: str = "main",
) -> dict:
    """The model summary for one repository (see the module docstring)."""
    client = getattr(platform, "model", None) if platform is not None else None
    if client is None or not hasattr(client, "messages"):
        return {"model": "skipped (extractive)", "written": False}
    from ..adapters.model import resolve_models

    _small, large = resolve_models()
    body = {
        "model": large,
        "max_tokens": 2500,
        "system": [{"type": "text", "text": SYSTEM}],
        "messages": [
            {"role": "user", "content": build_input(clone_dir, card_md, symbols, graph, caps)}
        ],
    }
    data = client.messages(body, purpose="repo_summary", repo=repo)
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    result = {
        "model": data.get("model", large),
        "request_id": data.get("request_id", ""),
        "cost_usd": data.get("cost_usd", 0.0),
        "written": False,
    }
    if not m:
        result["error"] = "no JSON object in the reply"
        return result
    try:
        obj = json.loads(m.group(0))
    except ValueError as e:
        result["error"] = f"invalid JSON: {e}"
        return result
    paths = set(tree(clone_dir, depth=12, limit=20000)) | {s.get("path", "") for s in symbols}
    problems = validate(obj, paths)
    if problems:
        result["error"] = "invalid summary: " + "; ".join(problems[:5])
        return result
    md = render(repo, obj)
    result["path"] = write(repo, md)
    result["written"] = True
    if platform is not None:
        result["ingest"] = _cards.ingest(
            platform,
            tenant,
            repo,
            md,
            acl=acl,
            kind="architecture",
            title=f"{repo} — architecture",
        )
    return result
