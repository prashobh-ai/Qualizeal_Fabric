"""Authoritative-source policy (Stage-2 Section B).

Two independent signals decide how much an evidence passage is trusted:

* **Source rank** — per tenant, per connector/door (``files``, ``confluence``,
  ``jira`` …). Rank 1 is the most authoritative. Defaults live in
  ``DEFAULT_RANKS``; a tenant overrides them in ``source_authority``.
  ``weight_for`` maps a rank to a multiplier that decays gently
  (``1 / (1 + 0.25 * (rank - 1))``) so lower-ranked sources are still
  retrievable, just ordered after their betters when scores are close.
* **Authoritative flag** — a curator marks one *document* as the source of
  truth (``documents.authoritative``); its passages get a 1.5x boost.

``boost`` applies both to a fused retrieval list; ``authoritative_source``
and ``conflicts`` explain the answer's citations to the reader (which
document wins, and where two sources of differing authority both spoke).

Every store query is tenant-filtered (invariant I5) and every mutation is
audited via ``platform.audit.write``.
"""

from __future__ import annotations

from ..contracts.types import new_id, now_ms
from ..stores.repositories import _guard

__all__ = [
    "DEFAULT_RANKS",
    "UNKNOWN_RANK",
    "AUTHORITATIVE_BOOST",
    "set_source_rank",
    "rank_for",
    "weight_for",
    "mark_authoritative",
    "is_authoritative",
    "boost",
    "authoritative_source",
    "conflicts",
    "list_ranks",
]

#: rank 1 = most authoritative. Curated files/uploads first, wikis next,
#: code repositories after, ticket trackers last.
DEFAULT_RANKS: dict[str, int] = {
    "files": 1,
    "confluence": 2,
    "sharepoint": 2,
    "github": 3,
    "jira": 4,
    "drive": 3,
    "upload": 2,
    "cli": 2,
}
#: rank used for a source that is neither configured nor in ``DEFAULT_RANKS``.
UNKNOWN_RANK = 5
#: multiplier applied to passages of a curator-marked authoritative document.
AUTHORITATIVE_BOOST = 1.5
_DECAY = 0.25


def _weight(rank: int) -> float:
    """Rank -> multiplier: 1.0 for rank 1, 0.8 for rank 2, 0.667 for rank 3 …"""
    return round(1.0 / (1.0 + _DECAY * (max(1, int(rank)) - 1)), 6)


# --------------------------------------------------------------------------
# source ranks
# --------------------------------------------------------------------------
def set_source_rank(platform, tenant: str, source: str, rank: int) -> None:
    """Override the rank of one source for a tenant (rank 1 = most authoritative)."""
    _guard(tenant)
    if not source or not isinstance(source, str):
        raise ValueError("source is required")
    try:
        rank = int(rank)
    except (TypeError, ValueError):
        raise ValueError("rank must be an integer >= 1") from None
    if rank < 1:
        raise ValueError("rank must be an integer >= 1")
    platform.db.execute(
        """INSERT INTO source_authority(tenant,source,rank,weight) VALUES(?,?,?,?)
           ON CONFLICT(tenant,source) DO UPDATE SET rank=excluded.rank, weight=excluded.weight""",
        (tenant, source, rank, _weight(rank)),
    )
    cache = getattr(platform, "cache", None)
    if cache is not None and hasattr(cache, "invalidate"):
        cache.invalidate(tenant)


def rank_for(platform, tenant: str, source: str) -> int:
    """Effective rank of a source: tenant override, else default, else UNKNOWN_RANK."""
    _guard(tenant)
    r = platform.db.one(
        "SELECT rank FROM source_authority WHERE tenant=? AND source=?", (tenant, source or "")
    )
    if r and r["rank"]:
        return int(r["rank"])
    return DEFAULT_RANKS.get(source or "", UNKNOWN_RANK)


def weight_for(platform, tenant: str, source: str) -> float:
    """Score multiplier for a source; 1.0 for rank 1, decaying with rank."""
    return _weight(rank_for(platform, tenant, source))


def list_ranks(platform, tenant: str) -> list[dict]:
    """Every known source for the tenant with its effective rank and weight.

    Union of the defaults, the tenant's overrides and the sources of its
    documents; sorted by (rank, source) so rank 1 comes first.
    """
    _guard(tenant)
    sources = set(DEFAULT_RANKS)
    sources |= {
        r["source"]
        for r in platform.db.query("SELECT source FROM source_authority WHERE tenant=?", (tenant,))
    }
    sources |= {
        r["source"]
        for r in platform.db.query(
            "SELECT DISTINCT source FROM documents WHERE tenant=? AND status='active'", (tenant,)
        )
        if r["source"]
    }
    overrides = {
        r["source"]: int(r["rank"])
        for r in platform.db.query(
            "SELECT source, rank FROM source_authority WHERE tenant=?", (tenant,)
        )
    }
    out = []
    for s in sources:
        rank = overrides.get(s, DEFAULT_RANKS.get(s, UNKNOWN_RANK))
        out.append(
            {"source": s, "rank": rank, "weight": _weight(rank), "overridden": s in overrides}
        )
    out.sort(key=lambda x: (x["rank"], x["source"]))
    return out


# --------------------------------------------------------------------------
# authoritative documents
# --------------------------------------------------------------------------
def mark_authoritative(
    platform, tenant: str, document_id: str, flag: bool, by_subject: str
) -> None:
    """Set/clear the authoritative flag on a document and audit the decision."""
    _guard(tenant)
    if not by_subject:
        raise ValueError("by_subject is required for an audited authority change")
    cur = platform.db.execute(
        "UPDATE documents SET authoritative=? WHERE tenant=? AND id=?",
        (1 if flag else 0, tenant, document_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"document {document_id} not found in tenant {tenant}")
    cache = getattr(platform, "cache", None)
    if cache is not None and hasattr(cache, "invalidate"):
        cache.invalidate(tenant)
    platform.audit.write(
        tenant,
        by_subject,
        False,
        "authority.mark" if flag else "authority.unmark",
        f"document:{document_id}",
        "allow",
        new_id("authority_"),
        now_ms(),
    )


def is_authoritative(platform, tenant: str, document_id: str) -> bool:
    _guard(tenant)
    r = platform.db.one(
        "SELECT authoritative FROM documents WHERE tenant=? AND id=?", (tenant, document_id)
    )
    return bool(r and r["authoritative"])


# --------------------------------------------------------------------------
# ranking boost
# --------------------------------------------------------------------------
def boost(platform, tenant: str, fused: list[tuple], passage_doc: dict) -> list[tuple]:
    """Re-weight a fused retrieval list by source rank and authoritative flag.

    ``fused``: [(passage_id, score)] · ``passage_doc``: {passage_id: (document_id, source)}.
    score' = score × weight_for(source) × (1.5 if document authoritative).
    Passages absent from ``passage_doc`` keep their score. Result is re-sorted
    by score descending; ties keep the incoming order (stable, deterministic).
    """
    _guard(tenant)
    w_cache: dict[str, float] = {}
    a_cache: dict[str, bool] = {}
    out = []
    for idx, (pid, score) in enumerate(fused):
        doc_id, source = passage_doc.get(pid, (None, None))
        mult = 1.0
        if source:
            if source not in w_cache:
                w_cache[source] = weight_for(platform, tenant, source)
            mult *= w_cache[source]
        if doc_id:
            if doc_id not in a_cache:
                a_cache[doc_id] = is_authoritative(platform, tenant, doc_id)
            if a_cache[doc_id]:
                mult *= AUTHORITATIVE_BOOST
        out.append((idx, pid, float(score) * mult))
    out.sort(key=lambda t: (-t[2], t[0]))
    return [(pid, score) for _, pid, score in out]


# --------------------------------------------------------------------------
# explaining citations
# --------------------------------------------------------------------------
def _cited_docs(platform, tenant: str, citations) -> list[dict]:
    """Unique cited documents (first-cited order) with source, rank, flag."""
    seen: list[dict] = []
    ids = set()
    for c in citations or []:
        doc_id = getattr(c, "document_id", None) or (
            c.get("document_id") if isinstance(c, dict) else None
        )
        if not doc_id or doc_id in ids:
            continue
        doc = platform.documents.get(tenant, doc_id)
        if not doc:
            continue  # not in this tenant (or deleted): never explained
        ids.add(doc_id)
        title = getattr(c, "document_title", None) or (
            c.get("document_title") if isinstance(c, dict) else None
        )
        seen.append(
            {
                "document_id": doc_id,
                "document_title": title or doc.get("title"),
                "source": doc.get("source") or "",
                "rank": rank_for(platform, tenant, doc.get("source") or ""),
                "authoritative": bool(doc.get("authoritative")),
                "order": len(seen),
            }
        )
    return seen


def _authority_key(d: dict) -> tuple:
    """Sort key: authoritative first, then best (lowest) rank, then first cited."""
    return (0 if d["authoritative"] else 1, d["rank"], d["order"])


def authoritative_source(platform, tenant: str, citations) -> dict | None:
    """The document a reader should trust most among the citations, with a reason.

    Returns {"document_id", "document_title", "source", "reason"} or None
    when nothing was cited. Authoritative-flagged documents win outright;
    otherwise the best-ranked source wins; ties go to the first cited.
    """
    _guard(tenant)
    docs = _cited_docs(platform, tenant, citations)
    if not docs:
        return None
    best = min(docs, key=_authority_key)
    n = len(docs)
    if best["authoritative"]:
        reason = (
            f"marked authoritative by a curator (source '{best['source']}', rank {best['rank']})"
            + (f"; preferred over {n - 1} other cited document(s)" if n > 1 else "")
        )
    elif n == 1:
        reason = f"only cited document; source '{best['source']}' has rank {best['rank']}"
    else:
        others = sorted({d["source"] for d in docs if d["document_id"] != best["document_id"]})
        same = [d for d in docs if d["rank"] == best["rank"]]
        if len(same) > 1:
            reason = (
                f"source '{best['source']}' shares the best rank {best['rank']} with "
                f"{len(same) - 1} other cited document(s); cited first"
            )
        else:
            reason = (
                f"source '{best['source']}' has the best rank ({best['rank']}) among cited "
                f"sources {', '.join(repr(s) for s in others)}"
            )
    return {
        "document_id": best["document_id"],
        "document_title": best["document_title"],
        "source": best["source"],
        "reason": reason,
    }


def conflicts(platform, tenant: str, citations) -> list[dict]:
    """Pairs of cited documents from different sources whose authority differs.

    [{"a": doc_id, "b": doc_id, "preferred": doc_id, "reason": str}] — ``a`` is
    always the earlier-cited document. A pair is reported when the sources
    differ and either the ranks differ or exactly one side is authoritative.
    """
    _guard(tenant)
    docs = _cited_docs(platform, tenant, citations)
    out = []
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            a, b = docs[i], docs[j]
            if a["source"] == b["source"]:
                continue
            if a["rank"] == b["rank"] and a["authoritative"] == b["authoritative"]:
                continue
            pref = min((a, b), key=_authority_key)
            other = b if pref is a else a
            if pref["authoritative"] and not other["authoritative"]:
                why = (
                    f"'{pref['document_title']}' is marked authoritative; "
                    f"'{other['document_title']}' ({other['source']}, rank {other['rank']}) is not"
                )
            else:
                why = (
                    f"source '{pref['source']}' (rank {pref['rank']}) outranks "
                    f"'{other['source']}' (rank {other['rank']})"
                )
            out.append(
                {
                    "a": a["document_id"],
                    "b": b["document_id"],
                    "preferred": pref["document_id"],
                    "reason": why,
                }
            )
    return out
