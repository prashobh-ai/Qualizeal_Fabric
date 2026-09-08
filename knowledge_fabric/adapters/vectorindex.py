"""Vector index backed by the ``embeddings`` table (pgvector in cloud).

Two invariants are enforced *here*, inside retrieval:
  * I5 — every query is tenant-filtered in SQL.
  * I6 — the ACL filter is applied while selecting candidates, BEFORE any
    ranking. A passage the caller may not see never enters the ranked set,
    so it can never appear in the retrieval trace (Runbook 4.2).
"""
from __future__ import annotations

import json

from ..stores.db import Database
from .embedder import cosine


def _acl_ok(passage_acl: list[str], accessible: list[str]) -> bool:
    return bool(set(passage_acl) & set(accessible))


class SqlVectorIndex:
    def __init__(self, db: Database, model_id: str):
        self.db = db
        self.model_id = model_id

    def upsert(self, tenant: str, items: list[dict]) -> None:
        for it in items:
            self.db.execute(
                """INSERT INTO embeddings(tenant,passage_id,model_id,vec) VALUES(?,?,?,?)
                   ON CONFLICT(tenant,passage_id,model_id) DO UPDATE SET vec=excluded.vec""",
                (tenant, it["passage_id"], self.model_id, json.dumps(it["vec"])))

    def delete(self, tenant: str, ids: list[str]) -> None:
        for pid in ids:
            self.db.execute("DELETE FROM embeddings WHERE tenant=? AND passage_id=?", (tenant, pid))

    def search(self, tenant: str, query_vec: list[float], k: int, acl: list[str]) -> list[tuple[str, float]]:
        rows = self.db.query(
            """SELECT e.passage_id pid, e.vec vec, p.acl acl
               FROM embeddings e JOIN passages p ON p.id=e.passage_id AND p.tenant=e.tenant
               WHERE e.tenant=? AND e.model_id=? AND p.superseded_by IS NULL""",
            (tenant, self.model_id))
        scored = []
        for r in rows:
            if not _acl_ok(json.loads(r["acl"]), acl):     # permission filter, pre-rank
                continue
            scored.append((r["pid"], cosine(query_vec, json.loads(r["vec"]))))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
