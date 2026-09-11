"""The ``fabric-data`` layout (T46) — one place every writer and reader agrees on.

The fabric's data lives on the ``fabric-data`` branch (checked out into a
directory in Actions) or, locally, under ``<repo>/fabric-data/``:

    data/{facts.json, capabilities.json, dependencies.json, provider_status.json,
          prices.json, quality/, api_calls/}
    corpus/public/**
    analysis/<repo>/{card.md, architecture.md, symbols.jsonl, comments.jsonl, callgraph.json}
    tables/<doc>/<sheet>.sqlite
    images/<doc>/<name>.json
    answers/<hash>.json

``KF_FABRIC_ROOT`` points at the checkout; ``KF_DATA_ROOT`` (read by the
ledger and the provider status) defaults to ``<fabric root>/data``.
"""

from __future__ import annotations

import json
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fabric_root() -> str:
    return os.environ.get("KF_FABRIC_ROOT") or os.path.join(_REPO, "fabric-data")


def path(*parts: str, mkdir: bool = False) -> str:
    """A path under the fabric root; ``mkdir=True`` creates the parent."""
    p = os.path.join(fabric_root(), *parts)
    if mkdir:
        os.makedirs(os.path.dirname(p) if "." in os.path.basename(p) else p, exist_ok=True)
    return p


def data_path(name: str, mkdir: bool = False) -> str:
    """``data/<name>`` — honours ``KF_DATA_ROOT`` like the ledger does."""
    root = os.environ.get("KF_DATA_ROOT") or os.path.join(fabric_root(), "data")
    if mkdir:
        os.makedirs(root, exist_ok=True)
    return os.path.join(root, name)


def repo_slug(repo: str) -> str:
    """``owner/name`` → a safe directory name (``owner__name``)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", repo.strip("/"))


def read_json(p: str, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(p: str, obj) -> str:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=str)
    os.replace(tmp, p)
    return p


def append_jsonl(p: str, rows) -> int:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    n = 0
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
            n += 1
    return n


def read_jsonl(p: str) -> list:
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out
