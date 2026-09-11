"""A deterministic fabric-data fixture for the T49 sets (facts, tables, images,
jira). Every expected value in ``eval/sets/*.jsonl`` is derived from THIS data,
so the sets are exact and self-contained — no live source is needed to grade
them. ``build(root)`` writes the fixture under a fabric root."""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3

from knowledge_fabric import fabric_data as fd

NOW = _dt.datetime(2026, 9, 11, 6, 0, tzinfo=_dt.UTC).isoformat()

REPOS = {
    "acme/knowledge-fabric": {
        "description": "Grounded answers with citations over the organisation's knowledge",
        "homepage": "",
        "topics": ["rag", "knowledge-graph"],
        "default_branch": "main",
        "visibility": "private",
        "archived": False,
        "size_kb": 4120,
        "stars": 12,
        "forks": 1,
        "license_spdx": "Apache-2.0",
        "created_at": "2025-01-10T00:00:00Z",
        "pushed_at": "2026-09-10T22:00:00Z",
        "open_issues": 4,
        "languages": {
            "Python": {"bytes": 810000, "share": 0.9},
            "JavaScript": {"bytes": 90000, "share": 0.1},
        },
        "primary_language": "Python",
        "contributors": [
            {"login": "alice", "contributions": 640},
            {"login": "bob", "contributions": 400},
            {"login": "carol", "contributions": 120},
            {"login": "dave", "contributions": 60},
            {"login": "erin", "contributions": 30},
            {"login": "frank", "contributions": 20},
            {"login": "grace", "contributions": 14},
        ],
        "contributors_count": 7,
        "commits": {"total": 1284},
        "pull_requests": {
            "open": 9,
            "closed": 31,
            "merged": 212,
            "total": 252,
            "mean_size": {"additions": 180, "deletions": 60, "changed_files": 6},
        },
        "releases": [{"tag": "v0.4.0", "name": "0.4.0", "published_at": "2026-08-01T00:00:00Z"}],
        "deployments": {
            "environments": ["staging", "production"],
            "latest": {"staging": "success", "production": "success"},
            "count": 41,
        },
        "workflows": [{"name": "ci", "last_conclusion": "success"}],
        "as_of": NOW,
    },
    "acme/etl-jobs": {
        "description": "Nightly ETL jobs",
        "homepage": "",
        "topics": [],
        "default_branch": "main",
        "visibility": "private",
        "archived": False,
        "size_kb": 300,
        "stars": 0,
        "forks": 0,
        "license_spdx": "MIT",
        "created_at": "2025-06-01T00:00:00Z",
        "pushed_at": "2026-09-01T00:00:00Z",
        "open_issues": 1,
        "languages": {"Python": {"bytes": 50000, "share": 1.0}},
        "primary_language": "Python",
        "contributors": [
            {"login": "carol", "contributions": 70},
            {"login": "dave", "contributions": 26},
        ],
        "contributors_count": 2,
        "commits": {"total": 96},
        "pull_requests": {
            "open": 1,
            "closed": 4,
            "merged": 18,
            "total": 23,
            "mean_size": {"additions": 40, "deletions": 10, "changed_files": 2},
        },
        "releases": [],
        "deployments": {
            "environments": ["production"],
            "latest": {"production": "success"},
            "count": 12,
        },
        "workflows": [{"name": "nightly", "last_conclusion": "success"}],
        "as_of": NOW,
    },
    "acme/web-portal": {
        "description": "Customer web portal",
        "homepage": "https://portal.example",
        "topics": ["web"],
        "default_branch": "main",
        "visibility": "private",
        "archived": False,
        "size_kb": 2200,
        "stars": 3,
        "forks": 0,
        "license_spdx": "MIT",
        "created_at": "2024-03-01T00:00:00Z",
        "pushed_at": "2026-09-09T00:00:00Z",
        "open_issues": 7,
        "languages": {"TypeScript": {"bytes": 400000, "share": 1.0}},
        "primary_language": "TypeScript",
        "contributors": [
            {"login": "erin", "contributions": 300},
            {"login": "frank", "contributions": 90},
            {"login": "grace", "contributions": 40},
        ],
        "contributors_count": 3,
        "commits": {"total": 430},
        "pull_requests": {
            "open": 3,
            "closed": 12,
            "merged": 58,
            "total": 73,
            "mean_size": {"additions": 120, "deletions": 50, "changed_files": 5},
        },
        "releases": [{"tag": "2.1.0", "name": "2.1.0", "published_at": "2026-07-15T00:00:00Z"}],
        "deployments": {
            "environments": ["production"],
            "latest": {"production": "success"},
            "count": 88,
        },
        "workflows": [{"name": "deploy", "last_conclusion": "success"}],
        "as_of": NOW,
    },
}

JIRA = {
    "QZ": {
        "issues": {
            "total": 63,
            "by_status": {"open": 14, "in progress": 9, "done": 40},
            "by_priority": {"critical": 3, "high": 15, "medium": 30, "low": 15},
            "by_type": {"bug": 14, "story": 40, "task": 9},
            "by_assignee": {"alice": 11, "bob": 20, "carol": 32},
        },
        "sprint": {"name": "Sprint 42", "state": "active"},
        "as_of": NOW,
    },
    "ENG": {
        "issues": {
            "total": 27,
            "by_status": {"open": 6, "done": 21},
            "by_priority": {"high": 7, "medium": 20},
            "by_type": {"bug": 12, "task": 15},
            "by_assignee": {"dave": 27},
        },
        "sprint": None,
        "as_of": NOW,
    },
}

CAPABILITIES = [
    {
        "repo": "acme/knowledge-fabric",
        "capability": "rag",
        "confidence": 0.95,
        "evidence": [{"path": "kf/retrieve.py", "line": 12, "snippet": "def bm25_search("}],
        "attributes": {
            "retrieval_techniques": ["bm25", "dense", "rrf"],
            "vector_store": "sqlite",
            "embedding_model": "bge-m3",
            "chunking": "symbol",
        },
    },
    {
        "repo": "acme/web-portal",
        "capability": "rag",
        "confidence": 0.7,
        "evidence": [{"path": "src/search.ts", "line": 3, "snippet": "import { embed }"}],
        "attributes": {"retrieval_techniques": ["dense"]},
    },
    {
        "repo": "acme/knowledge-fabric",
        "capability": "knowledge_graph",
        "confidence": 0.9,
        "evidence": [{"path": "kf/graph.py", "line": 1, "snippet": "class GraphRepo"}],
        "attributes": {"store": "sqlite", "visualised": True},
    },
    {
        "repo": "acme/knowledge-fabric",
        "capability": "caching",
        "confidence": 0.9,
        "evidence": [{"path": "kf/cache.py", "line": 8, "snippet": "class AnswerCache"}],
        "attributes": {"kinds": ["answer", "embedding"], "reusable": ["AnswerCache"]},
    },
    {
        "repo": "acme/etl-jobs",
        "capability": "caching",
        "confidence": 0.65,
        "evidence": [{"path": "jobs/util.py", "line": 4, "snippet": "@lru_cache"}],
        "attributes": {"kinds": ["http"], "reusable": []},
    },
    {
        "repo": "acme/web-portal",
        "capability": "dashboard_ui",
        "confidence": 0.9,
        "evidence": [{"path": "src/dashboard.css", "line": 1, "snippet": ".dashboard {"}],
        "attributes": {"css_files": ["dashboard.css"], "chart_library": "recharts"},
    },
    {
        "repo": "acme/knowledge-fabric",
        "capability": "enterprise_readiness",
        "confidence": 1.0,
        "evidence": [],
        "attributes": {
            "score": 80,
            "checklist": [
                {"item": "Dockerfile", "present": True, "evidence": "Dockerfile"},
                {"item": "CI", "present": True, "evidence": ".github/workflows/ci.yml"},
                {"item": "load test", "present": False, "evidence": ""},
            ],
        },
    },
]

DEPENDENCIES = {
    "acme/knowledge-fabric": [
        {
            "name": "fastapi",
            "version": ">=0.115",
            "ecosystem": "pypi",
            "licence": "MIT",
            "category": "permissive",
            "manifest_path": "pyproject.toml",
        },
        {
            "name": "pydantic",
            "version": ">=2",
            "ecosystem": "pypi",
            "licence": "MIT",
            "category": "permissive",
            "manifest_path": "pyproject.toml",
        },
    ],
}

SYMBOLS = [
    {
        "repo": "acme/knowledge-fabric",
        "path": "kf/util/retry.py",
        "language": "python",
        "symbol": "retry_call",
        "qualified": "kf.util.retry.retry_call",
        "kind": "function",
        "signature": "def retry_call(fn, attempts=3)",
        "docstring": "Retry a flaky call with backoff until it succeeds.",
        "start_line": 4,
        "end_line": 11,
        "url": "https://github.com/acme/knowledge-fabric/blob/main/kf/util/retry.py#L4-L11",
    },
    {
        "repo": "acme/knowledge-fabric",
        "path": "kf/cache.py",
        "language": "python",
        "symbol": "AnswerCache",
        "qualified": "kf.cache.AnswerCache",
        "kind": "class",
        "signature": "class AnswerCache",
        "docstring": "Answer cache.",
        "start_line": 8,
        "end_line": 40,
        "url": "https://github.com/acme/knowledge-fabric/blob/main/kf/cache.py#L8-L40",
    },
]

# The Defects sheet — 12 rows; every tables.jsonl expectation is computed from it.
DEFECTS = [
    ("D-1", "critical", "open", "alice", "payments", 13),
    ("D-2", "high", "closed", "bob", "checkout", 5),
    ("D-3", "low", "closed", "carol", "checkout", 2),
    ("D-4", "critical", "closed", "alice", "payments", 8),
    ("D-5", "high", "open", "bob", "checkout", 5),
    ("D-6", "low", "open", "carol", "search", 1),
    ("D-7", "medium", "closed", "bob", "checkout", 8),
    ("D-8", "low", "closed", "alice", "search", 3),
    ("D-9", "critical", "open", "bob", "payments", 3),
    ("D-10", "high", "closed", "alice", "payments", 3),
    ("D-11", "low", "open", "carol", "search", 2),
    ("D-12", "high", "closed", "bob", "checkout", 5),
]

IMAGES = {
    ("arch-deck.pptx", "slide3.png"): {
        "caption": "Architecture diagram: ingestion feeds a retriever and an answer service",
        "text_present": True,
        "entities": ["ingestion", "retriever", "answer service"],
        "kind": "diagram",
        "diagram": {
            "components": ["ingestion", "retriever", "answer service"],
            "connections": ["ingestion->retriever", "retriever->answer service"],
        },
        "chart": None,
        "ocr_text": "Ingestion  Retriever  Answer",
    },
    ("qa-report.docx", "chart1.png"): {
        "caption": "Line chart of open defects per week, decreasing from 42 to 18",
        "text_present": True,
        "entities": ["open defects"],
        "kind": "chart",
        "diagram": None,
        "chart": {"type": "line", "series": ["open defects"], "readings": ["42", "35", "27", "18"]},
        "ocr_text": "Open defects",
    },
    ("onboarding.pdf", "login.png"): {
        "caption": "Login screenshot with a Sign in button",
        "text_present": True,
        "entities": ["Sign in"],
        "kind": "screenshot",
        "diagram": None,
        "chart": None,
        "ocr_text": "Sign in  Email  Password",
    },
    ("release-plan.docx", "timeline.png"): {
        "caption": "Release timeline: Zephyr ships in Q3",
        "text_present": True,
        "entities": ["Zephyr", "Q3"],
        "kind": "diagram",
        "diagram": {"components": ["Q3", "Zephyr"], "connections": []},
        "chart": None,
        "ocr_text": "Q3 Zephyr",
    },
}


def build(root: str) -> str:
    """Write the fixture under ``root`` (sets KF_FABRIC_ROOT/KF_DATA_ROOT)."""
    os.environ["KF_FABRIC_ROOT"] = root
    os.environ["KF_DATA_ROOT"] = os.path.join(root, "data")
    tables = [
        {
            "doc_id": "qa-metrics.xlsx",
            "doc_title": "QA metrics",
            "sheet": "Defects",
            "columns": [
                {"name": "key", "type": "text"},
                {"name": "priority", "type": "text"},
                {"name": "status", "type": "text"},
                {"name": "assignee", "type": "text"},
                {"name": "component", "type": "text"},
                {"name": "story_points", "type": "int"},
            ],
            "rows": len(DEFECTS),
            "path": "tables/qa-metrics.xlsx/Defects.sqlite",
        }
    ]
    fd.write_json(
        fd.data_path("facts.json", mkdir=True),
        {
            "generated_at": NOW,
            "repositories": REPOS,
            "jira_projects": JIRA,
            "confluence_spaces": {"ENG": {"pages": 40, "last_updated": NOW, "as_of": NOW}},
            "documents": {
                "by_area": {"Code": 120, "Repository": 3},
                "by_type": {"code": 120, "document": 22},
                "total": 145,
            },
            "tables": tables,
        },
    )
    fd.write_json(fd.data_path("capabilities.json"), CAPABILITIES)
    fd.write_json(fd.data_path("dependencies.json"), DEPENDENCIES)
    slug = fd.repo_slug("acme/knowledge-fabric")
    fd.append_jsonl(fd.path("analysis", slug, "symbols.jsonl", mkdir=True), SYMBOLS)
    with open(fd.path("analysis", slug, "architecture.md"), "w", encoding="utf-8") as f:
        f.write(
            "# acme/knowledge-fabric — architecture\n\nRetrieval techniques: bm25, dense, rrf "
            "(kf/retrieve.py). Reuse candidates: AnswerCache (kf/cache.py).\n"
        )
    p = fd.path("tables", "qa-metrics.xlsx", "Defects.sqlite", mkdir=True)
    if os.path.exists(p):
        os.remove(p)
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE t(key TEXT, priority TEXT, status TEXT, assignee TEXT, "
        "component TEXT, story_points INTEGER)"
    )
    con.executemany("INSERT INTO t VALUES(?,?,?,?,?,?)", DEFECTS)
    con.commit()
    con.close()
    for (doc, name), desc in IMAGES.items():
        fd.write_json(
            fd.path("images", doc, name + ".json", mkdir=True),
            {
                "doc_id": doc,
                "name": name,
                "model": "fixture",
                "citation_url": f"file://{doc}/{name}",
                **desc,
            },
        )
    return root
