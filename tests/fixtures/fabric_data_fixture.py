"""A small ``fabric-data`` checkout for the T47/T48 tests.

Writes every file the surfaces and the MCP facts tools read, in the exact
shapes the analysis tracks produce: ``data/facts.json``,
``data/capabilities.json``, ``data/dependencies.json``,
``analysis/<slug>/{card.md, architecture.md, summary.json, symbols.jsonl}``,
``tables/<doc>/<sheet>.sqlite`` (single table ``t``) and
``images/<doc>/<name>.json``. Two repositories so per-repository numbers are
distinguishable; one Jira project; one Confluence space; one sheet; two images.
"""

from __future__ import annotations

import json
import os
import sqlite3

REPO_A = "qualizeal/fabric-core"
REPO_B = "qualizeal/test-harness"
DOC_ID = "doc_budget_2026"
SHEET = "Q1"

FACTS = {
    "repositories": {
        REPO_A: {
            "description": "The knowledge fabric core service.",
            "languages": {
                "Python": {"bytes": 800_000, "share": 0.8},
                "JavaScript": {"bytes": 200_000, "share": 0.2},
            },
            "primary_language": "Python",
            "contributors": [
                {"login": "prashobh", "contributions": 310},
                {"login": "asha", "contributions": 42},
            ],
            "contributors_count": 2,
            "commits": {"total": 412},
            "pull_requests": {
                "open": 3,
                "closed": 10,
                "merged": 57,
                "total": 70,
                "mean_size": {"additions": 120, "deletions": 40, "changed_files": 4},
            },
            "releases": [
                {"tag": "v0.4.0", "name": "0.4.0", "published_at": "2026-08-30T10:00:00Z"}
            ],
            "deployments": {
                "environments": ["staging", "production"],
                "latest": {"environment": "production", "created_at": "2026-09-01T08:00:00Z"},
                "count": 12,
            },
            "workflows": [
                {"name": "ci", "last_conclusion": "success"},
                {"name": "showcase", "last_conclusion": "success"},
            ],
            "pushed_at": "2026-09-09T12:00:00Z",
            "as_of": "2026-09-10T00:00:00Z",
        },
        REPO_B: {
            "description": "Automation harness for QE.",
            "languages": {"TypeScript": {"bytes": 50_000, "share": 1.0}},
            "primary_language": "TypeScript",
            "contributors": [{"login": "asha", "contributions": 9}],
            "contributors_count": 1,
            "commits": {"total": 33},
            "pull_requests": {
                "open": 1,
                "closed": 2,
                "merged": 5,
                "total": 8,
                "mean_size": {"additions": 30, "deletions": 5, "changed_files": 2},
            },
            "releases": [],
            "deployments": {"environments": [], "latest": {}, "count": 0},
            "workflows": [{"name": "ci", "last_conclusion": "failure"}],
            "pushed_at": "2026-07-02T09:30:00Z",
            "as_of": "2026-09-10T00:00:00Z",
        },
    },
    "jira_projects": {
        "REL": {
            "issues": {
                "total": 120,
                "by_status": {"Open": 40, "Done": 80},
                "by_priority": {"High": 10},
                "by_type": {"Bug": 60, "Story": 60},
                "by_assignee": {"asha": 20},
            },
            "sprint": "Sprint 42",
            "as_of": "2026-09-10T00:00:00Z",
        }
    },
    "confluence_spaces": {
        "QE": {"pages": 34, "last_updated": "2026-09-08T00:00:00Z", "as_of": "2026-09-10T00:00:00Z"}
    },
    "documents": {"by_area": {"qe": 3}, "by_type": {"docx": 2, "xlsx": 1}, "total": 3},
    "tables": [
        {
            "doc_id": DOC_ID,
            "doc_title": "Budget 2026",
            "sheet": SHEET,
            "columns": [
                {"name": "item", "type": "TEXT"},
                {"name": "amount", "type": "REAL"},
                {"name": "owner", "type": "TEXT"},
            ],
            "rows": 3,
            "path": f"tables/{DOC_ID}/{SHEET}.sqlite",
        }
    ],
}

CAPABILITIES = [
    {
        "repo": REPO_A,
        "capability": "rag",
        "confidence": 0.92,
        "evidence": [
            {
                "path": "knowledge_fabric/answer/service.py",
                "line": 125,
                "snippet": "class AnswerService:",
            }
        ],
        "attributes": {"retrieval_techniques": ["bm25", "hybrid"]},
    },
    {
        "repo": REPO_A,
        "capability": "caching",
        "confidence": 0.8,
        "evidence": [{"path": "knowledge_fabric/answer/cache.py", "line": 1, "snippet": "cache"}],
        "attributes": {
            "reusable": [
                {
                    "symbol": "AnswerCache",
                    "path": "knowledge_fabric/answer/cache.py",
                    "why": "tenant-scoped answer cache",
                }
            ]
        },
    },
    {
        "repo": REPO_A,
        "capability": "enterprise_readiness",
        "confidence": 0.7,
        "evidence": [],
        "attributes": {"score": 0.75, "checklist": {"sso": True, "audit": True, "sbom": False}},
    },
    {
        "repo": REPO_B,
        "capability": "dashboard_ui",
        "confidence": 0.6,
        "evidence": [{"path": "src/ui/app.css", "line": 1, "snippet": ".dash{}"}],
        "attributes": {"css_files": ["src/ui/app.css"]},
    },
]

DEPENDENCIES = {
    REPO_A: [
        {
            "name": "fastapi",
            "version": "0.115",
            "ecosystem": "pypi",
            "licence": "MIT",
            "category": "web",
            "manifest_path": "pyproject.toml",
        },
        {
            "name": "pg8000",
            "version": "1.31",
            "ecosystem": "pypi",
            "licence": "BSD-3-Clause",
            "category": "db",
            "manifest_path": "pyproject.toml",
        },
    ],
    REPO_B: [
        {
            "name": "playwright",
            "version": "1.45",
            "ecosystem": "npm",
            "licence": "Apache-2.0",
            "category": "test",
            "manifest_path": "package.json",
        }
    ],
}

TABLE_ROWS = [
    ("licences", 1200.5, "asha"),
    ("cloud", 8400.0, "prashobh"),
    ("training", 300.0, "asha"),
]


def slug(repo: str) -> str:
    from knowledge_fabric import fabric_data as fd

    return fd.repo_slug(repo)


def write_fabric(root: str) -> str:
    """Populate ``root`` as a fabric-data checkout and return it."""
    data = os.path.join(root, "data")
    os.makedirs(data, exist_ok=True)
    for name, obj in (
        ("facts.json", FACTS),
        ("capabilities.json", CAPABILITIES),
        ("dependencies.json", DEPENDENCIES),
    ):
        with open(os.path.join(data, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=1)
    # analysis for repo A only (repo B has facts but no card yet)
    a = os.path.join(root, "analysis", slug(REPO_A))
    os.makedirs(a, exist_ok=True)
    with open(os.path.join(a, "card.md"), "w", encoding="utf-8") as fh:
        fh.write("# fabric-core\n\nThe governed answer path.\n")
    with open(os.path.join(a, "architecture.md"), "w", encoding="utf-8") as fh:
        fh.write("# Architecture\n\n- answer service\n- ingestion pipeline\n")
    with open(os.path.join(a, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "reuse_candidates": [
                    {
                        "symbol": "AnswerService.ask",
                        "path": "knowledge_fabric/answer/service.py",
                        "why": "the single governed entry point",
                    }
                ]
            },
            fh,
        )
    with open(os.path.join(a, "symbols.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"symbol": "AnswerService", "path": "knowledge_fabric/answer/service.py"})
        )
        fh.write("\n")
    # the extracted sheet
    t = os.path.join(root, "tables", DOC_ID)
    os.makedirs(t, exist_ok=True)
    db = os.path.join(t, f"{SHEET}.sqlite")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(item TEXT, amount REAL, owner TEXT)")
    con.executemany("INSERT INTO t VALUES(?,?,?)", TABLE_ROWS)
    con.commit()
    con.close()
    # two image descriptions
    im = os.path.join(root, "images", DOC_ID)
    os.makedirs(im, exist_ok=True)
    for name in ("chart1", "diagram2"):
        with open(os.path.join(im, f"{name}.json"), "w", encoding="utf-8") as fh:
            json.dump({"description": f"{name} of the budget", "kind": "chart"}, fh)
    return root


def point_env(root: str) -> dict:
    """Set ``KF_FABRIC_ROOT``/``KF_DATA_ROOT`` at ``root``; returns the previous values."""
    prev = {k: os.environ.get(k) for k in ("KF_FABRIC_ROOT", "KF_DATA_ROOT")}
    os.environ["KF_FABRIC_ROOT"] = root
    os.environ["KF_DATA_ROOT"] = os.path.join(root, "data")
    return prev


def restore_env(prev: dict) -> None:
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
