"""Assemble the ``documents`` block of ``data/facts.json`` from the platform (T42).

The repository / Jira / Confluence / table blocks are written by their own
tracks from the live sources; the document counts are the one fact only the
platform knows (what was actually ingested, per tenant), so this module
computes them from the document store and merges them into the shared file
without touching any other key.
"""

from __future__ import annotations

import datetime as _dt

from .. import fabric_data as fd


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def area_of(doc: dict) -> str:
    """The *area* a document belongs to — its connector/source family, which is
    how people ask ("how many GitHub documents", "how many handbook pages")."""
    source = (doc.get("source") or "").strip().lower()
    uri = (doc.get("uri") or "").lower()
    if source:
        return source
    if "://" in uri:
        return uri.split("://", 1)[0]
    return "other"


def build_documents_facts(platform, tenant: str) -> dict:
    """``{"by_area": {...}, "by_type": {...}, "total": n, "as_of": iso}`` for
    the active documents of ``tenant``."""
    by_area: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total = 0
    for doc in platform.documents.list(tenant):
        total += 1
        a = area_of(doc)
        t = (doc.get("type") or "unknown").lower()
        by_area[a] = by_area.get(a, 0) + 1
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "by_area": dict(sorted(by_area.items())),
        "by_type": dict(sorted(by_type.items())),
        "total": total,
        "as_of": _now_iso(),
    }


def write_documents_facts(platform, tenant: str) -> str:
    """Merge the document counts into ``data/facts.json["documents"]`` (other
    keys are preserved verbatim). Returns the file path."""
    path = fd.data_path("facts.json", mkdir=True)
    current = fd.read_json(path, default={}) or {}
    current["documents"] = build_documents_facts(platform, tenant)
    return fd.write_json(path, current)
