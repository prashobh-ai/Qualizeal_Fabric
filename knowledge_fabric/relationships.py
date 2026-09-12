"""Cross-source relationship discovery (T99) — build the edge graph from mentions.

Every source already lands in one fabric as documents + cited passages. This
scan reads those documents and records the cross-source links they *mention*: a
commit or pull request whose message names a Jira key, a document that cites an
issue. The edges persist in the ``relationships`` store, so cross-source
verification is a graph lookup, not a re-scan of every source.

Run at the end of ingestion (``scripts/ingest.py``) or on demand; it is
idempotent — it rebuilds the tenant's edges from the current fabric each time.

Only mentions of **known** Jira issues (an issue actually ingested, or named in
the Jira facts) become edges, so noise like ``UTF-8`` never turns into a link.
The design generalises: adding a source means teaching :func:`classify` its uri
shape — the scan, the store and the verifier are unchanged.
"""

from __future__ import annotations

import json
import re

# a Jira issue key: a 2+ char uppercase/digit project, a dash, a number
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")

_JIRA_URI = re.compile(r"^jira://[^/]+/([A-Z][A-Z0-9]{1,9}-\d+)$")
_GH_URI = re.compile(r"^github://(?P<repo>[^/]+/[^/]+)/(?P<kind>pulls|commits|issues)/(?P<ref>.+)$")
_CONF_URI = re.compile(r"^confluence://(?P<space>[^/]+)/(?P<pid>[^/]+)$")

__all__ = ["ISSUE_KEY", "classify", "known_issue_keys", "scan"]


def classify(uri: str, doc_id: str) -> tuple[str, str]:
    """``(kind, id)`` for a document from its uri: issue / pull_request / commit /
    gh_issue / page / document."""
    uri = uri or ""
    m = _JIRA_URI.match(uri)
    if m:
        return "issue", m.group(1)
    m = _GH_URI.match(uri)
    if m:
        repo, kind, ref = m.group("repo"), m.group("kind"), m.group("ref")
        if kind == "pulls":
            return "pull_request", f"{repo}#{ref}"
        if kind == "commits":
            return "commit", f"{repo}@{ref[:12]}"
        return "gh_issue", f"{repo}!{ref}"
    m = _CONF_URI.match(uri)
    if m:
        return "page", f"{m.group('space')}/{m.group('pid')}"
    return "document", doc_id


def known_issue_keys(platform, tenant: str) -> set[str]:
    """Every Jira issue key the fabric knows — from ingested Jira documents and
    from the Jira facts (project prefixes make a mention plausible)."""
    keys: set[str] = set()
    for d in platform.documents.list(tenant):
        m = _JIRA_URI.match(d.get("uri") or "")
        if m:
            keys.add(m.group(1))
    return keys


def _doc_url(doc: dict) -> str:
    try:
        meta = json.loads(doc.get("meta") or "{}")
    except (TypeError, ValueError):
        meta = {}
    return str(meta.get("citation_url") or doc.get("uri") or "")


def _doc_text(platform, tenant: str, doc_id: str, cap: int = 20000) -> str:
    parts = [
        p.text
        for p in platform.passages.by_document(tenant, doc_id)
        if p.superseded_by is None and p.text
    ]
    return "\n".join(parts)[:cap]


def scan(platform, tenant: str) -> dict:
    """Rebuild the tenant's cross-source edges from the current fabric.

    Returns ``{edges, documents_scanned, issue_keys}``. Idempotent: existing
    edges are cleared first, then re-derived, so a re-run reflects the fabric as
    it stands."""
    store = platform.relationships
    store.clear(tenant)
    known = known_issue_keys(platform, tenant)
    docs = platform.documents.list(tenant)
    edges = 0
    for doc in docs:
        subj_kind, subj_id = classify(doc.get("uri") or "", doc["id"])
        title = doc.get("title") or ""
        text = title + "\n" + _doc_text(platform, tenant, doc["id"])
        url = _doc_url(doc)
        seen: set[str] = set()
        for key in ISSUE_KEY.findall(text):
            if key not in known or key == subj_id or key in seen:
                continue
            seen.add(key)
            store.add(
                tenant,
                subj_kind,
                subj_id,
                "mentions",
                "issue",
                key,
                evidence_kind=subj_kind,
                evidence_id=doc["id"],
                evidence_url=url,
                evidence_title=title,
            )
            edges += 1
    return {"edges": edges, "documents_scanned": len(docs), "issue_keys": sorted(known)}
