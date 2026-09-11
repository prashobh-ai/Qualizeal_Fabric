"""Facts — the exact, as-of-dated numbers the fabric knows (T42).

The ``fabric-data`` writers (T37–T41) produce ``data/facts.json``,
``data/capabilities.json``, ``data/dependencies.json`` and the per-repository
``analysis/<slug>/`` files; this package READS them by their published schemas
and assembles the one block the platform itself owns (``documents`` counts).

Every loader is tolerant of a missing file — the aggregate simply has nothing
to answer from and the prose path runs — but never of a malformed one being
silently reshaped: the schema is the contract with the other tracks.
"""

from __future__ import annotations

import os

from .. import fabric_data as fd
from .build import build_documents_facts, write_documents_facts

EMPTY_FACTS: dict = {
    "repositories": {},
    "jira_projects": {},
    "confluence_spaces": {},
    "documents": {"by_area": {}, "by_type": {}, "total": 0},
    "tables": [],
}


def load_facts() -> dict:
    """``data/facts.json`` with every top-level key present (empty when absent)."""
    data = fd.read_json(fd.data_path("facts.json"), default={}) or {}
    out = {k: (v.copy() if isinstance(v, dict) else list(v)) for k, v in EMPTY_FACTS.items()}
    for k in EMPTY_FACTS:
        if k in data and data[k] is not None:
            out[k] = data[k]
    return out


def load_capabilities() -> list[dict]:
    """``data/capabilities.json`` — ``[{repo, capability, confidence, evidence, attributes}]``."""
    data = fd.read_json(fd.data_path("capabilities.json"), default=[])
    return [c for c in (data or []) if isinstance(c, dict) and c.get("repo")]


def load_dependencies() -> dict[str, list[dict]]:
    """``data/dependencies.json`` — ``{"owner/repo": [{name, version, ecosystem, licence, …}]}``."""
    data = fd.read_json(fd.data_path("dependencies.json"), default={})
    return {k: list(v) for k, v in (data or {}).items() if isinstance(v, list)}


def symbols_for(repo: str) -> list[dict]:
    """``analysis/<slug>/symbols.jsonl`` for one repository."""
    return fd.read_jsonl(fd.path("analysis", fd.repo_slug(repo), "symbols.jsonl"))


def analysed_repos() -> list[str]:
    """Repositories with an ``analysis/<slug>/`` directory (slug form)."""
    base = fd.path("analysis")
    try:
        return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    except OSError:
        return []


def architecture_for(repo: str) -> str:
    """The model-written ``architecture.md`` for a repository ('' when absent)."""
    try:
        with open(
            fd.path("analysis", fd.repo_slug(repo), "architecture.md"), encoding="utf-8"
        ) as f:
            return f.read()
    except OSError:
        return ""


__all__ = [
    "EMPTY_FACTS",
    "analysed_repos",
    "architecture_for",
    "build_documents_facts",
    "load_capabilities",
    "load_dependencies",
    "load_facts",
    "symbols_for",
    "write_documents_facts",
]
