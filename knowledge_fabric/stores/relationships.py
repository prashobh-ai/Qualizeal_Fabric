"""The cross-source relationships store (T99).

One tenant-scoped edge table linking entities discovered across sources — a Jira
key mentioned by a commit or pull request, a document referencing an issue, and
so on. Edges are directed ``subject --relation--> object`` and carry the
evidence (the mentioning artefact's kind, id, url, title) so a corroboration can
cite the link, not merely assert it.

Edges are **idempotent**: the id is a deterministic hash of
``(tenant, subject, relation, object, evidence)`` so re-scanning a fabric never
duplicates a link. Every read and write is tenant-filtered (invariant I5).
"""

from __future__ import annotations

import hashlib

from ..contracts.types import now_ms
from .repositories import _guard


def edge_id(tenant: str, subj: tuple, relation: str, obj: tuple, evidence_id: str) -> str:
    raw = "|".join([tenant, *subj, relation, *obj, evidence_id or ""])
    return "rel_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]  # noqa: S324 (id, not crypto)


class RelationshipRepo:
    def __init__(self, db):
        self.db = db

    def add(
        self,
        tenant: str,
        subject_kind: str,
        subject_id: str,
        relation: str,
        object_kind: str,
        object_id: str,
        *,
        evidence_kind: str = "",
        evidence_id: str = "",
        evidence_url: str = "",
        evidence_title: str = "",
    ) -> str:
        """Record one directed edge; returns its stable id. Re-adding the same
        edge (same subject/relation/object/evidence) is a no-op."""
        _guard(tenant)
        eid = edge_id(
            tenant,
            (subject_kind, subject_id),
            relation,
            (object_kind, object_id),
            evidence_id or evidence_url,
        )
        self.db.execute(
            """INSERT INTO relationships(id,tenant,subject_kind,subject_id,relation,
               object_kind,object_id,evidence_kind,evidence_id,evidence_url,evidence_title,at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET evidence_url=excluded.evidence_url,
               evidence_title=excluded.evidence_title, at=excluded.at""",
            (
                eid,
                tenant,
                subject_kind,
                subject_id,
                relation,
                object_kind,
                object_id,
                evidence_kind,
                evidence_id,
                evidence_url,
                evidence_title,
                now_ms(),
            ),
        )
        return eid

    def _rows(self, sql: str, params: tuple) -> list[dict]:
        return [dict(r) for r in self.db.query(sql, params)]

    def mentions_of(
        self, tenant: str, object_kind: str, object_id: str, subject_kinds: list[str] | None = None
    ) -> list[dict]:
        """Every edge pointing at ``(object_kind, object_id)`` — e.g. the commits
        and pull requests that reference a Jira key. Optionally restrict the
        subject kinds (``["commit", "pull_request"]`` for code evidence)."""
        _guard(tenant)
        rows = self._rows(
            "SELECT * FROM relationships WHERE tenant=? AND object_kind=? AND object_id=? "
            "ORDER BY at DESC",
            (tenant, object_kind, object_id),
        )
        if subject_kinds is not None:
            keep = set(subject_kinds)
            rows = [r for r in rows if r["subject_kind"] in keep]
        return rows

    def neighbours(self, tenant: str, kind: str, ident: str) -> list[dict]:
        """Every edge touching an entity, either direction (the graph lookup)."""
        _guard(tenant)
        return self._rows(
            "SELECT * FROM relationships WHERE tenant=? AND "
            "((subject_kind=? AND subject_id=?) OR (object_kind=? AND object_id=?)) "
            "ORDER BY at DESC",
            (tenant, kind, ident, kind, ident),
        )

    def count(self, tenant: str) -> int:
        _guard(tenant)
        r = self.db.one("SELECT COUNT(*) c FROM relationships WHERE tenant=?", (tenant,))
        return int(r["c"]) if r else 0

    def clear(self, tenant: str) -> None:
        """Drop this tenant's edges (a scan rebuilds them idempotently)."""
        _guard(tenant)
        self.db.execute("DELETE FROM relationships WHERE tenant=?", (tenant,))
