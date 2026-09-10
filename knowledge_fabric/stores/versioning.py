"""Data versioning: document history, diff, rollback, dataset versions, lineage.

Stage-2 Section B. Every function takes ``platform`` and ``tenant`` and every
query is tenant-filtered (invariant I5, same guard as ``repositories``).

Model
-----
* The ingestion pipeline already keeps *incremental* history in the
  ``passages`` table: a changed document supersedes its old passages
  (``superseded_by = 'v<n>'``) and lands new ones with ``version = n``.
  Superseded passages keep their embeddings, so re-activating them is a
  metadata flip, not a re-index.
* ``document_versions`` is the explicit ledger: one row per
  (document, version) with the content hash, the passage ids that make up
  that version and the source version. ``record_version`` writes it (the
  pipeline calls it after the chunk step; ``backfill`` reconstructs it from
  the passages table for documents ingested before that hook existed).
* ``dataset_versions`` is a tenant-wide monotonically increasing counter
  bumped by the curator/ingestion whenever the corpus changes, so every
  answer can say which dataset version it was grounded on.

Rollback invariant: after ``rollback`` exactly one version's passages are
live (``superseded_by IS NULL``) for the document, the document's
``content_hash``/``source_version`` are those of the restored version,
``current_version`` is a fresh max+1, a ``document_versions`` row records the
restore, and the action is audited.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..contracts.types import new_id, now_ms
from .repositories import _guard

__all__ = [
    "record_version",
    "history",
    "diff",
    "rollback",
    "bump_dataset",
    "current_dataset",
    "lineage",
    "backfill",
    "list_dataset_versions",
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _version_row_id(document_id: str, version: int) -> str:
    """Deterministic ledger row id so re-recording a version is idempotent."""
    return f"dv_{document_id}_{int(version)}"


def _passage_versions(platform, tenant: str, document_id: str) -> dict[int, dict[str, Any]]:
    """Versions reconstructed from the passages table (fallback / backfill).

    Returns {version: {"passage_ids": [...], "content_hash": str, "source_version": str}}
    with passage ids in insertion (rowid) order for determinism.
    """
    rows = platform.db.query(
        """SELECT id, version, prov_hash, prov_version FROM passages
           WHERE tenant=? AND document_id=? ORDER BY version, rowid""",
        (tenant, document_id),
    )
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        v = int(r["version"] or 1)
        slot = out.setdefault(
            v,
            {
                "passage_ids": [],
                "content_hash": r["prov_hash"],
                "source_version": r["prov_version"],
            },
        )
        slot["passage_ids"].append(r["id"])
    return out


def _version_row(platform, tenant: str, document_id: str, version: int) -> dict | None:
    r = platform.db.one(
        "SELECT * FROM document_versions WHERE tenant=? AND document_id=? AND version=?",
        (tenant, document_id, int(version)),
    )
    return dict(r) if r else None


def _version_passage_ids(platform, tenant: str, document_id: str, version: int) -> dict | None:
    """Passage ids + hash + source_version for a version: ledger first, passages fallback."""
    row = _version_row(platform, tenant, document_id, version)
    if row:
        return {
            "passage_ids": json.loads(row["passage_ids"] or "[]"),
            "content_hash": row["content_hash"],
            "source_version": row["source_version"],
        }
    return _passage_versions(platform, tenant, document_id).get(int(version))


def _texts_for(platform, tenant: str, passage_ids: list[str]) -> list[str]:
    """Passage texts in the given id order (tenant-filtered)."""
    if not passage_ids:
        return []
    texts: dict[str, str] = {}
    # chunk the IN list to stay well under SQLite's variable limit
    for i in range(0, len(passage_ids), 500):
        chunk = passage_ids[i : i + 500]
        marks = ",".join("?" for _ in chunk)
        for r in platform.db.query(
            f"SELECT id, text FROM passages WHERE tenant=? AND id IN ({marks})", (tenant, *chunk)
        ):
            texts[r["id"]] = r["text"]
    return [texts[pid] for pid in passage_ids if pid in texts]


# --------------------------------------------------------------------------
# document versions
# --------------------------------------------------------------------------
def record_version(
    platform,
    tenant: str,
    document_id: str,
    version: int,
    content_hash: str,
    passage_ids: list[str],
    source_version: str,
) -> None:
    """Write (or refresh) the ledger row for one document version.

    Idempotent per (document, version): the row id is deterministic and an
    existing row is replaced, so the pipeline may call this on every ingest.
    """
    _guard(tenant)
    if not document_id:
        raise ValueError("document_id is required")
    version = int(version)
    if version < 1:
        raise ValueError("version must be >= 1")
    platform.db.execute(
        """INSERT INTO document_versions(id,tenant,document_id,version,content_hash,created_at,
           passage_ids,source_version) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET content_hash=excluded.content_hash,
           passage_ids=excluded.passage_ids, source_version=excluded.source_version""",
        (
            _version_row_id(document_id, version),
            tenant,
            document_id,
            version,
            content_hash,
            now_ms(),
            json.dumps(list(passage_ids)),
            str(source_version) if source_version is not None else None,
        ),
    )


def backfill(platform, tenant: str, document_id: str) -> int:
    """Reconstruct missing ledger rows from the passages table.

    Returns the number of rows created. Existing rows are left untouched, so
    this is safe to call before any history/diff/rollback on a document that
    was ingested before the pipeline recorded versions.
    """
    _guard(tenant)
    created = 0
    for v, info in sorted(_passage_versions(platform, tenant, document_id).items()):
        if _version_row(platform, tenant, document_id, v) is None:
            record_version(
                platform,
                tenant,
                document_id,
                v,
                info["content_hash"],
                info["passage_ids"],
                info["source_version"],
            )
            created += 1
    return created


def history(platform, tenant: str, document_id: str) -> list[dict]:
    """All recorded versions of a document, oldest first.

    Each item: {version, content_hash, created_at, passages, source_version}.
    Ledger rows are authoritative; versions only present in the passages
    table (pre-hook ingests) are merged in so history is never silently short.
    """
    _guard(tenant)
    out: dict[int, dict] = {}
    for r in platform.db.query(
        """SELECT version, content_hash, created_at, passage_ids, source_version
               FROM document_versions WHERE tenant=? AND document_id=? ORDER BY version""",
        (tenant, document_id),
    ):
        out[int(r["version"])] = {
            "version": int(r["version"]),
            "content_hash": r["content_hash"],
            "created_at": r["created_at"],
            "passages": len(json.loads(r["passage_ids"] or "[]")),
            "source_version": r["source_version"],
            "recorded": True,
        }
    for v, info in _passage_versions(platform, tenant, document_id).items():
        if v not in out:
            out[v] = {
                "version": v,
                "content_hash": info["content_hash"],
                "created_at": None,
                "passages": len(info["passage_ids"]),
                "source_version": info["source_version"],
                "recorded": False,
            }
    return [out[v] for v in sorted(out)]


def diff(platform, tenant: str, document_id: str, v_from: int, v_to: int) -> dict:
    """Passage-level diff between two versions of a document.

    Compares passage *texts* as multisets: {"added": [...], "removed": [...],
    "unchanged": n}. Ordering follows the passage order of each version, so
    the output is deterministic. Raises KeyError for an unknown version.
    """
    _guard(tenant)
    a = _version_passage_ids(platform, tenant, document_id, v_from)
    b = _version_passage_ids(platform, tenant, document_id, v_to)
    if a is None:
        raise KeyError(f"version {v_from} of {document_id} not found")
    if b is None:
        raise KeyError(f"version {v_to} of {document_id} not found")
    from_texts = _texts_for(platform, tenant, a["passage_ids"])
    to_texts = _texts_for(platform, tenant, b["passage_ids"])
    from_count, to_count = Counter(from_texts), Counter(to_texts)
    common = from_count & to_count
    removed = _leftover(from_texts, from_count - to_count)
    added = _leftover(to_texts, to_count - from_count)
    return {
        "added": added,
        "removed": removed,
        "unchanged": sum(common.values()),
        "from_version": int(v_from),
        "to_version": int(v_to),
    }


def _leftover(ordered: list[str], surplus: Counter) -> list[str]:
    """Items of ``ordered`` not matched on the other side, in original order."""
    left = dict(surplus)
    out = []
    for t in ordered:
        if left.get(t, 0) > 0:
            out.append(t)
            left[t] -= 1
    return out


def rollback(platform, tenant: str, document_id: str, to_version: int, by_subject: str) -> dict:
    """Restore ``to_version`` of a document as the live version.

    Steps (all tenant-scoped):
      1. make sure every version present in the passages table is in the ledger;
      2. compute new_version = max(known versions, current_version) + 1;
      3. supersede the currently live passages (``superseded_by='v<new>'``);
      4. re-activate the passages of ``to_version`` (``superseded_by=NULL``),
         re-embedding any that lost their vector;
      5. point the document at the restored content (hash, source_version,
         current_version=new_version);
      6. record a ledger row for new_version (same passage ids as to_version);
      7. invalidate the tenant's caches and write an audit entry.

    Returns {"new_version": int, "reactivated": int, "superseded": int,
    "restored_version": int}. Raises KeyError for unknown document/version.
    """
    _guard(tenant)
    if not by_subject:
        raise ValueError("by_subject is required for an audited rollback")
    doc = platform.documents.get(tenant, document_id)
    if not doc:
        raise KeyError(f"document {document_id} not found in tenant {tenant}")
    backfill(platform, tenant, document_id)
    target = _version_passage_ids(platform, tenant, document_id, to_version)
    if target is None or not target["passage_ids"]:
        raise KeyError(f"version {to_version} of {document_id} not found")

    known = [h["version"] for h in history(platform, tenant, document_id)]
    new_version = max([doc.get("current_version") or 1, *known]) + 1

    # the restore must be complete or not at all: verify before touching anything
    ids = list(target["passage_ids"])
    present = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" for _ in chunk)
        present += platform.db.one(
            f"SELECT COUNT(*) c FROM passages WHERE tenant=? AND document_id=? AND id IN ({marks})",
            (tenant, document_id, *chunk),
        )["c"]
    if present != len(ids):
        raise KeyError(
            f"version {to_version} of {document_id} is incomplete "
            f"({present}/{len(ids)} passages present); rollback aborted"
        )

    # 3. supersede whatever is live now
    live_before = [
        r["id"]
        for r in platform.db.query(
            "SELECT id FROM passages WHERE tenant=? AND document_id=? AND superseded_by IS NULL",
            (tenant, document_id),
        )
    ]
    platform.passages.supersede_document(tenant, document_id, new_version)

    # 4. re-activate the target version's passages
    reactivated = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" for _ in chunk)
        cur = platform.db.execute(
            (
                f"UPDATE passages SET superseded_by=NULL WHERE tenant=? AND document_id=? "
                f"AND id IN ({marks})"
            ),
            (tenant, document_id, *chunk),
        )
        reactivated += max(cur.rowcount, 0)
    _ensure_embeddings(platform, tenant, ids)

    # 5. document points at the restored content
    platform.db.execute(
        """UPDATE documents SET current_version=?, content_hash=?, source_version=?
           WHERE tenant=? AND id=?""",
        (new_version, target["content_hash"], target["source_version"], tenant, document_id),
    )

    # 6. ledger row for the restore
    record_version(
        platform,
        tenant,
        document_id,
        new_version,
        target["content_hash"],
        ids,
        target["source_version"],
    )

    # 7. caches + audit
    cache = getattr(platform, "cache", None)
    if cache is not None and hasattr(cache, "invalidate"):
        cache.invalidate(tenant)
    platform.audit.write(
        tenant,
        by_subject,
        False,
        "version.rollback",
        f"document:{document_id}:v{int(to_version)}->v{new_version}",
        "allow",
        new_id("rollback_"),
        now_ms(),
    )
    return {
        "new_version": new_version,
        "reactivated": reactivated,
        "superseded": len(live_before),
        "restored_version": int(to_version),
    }


def _ensure_embeddings(platform, tenant: str, passage_ids: list[str]) -> int:
    """Re-embed reactivated passages whose vectors were deleted. Returns count re-embedded."""
    model_id = getattr(platform.vindex, "model_id", None)
    missing = []
    for pid in passage_ids:
        if model_id is None:
            r = platform.db.one(
                "SELECT 1 FROM embeddings WHERE tenant=? AND passage_id=?", (tenant, pid)
            )
        else:
            r = platform.db.one(
                "SELECT 1 FROM embeddings WHERE tenant=? AND passage_id=? AND model_id=?",
                (tenant, pid, model_id),
            )
        if not r:
            missing.append(pid)
    if not missing:
        return 0
    texts = _texts_for(platform, tenant, missing)
    vecs = platform.embedder.embed(texts)
    platform.vindex.upsert(
        tenant, [{"passage_id": pid, "vec": v} for pid, v in zip(missing, vecs, strict=False)]
    )
    return len(missing)


# --------------------------------------------------------------------------
# dataset versions (tenant-wide)
# --------------------------------------------------------------------------
def current_dataset(platform, tenant: str) -> int:
    """Highest dataset version for the tenant; 0 if none was ever bumped."""
    _guard(tenant)
    r = platform.db.one("SELECT MAX(version) v FROM dataset_versions WHERE tenant=?", (tenant,))
    return int(r["v"]) if r and r["v"] else 0


def bump_dataset(platform, tenant: str, reason: str) -> int:
    """Create the next dataset version, snapshotting doc/passage counts. Returns it."""
    _guard(tenant)
    nxt = current_dataset(platform, tenant) + 1
    docs = len(platform.documents.list(tenant))
    passages = platform.passages.count(tenant)
    platform.db.execute(
        """INSERT INTO dataset_versions(tenant,version,created_at,reason,doc_count,passage_count)
           VALUES(?,?,?,?,?,?)""",
        (tenant, nxt, now_ms(), reason or "", docs, passages),
    )
    return nxt


def list_dataset_versions(platform, tenant: str, limit: int = 50) -> list[dict]:
    """Newest-first dataset versions: [{version, created_at, reason, doc_count, passage_count}]."""
    _guard(tenant)
    return [
        dict(r)
        for r in platform.db.query(
            """SELECT version, created_at, reason, doc_count, passage_count FROM dataset_versions
           WHERE tenant=? ORDER BY version DESC LIMIT ?""",
            (tenant, int(limit)),
        )
    ]


# --------------------------------------------------------------------------
# lineage (I8: every derived artefact links back to its origin)
# --------------------------------------------------------------------------
def lineage(platform, tenant: str, passage_id: str) -> dict | None:
    """Origin of one passage, or None if it is not in this tenant.

    {"passage_id", "document_id", "document_title", "content_hash", "version",
     "live": bool, "superseded_by", "source", "source_version", "uri",
     "coordinate": {"kind", "locator", "render"}}
    """
    _guard(tenant)
    pas = platform.passages.get(tenant, passage_id)
    if not pas:
        return None
    doc = platform.documents.get(tenant, pas.document_id) or {}
    return {
        "passage_id": pas.id,
        "document_id": pas.document_id,
        "document_title": doc.get("title"),
        "content_hash": pas.provenance.content_hash,
        "version": pas.version,
        "live": pas.superseded_by is None,
        "superseded_by": pas.superseded_by,
        "source": pas.provenance.source,
        "source_version": pas.provenance.source_version,
        "uri": doc.get("uri"),
        "coordinate": {
            "kind": pas.coordinate.kind.value,
            "locator": pas.coordinate.locator,
            "render": pas.coordinate.render(),
        },
    }
