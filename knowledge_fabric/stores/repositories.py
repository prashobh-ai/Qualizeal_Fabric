"""Repositories: the ONLY way application code touches stored data.

Every method takes ``tenant`` and injects it into the SQL WHERE clause. The
``_guard`` helper fails closed on a missing/empty tenant, so a cross-tenant
query is impossible to express by accident (invariant I5). This is the
"guaranteed filter" the spec allows in place of Postgres RLS.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..contracts.types import (
    Coordinate, CoordinateKind, Document, GraphEdge, GraphNode, Passage,
    Provenance,
)
from .db import Database


def _guard(tenant: str) -> str:
    if not tenant or not isinstance(tenant, str):
        raise PermissionError("tenant scope is required on every store call (I5)")
    return tenant


def _acl_json(acl: list[str]) -> str:
    return json.dumps(sorted(set(acl)))


class DocumentRepo:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, doc: Document) -> None:
        _guard(doc.tenant)
        self.db.execute(
            """INSERT INTO documents(id,tenant,source,source_version,content_hash,type,
               language,title,uri,ingested_at,status,current_version,acl)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET source_version=excluded.source_version,
               content_hash=excluded.content_hash, status=excluded.status,
               current_version=excluded.current_version, acl=excluded.acl""",
            (doc.id, doc.tenant, doc.source, doc.source_version, doc.content_hash,
             doc.type, doc.language, doc.title, doc.uri, doc.ingested_at, doc.status,
             doc.current_version, _acl_json(doc.acl)),
        )

    def by_hash(self, tenant: str, content_hash: str) -> Optional[dict]:
        _guard(tenant)
        r = self.db.one("SELECT * FROM documents WHERE tenant=? AND content_hash=? AND status='active'",
                        (tenant, content_hash))
        return dict(r) if r else None

    def by_source_uri(self, tenant: str, source: str, uri: str) -> Optional[dict]:
        _guard(tenant)
        r = self.db.one(
            "SELECT * FROM documents WHERE tenant=? AND source=? AND uri=? AND status='active'",
            (tenant, source, uri))
        return dict(r) if r else None

    def get(self, tenant: str, doc_id: str) -> Optional[dict]:
        _guard(tenant)
        r = self.db.one("SELECT * FROM documents WHERE tenant=? AND id=?", (tenant, doc_id))
        return dict(r) if r else None

    def tombstone(self, tenant: str, doc_id: str) -> None:
        _guard(tenant)
        self.db.execute("UPDATE documents SET status='tombstoned' WHERE tenant=? AND id=?",
                        (tenant, doc_id))

    def list(self, tenant: str) -> list[dict]:
        _guard(tenant)
        return [dict(r) for r in self.db.query(
            "SELECT * FROM documents WHERE tenant=? AND status='active' ORDER BY ingested_at DESC",
            (tenant,))]


class PassageRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, p: Passage, index_version: int, acl: list[str]) -> None:
        _guard(p.tenant)
        self.db.execute(
            """INSERT INTO passages(id,tenant,document_id,text,abstract,overview,
               coord_kind,coord_locator,prov_hash,prov_source,prov_version,version,
               superseded_by,index_version,acl) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET text=excluded.text, index_version=excluded.index_version""",
            (p.id, p.tenant, p.document_id, p.text, p.abstract, p.overview,
             p.coordinate.kind.value, json.dumps(p.coordinate.locator),
             p.provenance.content_hash, p.provenance.source, p.provenance.source_version,
             p.version, p.superseded_by, index_version, _acl_json(acl)),
        )

    def _mk(self, r) -> Passage:
        return Passage(
            id=r["id"], tenant=r["tenant"], document_id=r["document_id"], text=r["text"],
            abstract=r["abstract"], overview=r["overview"],
            coordinate=Coordinate(CoordinateKind(r["coord_kind"]), json.loads(r["coord_locator"])),
            provenance=Provenance(r["prov_hash"], r["prov_source"], r["prov_version"]),
            version=r["version"], superseded_by=r["superseded_by"],
        )

    def get(self, tenant: str, passage_id: str) -> Optional[Passage]:
        _guard(tenant)
        r = self.db.one("SELECT * FROM passages WHERE tenant=? AND id=?", (tenant, passage_id))
        return self._mk(r) if r else None

    def acl_of(self, tenant: str, passage_id: str) -> list[str]:
        _guard(tenant)
        r = self.db.one("SELECT acl FROM passages WHERE tenant=? AND id=?", (tenant, passage_id))
        return json.loads(r["acl"]) if r else []

    def for_tenant(self, tenant: str, index_version: Optional[int] = None) -> list[Passage]:
        _guard(tenant)
        if index_version is None:
            rows = self.db.query(
                "SELECT * FROM passages WHERE tenant=? AND superseded_by IS NULL", (tenant,))
        else:
            rows = self.db.query(
                "SELECT * FROM passages WHERE tenant=? AND index_version=? AND superseded_by IS NULL",
                (tenant, index_version))
        return [self._mk(r) for r in rows]

    def by_document(self, tenant: str, document_id: str) -> list[Passage]:
        _guard(tenant)
        rows = self.db.query("SELECT * FROM passages WHERE tenant=? AND document_id=?",
                             (tenant, document_id))
        return [self._mk(r) for r in rows]

    def supersede_document(self, tenant: str, document_id: str, new_version: int) -> None:
        _guard(tenant)
        self.db.execute(
            "UPDATE passages SET superseded_by='v'||? WHERE tenant=? AND document_id=? AND superseded_by IS NULL",
            (str(new_version), tenant, document_id))

    def delete_document_passages(self, tenant: str, document_id: str) -> list[str]:
        _guard(tenant)
        ids = [r["id"] for r in self.db.query(
            "SELECT id FROM passages WHERE tenant=? AND document_id=?", (tenant, document_id))]
        self.db.execute("DELETE FROM passages WHERE tenant=? AND document_id=?", (tenant, document_id))
        return ids

    def count(self, tenant: str) -> int:
        _guard(tenant)
        r = self.db.one("SELECT COUNT(*) c FROM passages WHERE tenant=? AND superseded_by IS NULL", (tenant,))
        return r["c"] if r else 0


class GraphRepo:
    def __init__(self, db: Database):
        self.db = db

    def upsert_node(self, n: GraphNode) -> None:
        _guard(n.tenant)
        self.db.execute(
            """INSERT INTO graph_nodes(id,tenant,canonical_key,type,labels,provenance)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET labels=excluded.labels""",
            (n.id, n.tenant, n.canonical_key, n.type, json.dumps(n.labels),
             json.dumps([p.__dict__ for p in n.provenance], default=str)))

    def resolve(self, tenant: str, canonical_key: str, type_: str) -> Optional[dict]:
        _guard(tenant)
        r = self.db.one("SELECT * FROM graph_nodes WHERE tenant=? AND canonical_key=? AND type=?",
                        (tenant, canonical_key, type_))
        return dict(r) if r else None

    def upsert_edge(self, e: GraphEdge) -> None:
        _guard(e.tenant)
        self.db.execute(
            """INSERT INTO graph_edges(id,tenant,src,dst,relation,typed_fact,weight,
               contextual_weight,provenance,conflict_flag) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET weight=excluded.weight,
               contextual_weight=excluded.contextual_weight, conflict_flag=excluded.conflict_flag""",
            (e.id, e.tenant, e.src, e.dst, e.relation, json.dumps(e.typed_fact),
             e.weight, e.contextual_weight,
             json.dumps([p.__dict__ for p in e.provenance], default=str),
             1 if e.conflict_flag else 0))

    def neighbors(self, tenant: str, node_id: str) -> list[dict]:
        _guard(tenant)
        rows = self.db.query(
            "SELECT * FROM graph_edges WHERE tenant=? AND (src=? OR dst=?)",
            (tenant, node_id, node_id))
        return [dict(r) for r in rows]

    def node_for_passage(self, tenant: str, passage_id: str) -> list[str]:
        """Nodes whose provenance references this passage (used for graph expansion)."""
        _guard(tenant)
        rows = self.db.query("SELECT id, provenance FROM graph_nodes WHERE tenant=?", (tenant,))
        out = []
        for r in rows:
            if passage_id in (r["provenance"] or ""):
                out.append(r["id"])
        return out

    def contradictions(self, tenant: str) -> int:
        _guard(tenant)
        r = self.db.one("SELECT COUNT(*) c FROM graph_edges WHERE tenant=? AND conflict_flag=1", (tenant,))
        return r["c"] if r else 0

    def counts(self, tenant: str) -> tuple[int, int]:
        _guard(tenant)
        n = self.db.one("SELECT COUNT(*) c FROM graph_nodes WHERE tenant=?", (tenant,))["c"]
        e = self.db.one("SELECT COUNT(*) c FROM graph_edges WHERE tenant=?", (tenant,))["c"]
        return n, e


class AuditRepo:
    def __init__(self, db: Database):
        self.db = db

    def write(self, tenant: str, subject: str, is_agent: bool, action: str,
              resource: str, decision: str, trace_id: str, at: int) -> None:
        _guard(tenant)
        self.db.execute(
            """INSERT INTO audit_log(tenant,subject,is_agent,action,resource,decision,trace_id,at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (tenant, subject, 1 if is_agent else 0, action, resource, decision, trace_id, at))

    def for_tenant(self, tenant: str, limit: int = 100) -> list[dict]:
        _guard(tenant)
        return [dict(r) for r in self.db.query(
            "SELECT * FROM audit_log WHERE tenant=? ORDER BY id DESC LIMIT ?", (tenant, limit))]

    def for_trace(self, tenant: str, trace_id: str) -> list[dict]:
        _guard(tenant)
        return [dict(r) for r in self.db.query(
            "SELECT * FROM audit_log WHERE tenant=? AND trace_id=?", (tenant, trace_id))]


class CurationRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, tenant: str, item: str, kind: str, at: int) -> None:
        _guard(tenant)
        from ..contracts.types import new_id
        self.db.execute(
            "INSERT INTO curation_queue(id,tenant,item,kind,status,assignee,resolution,at) VALUES(?,?,?,?,?,?,?,?)",
            (new_id("cur_"), tenant, item, kind, "open", None, None, at))

    def list(self, tenant: str, kind: Optional[str] = None) -> list[dict]:
        _guard(tenant)
        if kind:
            return [dict(r) for r in self.db.query(
                "SELECT * FROM curation_queue WHERE tenant=? AND kind=? AND status='open' ORDER BY at DESC",
                (tenant, kind))]
        return [dict(r) for r in self.db.query(
            "SELECT * FROM curation_queue WHERE tenant=? AND status='open' ORDER BY at DESC", (tenant,))]
