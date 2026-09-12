"""SQLite-backed store with a per-thread connection factory.

The local default uses SQLite so the whole platform runs with zero external
services (the spec's Postgres+pgvector is the cloud adapter behind the same
repository API). Tenant isolation (I5) is enforced by the repository layer
in ``repositories.py`` via a guaranteed WHERE filter — never left to the
caller to remember.

Concurrency note (Runbook Section 5.1): we open a fresh connection per
thread and never bind a tenant to a connection. The tenant is a query
parameter on every statement, so a pooled/reused connection cannot leak
another tenant's rows.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, source TEXT, source_version TEXT,
    content_hash TEXT, type TEXT, language TEXT, title TEXT, uri TEXT,
    ingested_at INTEGER, status TEXT, current_version INTEGER, acl TEXT,
    authoritative INTEGER DEFAULT 0, meta TEXT
);
CREATE TABLE IF NOT EXISTS passages (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, document_id TEXT NOT NULL,
    text TEXT, abstract TEXT, overview TEXT,
    coord_kind TEXT, coord_locator TEXT,
    prov_hash TEXT, prov_source TEXT, prov_version TEXT,
    version INTEGER, superseded_by TEXT, index_version INTEGER DEFAULT 1,
    acl TEXT
);
CREATE TABLE IF NOT EXISTS embeddings (
    tenant TEXT NOT NULL, passage_id TEXT NOT NULL, model_id TEXT,
    vec TEXT, PRIMARY KEY (tenant, passage_id, model_id)
);
CREATE TABLE IF NOT EXISTS originals (
    content_hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, object_key TEXT,
    size INTEGER, mime TEXT
);
CREATE TABLE IF NOT EXISTS graph_nodes (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, canonical_key TEXT, type TEXT,
    labels TEXT, provenance TEXT
);
CREATE TABLE IF NOT EXISTS graph_edges (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, src TEXT, dst TEXT, relation TEXT,
    typed_fact TEXT, weight REAL, contextual_weight REAL, provenance TEXT,
    conflict_flag INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ontology_versions (
    tenant TEXT NOT NULL, version INTEGER, schema TEXT, active INTEGER,
    PRIMARY KEY (tenant, version)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, kind TEXT, payload TEXT,
    state TEXT, attempts INTEGER, lease REAL, dead_letter_reason TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS health_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, area TEXT,
    coverage REAL, freshness REAL, contradictions INTEGER, gaps INTEGER,
    connectedness REAL, traceability REAL, taken_at INTEGER
);
CREATE TABLE IF NOT EXISTS spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, tenant TEXT, name TEXT,
    attrs TEXT, started_at REAL, duration_ms REAL, cost REAL, tokens INTEGER,
    tier TEXT, grounding REAL, citations_count INTEGER, stage TEXT,
    subject TEXT, roles TEXT, level TEXT, why TEXT,
    tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
    cache_hit INTEGER DEFAULT 0, cache_technique TEXT, cost_saved REAL DEFAULT 0,
    lang TEXT, sources TEXT,
    model_name TEXT, complexity TEXT, dataset_version INTEGER DEFAULT 0, reasoning TEXT
);
CREATE TABLE IF NOT EXISTS budgets (
    tenant TEXT PRIMARY KEY, cap REAL, spent REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS agent_budgets (
    tenant TEXT NOT NULL, agent TEXT NOT NULL, cap REAL, spent REAL DEFAULT 0,
    PRIMARY KEY (tenant, agent)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, subject TEXT,
    is_agent INTEGER, action TEXT, resource TEXT, decision TEXT,
    trace_id TEXT, at INTEGER
);
CREATE TABLE IF NOT EXISTS question_bank (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, question TEXT,
    expected_docs TEXT, family TEXT
);
CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, candidate_version INTEGER,
    passed INTEGER, metrics TEXT, regressions TEXT, at INTEGER
);
CREATE TABLE IF NOT EXISTS index_versions (
    tenant TEXT NOT NULL, version INTEGER, state TEXT, promoted_at INTEGER,
    PRIMARY KEY (tenant, version)
);
CREATE TABLE IF NOT EXISTS curation_queue (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, item TEXT, kind TEXT,
    status TEXT, assignee TEXT, resolution TEXT, at INTEGER
);
CREATE TABLE IF NOT EXISTS connector_cursors (
    tenant TEXT NOT NULL, source TEXT NOT NULL, cursor TEXT, last_sync INTEGER,
    items INTEGER DEFAULT 0, PRIMARY KEY (tenant, source)
);
CREATE TABLE IF NOT EXISTS dataset_versions (
    tenant TEXT NOT NULL, version INTEGER NOT NULL, created_at INTEGER, reason TEXT,
    doc_count INTEGER, passage_count INTEGER, PRIMARY KEY (tenant, version)
);
CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, document_id TEXT NOT NULL, version INTEGER,
    content_hash TEXT, created_at INTEGER, passage_ids TEXT, source_version TEXT
);
CREATE TABLE IF NOT EXISTS source_authority (
    tenant TEXT NOT NULL, source TEXT NOT NULL, rank INTEGER, weight REAL,
    PRIMARY KEY (tenant, source)
);
CREATE TABLE IF NOT EXISTS refresh_schedules (
    tenant TEXT NOT NULL, source TEXT NOT NULL, interval_s INTEGER, next_run REAL,
    last_run REAL, last_status TEXT, error_count INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
    config TEXT, PRIMARY KEY (tenant, source)
);
CREATE TABLE IF NOT EXISTS connector_config (
    tenant TEXT NOT NULL, source TEXT NOT NULL, enabled INTEGER DEFAULT 1, config TEXT,
    allow TEXT, scopes TEXT, updated_at INTEGER, PRIMARY KEY (tenant, source)
);
CREATE TABLE IF NOT EXISTS curation_decisions (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, document_id TEXT, decision TEXT, reason TEXT,
    by_subject TEXT, at INTEGER
);
CREATE TABLE IF NOT EXISTS ingest_runs (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, source TEXT, started_at INTEGER,
    finished_at INTEGER, status TEXT, steps TEXT, items INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rate_limit (
    tenant TEXT NOT NULL, subject TEXT NOT NULL, window_start REAL, count INTEGER,
    PRIMARY KEY (tenant, subject)
);
CREATE TABLE IF NOT EXISTS curation_settings (
    tenant TEXT NOT NULL, source TEXT NOT NULL, mode TEXT,
    PRIMARY KEY (tenant, source)
);
CREATE TABLE IF NOT EXISTS curation_log (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, ts INTEGER, action TEXT, mode TEXT,
    source TEXT, document_id TEXT, title TEXT, score TEXT, recommendation TEXT,
    reason TEXT, actor TEXT, review_id TEXT
);
CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY, tenant TEXT NOT NULL,
    subject_kind TEXT, subject_id TEXT, relation TEXT, object_kind TEXT, object_id TEXT,
    evidence_kind TEXT, evidence_id TEXT, evidence_url TEXT, evidence_title TEXT, at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_passages_tenant ON passages(tenant, index_version);
CREATE INDEX IF NOT EXISTS ix_docs_tenant ON documents(tenant);
CREATE INDEX IF NOT EXISTS ix_edges_tenant ON graph_edges(tenant, src);
CREATE INDEX IF NOT EXISTS ix_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS ix_audit_tenant ON audit_log(tenant);
CREATE INDEX IF NOT EXISTS ix_curation_log_tenant ON curation_log(tenant, review_id);
CREATE INDEX IF NOT EXISTS ix_rel_object ON relationships(tenant, object_kind, object_id);
CREATE INDEX IF NOT EXISTS ix_rel_subject ON relationships(tenant, subject_kind, subject_id);
"""


class Database:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._local = threading.local()
        # For in-memory DBs each connection is isolated, so keep one shared
        # connection guarded by a lock. For file DBs use per-thread conns.
        self._is_memory = path == ":memory:"
        self._lock = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._shared = None
        self._init_schema()

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(
            "PRAGMA journal_mode=WAL" if not self._is_memory else "PRAGMA journal_mode=MEMORY"
        )
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def conn(self) -> sqlite3.Connection:
        if self._is_memory:
            if self._shared is None:
                self._shared = self._new_conn()
            return self._shared
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._new_conn()
            self._local.conn = c
        return c

    # Columns added after a table first shipped: (table, column, DDL type). A
    # database created by an earlier release gets them via ALTER TABLE so an
    # existing ``KF_DB`` keeps working without a rebuild.
    _MIGRATIONS = (("documents", "meta", "TEXT"),)

    def _init_schema(self) -> None:
        with self._lock:
            conn = self.conn()
            conn.executescript(SCHEMA)
            for table, column, ddl in self._MIGRATIONS:
                cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn().execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn().execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()):
        rows = self.query(sql, params)
        return rows[0] if rows else None
