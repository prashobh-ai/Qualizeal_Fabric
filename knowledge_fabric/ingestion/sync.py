"""Automated ingestion / sync runner (roadmap WS1).

Drives connectors on a schedule or on demand: load the saved cursor, pull only
the delta (change detection), apply tombstones for deleted source items, land
the rest through the same 7-step pipeline, and persist the new cursor. A
re-sync of unchanged content costs nothing (idempotent-by-hash, I9). Source
freshness (minutes since last sync) is recorded for the dashboard.
"""
from __future__ import annotations

import time

from ..contracts.types import now_ms
from ..connectors import registry
from .intake import Intake, IngestWorker


class SyncManager:
    def __init__(self, platform):
        self.p = platform
        self.intake = Intake(platform)
        self.worker = IngestWorker(platform, self.intake)

    def _cursor(self, tenant, source):
        r = self.p.db.one("SELECT cursor FROM connector_cursors WHERE tenant=? AND source=?",
                          (tenant, source))
        return r["cursor"] if r else None

    def _save_cursor(self, tenant, source, cursor, items):
        self.p.db.execute(
            """INSERT INTO connector_cursors(tenant,source,cursor,last_sync,items)
               VALUES(?,?,?,?,?)
               ON CONFLICT(tenant,source) DO UPDATE SET cursor=excluded.cursor,
               last_sync=excluded.last_sync, items=connector_cursors.items+excluded.items""",
            (tenant, source, str(cursor), now_ms(), items))

    def _tombstone(self, tenant, source, uris):
        for uri in uris:
            doc = self.p.documents.by_source_uri(tenant, source, uri)
            if doc:
                ids = self.p.passages.delete_document_passages(tenant, doc["id"])
                self.p.vindex.delete(tenant, ids)
                self.p.documents.tombstone(tenant, doc["id"])

    def sync(self, tenant: str, source: str, config: dict, ontology: str = "quality-assurance",
             **kwargs) -> dict:
        connector = registry.build(source, tenant, config, **kwargs)
        cursor = self._cursor(tenant, source)
        items, next_cursor = connector.pull(cursor)
        tombstoned = set(getattr(connector, "last_tombstones", []))
        for it in items:
            tombstoned |= set(it.meta.get("tombstones", []))
        if tombstoned:
            self._tombstone(tenant, source, tombstoned)
        submitted = 0
        for it in items:
            it.meta.setdefault("ontology", ontology)
            self.intake.submit(it)
            submitted += 1
        results = self.worker.drain()
        self._save_cursor(tenant, source, next_cursor, submitted)
        ingested = [r for r in results if r["status"] in ("ok", "updated")]
        return {"tenant": tenant, "source": source, "pulled": submitted,
                "ingested": len(ingested), "noops": len([r for r in results if r["status"] == "noop"]),
                "tombstoned": len(tombstoned), "cursor": next_cursor,
                "scopes": connector.scopes(), "read_only": connector.read_only}

    def run_all(self, tenant: str, sources: list[dict]) -> list[dict]:
        """sources: [{source, config, ontology?, records?}] — the tenant's source set."""
        out = []
        for spec in sources:
            kw = {"records": spec["records"]} if "records" in spec else {}
            out.append(self.sync(tenant, spec["source"], spec.get("config", {}),
                                  spec.get("ontology", "quality-assurance"), **kw))
        return out

    def source_health(self, tenant: str) -> list[dict]:
        rows = self.p.db.query(
            "SELECT source, cursor, last_sync, items FROM connector_cursors WHERE tenant=?", (tenant,))
        now = now_ms()
        return [{"source": r["source"], "items": r["items"],
                 "freshness_minutes": round((now - (r["last_sync"] or now)) / 60000, 1),
                 "last_sync_ms": r["last_sync"]} for r in rows]
