"""Knowledge-base evaluation (Stage-2 Section D).

Turns the raw stores into *curator advice*: for every active document a
transparent score, a suggestion (``keep`` / ``review`` / ``delete``) and the
plain-language reasons behind it, plus one ``data_quality`` dictionary the
Curator console renders as KPI tiles + risk register.

Signals per document
  citation_uses          how many answered traces used the document as evidence
                         (``spans.sources`` titles and ``attrs.trajectory.selected``
                         passage ids, both resolved back to document ids)
  age_days               days since ingestion
  duplicate_passages     live passages that duplicate a passage of *another*
                         document (embeddings table + cosine, cross-document only)
  contradiction_flags    conflict-flagged graph edges whose provenance is this document
  readability            0..1 heuristic (sentence length + long-word ratio)
  coverage_contribution  share of the tenant ontology's salient vocabulary present
  orphan_ratio           share of live passages no graph node was extracted from
  gap_hits               open gap questions that touch this document's subject

Suggestion rules (printed verbatim as reasons so a curator can audit them):
  delete  duplicate_passages >= 50% of passages AND not authoritative
  delete  age_days > 365 AND citation_uses == 0 AND not authoritative
  review  citation_uses == 0 OR readability < 0.35 OR contradiction_flags > 0
  keep    otherwise
Authoritative documents are never ``delete`` (at most ``review``).

Every store access is tenant-filtered (invariant I5); the module is
deterministic (stable ordering, no randomness, no model calls).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..adapters.embedder import cosine
from ..contracts.types import now_ms as _now_ms
from ..ontology.packs import get_pack
from ..stores.repositories import _guard
from . import metrics

# --------------------------------------------------------------------------
# tunables (kept as module constants so the rules are inspectable)
# --------------------------------------------------------------------------
DUPLICATE_THRESHOLD = 0.92        # cosine at/above which two passages are duplicates
DUPLICATE_DELETE_SHARE = 0.50     # share of a doc's passages that must be duplicates
STALE_DAYS = 365                  # uncited + older than this => delete candidate
READABILITY_REVIEW = 0.35         # below this a document is flagged for review
_MS_PER_DAY = 86_400_000

_SUGGESTION_RANK = {"delete": 0, "review": 1, "keep": 2}
_WORD = re.compile(r"[A-Za-z0-9_']+")
_TOKEN = re.compile(r"[a-z0-9]+")
_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)|\n+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
         "what", "which", "how", "who", "when", "where", "does", "do", "did", "was",
         "were", "be", "with", "that", "this", "it", "as", "by", "at", "from", "our",
         "we", "you", "i", "can", "will", "should", "must", "may", "according"}


# ==========================================================================
# citation usage
# ==========================================================================
def _passage_owner(platform, tenant: str) -> dict[str, str]:
    """passage_id -> document_id for every passage of the tenant (live or superseded,
    so old traces still resolve)."""
    _guard(tenant)
    rows = platform.db.query("SELECT id, document_id FROM passages WHERE tenant=?", (tenant,))
    return {r["id"]: r["document_id"] for r in rows}


def _docs_by_title(platform, tenant: str) -> dict[str, list[str]]:
    """title -> [document_id...] over the tenant's documents (any status), so a
    trace that cited a since-tombstoned document still resolves."""
    _guard(tenant)
    out: dict[str, list[str]] = {}
    for r in platform.db.query("SELECT id, title FROM documents WHERE tenant=? ORDER BY ingested_at, id",
                               (tenant,)):
        out.setdefault(r["title"] or "", []).append(r["id"])
    return out


def _loads(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if v is not None else default


def citation_usage(platform, tenant: str) -> dict[str, int]:
    """Count, per document, the answered traces that used it as evidence.

    A trace counts a document once when either
      * one of the trace's ``sources`` (document titles) resolves to it, or
      * one of ``attrs.trajectory.selected`` (passage ids) belongs to it.
    Only ``answer`` spans that produced at least one citation are considered;
    a title shared by several documents is disambiguated with the trajectory's
    passages when possible and otherwise credited to all of them.
    """
    _guard(tenant)
    owner = _passage_owner(platform, tenant)
    by_title = _docs_by_title(platform, tenant)
    usage: dict[str, int] = {}
    rows = platform.db.query(
        "SELECT sources, attrs, citations_count FROM spans WHERE tenant=? AND name='answer' ORDER BY id",
        (tenant,))
    for r in rows:
        if int(r["citations_count"] or 0) <= 0:
            continue
        attrs = _loads(r["attrs"], {})
        selected = (attrs.get("trajectory") or {}).get("selected") or []
        from_passages = {owner[pid] for pid in selected if pid in owner}
        used: set[str] = set(from_passages)
        for title in _loads(r["sources"], []) or []:
            candidates = by_title.get(str(title), [])
            if len(candidates) > 1:
                narrowed = [d for d in candidates if d in from_passages]
                candidates = narrowed or candidates
            used.update(candidates)
        for doc_id in used:
            usage[doc_id] = usage.get(doc_id, 0) + 1
    return dict(sorted(usage.items()))


# ==========================================================================
# duplicates
# ==========================================================================
def _live_vectors(platform, tenant: str) -> list[tuple[str, str, list[float]]]:
    """[(passage_id, document_id, vec)] for live passages of the current embedder."""
    _guard(tenant)
    model_id = platform.embedder.model_id()
    rows = platform.db.query(
        """SELECT e.passage_id pid, p.document_id did, e.vec vec
           FROM embeddings e JOIN passages p ON p.id=e.passage_id AND p.tenant=e.tenant
           WHERE e.tenant=? AND e.model_id=? AND p.superseded_by IS NULL
           ORDER BY e.passage_id""",
        (tenant, model_id))
    out = []
    for r in rows:
        vec = _loads(r["vec"], None)
        if isinstance(vec, list) and vec:
            out.append((r["pid"], r["did"], vec))
    return out


def _doc_meta(platform, tenant: str) -> dict[str, dict]:
    _guard(tenant)
    return {r["id"]: dict(r) for r in platform.db.query(
        "SELECT id, ingested_at, authoritative, content_hash, title, source, uri, status "
        "FROM documents WHERE tenant=?", (tenant,))}


def _keeps_original(a: dict, b: dict) -> bool:
    """True when document ``a`` is the original of a duplicate pair.

    The earlier-ingested document is the original and the later one the copy;
    ties break on id so the result is stable across runs. The authoritative flag
    deliberately does NOT re-orient the pair: it is applied by the suggestion
    rules instead (an authoritative copy is ``review``, never ``delete``), so a
    curator sees the duplication on the document they actually uploaded.
    """
    ia, ib = int(a.get("ingested_at") or 0), int(b.get("ingested_at") or 0)
    if ia != ib:
        return ia < ib
    return a["id"] < b["id"]


def duplicates(platform, tenant: str, threshold: float = DUPLICATE_THRESHOLD) -> list[dict]:
    """Cross-document near-duplicate passages.

    Compares every live passage embedding with every live passage of a *different*
    document (same-document repetition is not a duplicate). Each unordered pair at
    or above ``threshold`` is reported once with ``passage_id`` on the newer copy
    and ``dup_of`` on the older original (ties on id — see ``_keeps_original``). O(n^2) in live passages; fine for the
    tens of thousands a tenant holds locally, and the cloud adapter can replace it
    with a pgvector self-join behind the same signature.
    """
    _guard(tenant)
    vecs = _live_vectors(platform, tenant)
    meta = _doc_meta(platform, tenant)
    out: list[dict] = []
    for i in range(len(vecs)):
        pid_a, doc_a, vec_a = vecs[i]
        for j in range(i + 1, len(vecs)):
            pid_b, doc_b, vec_b = vecs[j]
            if doc_a == doc_b:
                continue
            sim = cosine(vec_a, vec_b)
            if sim < threshold:
                continue
            ma, mb = meta.get(doc_a, {"id": doc_a}), meta.get(doc_b, {"id": doc_b})
            if _keeps_original(ma, mb):
                copy, orig = (pid_b, doc_b), (pid_a, doc_a)
            else:
                copy, orig = (pid_a, doc_a), (pid_b, doc_b)
            out.append({"passage_id": copy[0], "dup_of": orig[0], "document_id": copy[1],
                        "dup_document_id": orig[1], "cosine": round(min(1.0, sim), 6)})
    out.sort(key=lambda d: (-d["cosine"], d["passage_id"], d["dup_of"]))
    return out


# ==========================================================================
# readability
# ==========================================================================
def readability(text: str) -> float:
    """Heuristic readability in 0..1 (1 = easiest).

    Two penalties, each clamped to 0..1: mean sentence length beyond 15 words
    (reaches full penalty at 45 words) and the share of long words (>= 8
    letters, full penalty at 60%). ``score = 1 - 0.6*length_pen - 0.4*long_pen``.
    Empty / word-less text scores 0.0. Deterministic and language-agnostic.
    """
    if not text or not text.strip():
        return 0.0
    words = _WORD.findall(text)
    if not words:
        return 0.0
    sentences = [s for s in _SENTENCE_END.split(text) if s and _WORD.search(s)]
    n_sent = max(1, len(sentences))
    avg_len = len(words) / n_sent
    length_pen = min(1.0, max(0.0, (avg_len - 15.0) / 30.0))
    long_ratio = sum(1 for w in words if len(w) >= 8) / len(words)
    long_pen = min(1.0, long_ratio / 0.6)
    score = 1.0 - 0.6 * length_pen - 0.4 * long_pen
    return round(min(1.0, max(0.0, score)), 4)


# ==========================================================================
# per-document signals
# ==========================================================================
def _pack_for(platform, tenant: str):
    """Ontology pack of the tenant: demo tenant config when known, else the default pack."""
    try:
        from ..tenants.demo import DEMO_TENANTS
        for cfg in DEMO_TENANTS:
            if cfg.tenant == tenant:
                return get_pack(cfg.ontology)
    except Exception:  # pragma: no cover - demo config is optional
        pass
    return get_pack("quality-assurance")


def _live_passages_by_doc(platform, tenant: str) -> dict[str, list[dict]]:
    _guard(tenant)
    out: dict[str, list[dict]] = {}
    for r in platform.db.query(
            "SELECT id, document_id, text, coord_locator FROM passages "
            "WHERE tenant=? AND superseded_by IS NULL ORDER BY id", (tenant,)):
        out.setdefault(r["document_id"], []).append(dict(r))
    return out


def _contradictions_by_hash(platform, tenant: str) -> dict[str, int]:
    """content_hash -> number of conflict-flagged edges whose provenance carries it."""
    _guard(tenant)
    out: dict[str, int] = {}
    for r in platform.db.query(
            "SELECT provenance FROM graph_edges WHERE tenant=? AND conflict_flag=1", (tenant,)):
        for prov in _loads(r["provenance"], []) or []:
            h = (prov or {}).get("content_hash")
            if h:
                out[h] = out.get(h, 0) + 1
    return out


def _node_provenance(platform, tenant: str) -> tuple[set[str], set[tuple[str, str]]]:
    """(content hashes referenced by any graph node,
        (content_hash, locator-repr) pairs referenced by passage-level provenance)."""
    _guard(tenant)
    hashes: set[str] = set()
    located: set[tuple[str, str]] = set()
    for r in platform.db.query("SELECT provenance FROM graph_nodes WHERE tenant=?", (tenant,)):
        for prov in _loads(r["provenance"], []) or []:
            h = (prov or {}).get("content_hash")
            if not h:
                continue
            hashes.add(h)
            coord = str(prov.get("coordinate") or "")
            m = re.search(r"locator=(\{.*\})\)?$", coord)
            if m:
                located.add((h, m.group(1)))
    return hashes, located


def _orphan_ratio(doc: dict, passages: list[dict], hashes: set[str],
                  located: set[tuple[str, str]]) -> float:
    """Share of live passages that no graph node was extracted from."""
    if not passages:
        return 1.0
    h = doc.get("content_hash") or ""
    if h not in hashes:
        return 1.0
    connected = 0
    for p in passages:
        locator = _loads(p.get("coord_locator"), {})
        if (h, repr(locator)) in located:
            connected += 1
    if connected == 0:
        # nodes exist for the document but carry no passage coordinate (concept
        # nodes): treat the document as weakly connected rather than orphaned
        return 0.5
    return round(1.0 - connected / len(passages), 4)


def _salient_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 2}


def _gap_hits(gaps: list[str], title: str, doc_tokens: set[str]) -> int:
    """Open gap questions that touch this document (title overlap, or >= 3 shared
    salient tokens with the body)."""
    title_toks = _salient_tokens(title)
    hits = 0
    for q in gaps:
        qt = _salient_tokens(q)
        if not qt:
            continue
        if qt & title_toks or len(qt & doc_tokens) >= 3:
            hits += 1
    return hits


def _score(signals: dict, authoritative: bool, n_pass: int) -> float:
    """Weighted 0..1 quality score (weights sum to 1.0; authoritative adds 0.1).

    citation 0.25 · freshness 0.15 · uniqueness 0.20 · consistency 0.10 ·
    readability 0.10 · coverage 0.10 · connectedness 0.10
    """
    cit = min(1.0, signals["citation_uses"] / 3.0)
    fresh = 1.0 - min(1.0, signals["age_days"] / STALE_DAYS)
    uniq = 1.0 - (signals["duplicate_passages"] / n_pass if n_pass else 1.0)
    consistent = 1.0 if signals["contradiction_flags"] == 0 else 0.0
    score = (0.25 * cit + 0.15 * fresh + 0.20 * uniq + 0.10 * consistent
             + 0.10 * signals["readability"] + 0.10 * signals["coverage_contribution"]
             + 0.10 * (1.0 - signals["orphan_ratio"]))
    if authoritative:
        score += 0.10
    return round(min(1.0, max(0.0, score)), 4)


def _suggest(signals: dict, authoritative: bool, n_pass: int,
             duplicated_by: int = 0) -> tuple[str, list[str]]:
    """Apply the suggestion rules; every fired rule becomes a reason string.

    ``duplicated_by`` is informational: how many of this document's passages a
    *newer* document copies. It never changes the suggestion (the copy is the one
    flagged) but tells the curator which side of the pair this is.
    """
    reasons: list[str] = []
    dup_share = (signals["duplicate_passages"] / n_pass) if n_pass else 0.0
    wants_delete = False

    if n_pass and dup_share >= DUPLICATE_DELETE_SHARE:
        reasons.append(f"duplicate: {signals['duplicate_passages']}/{n_pass} passages "
                       f"({dup_share:.0%}) duplicate another document's content")
        wants_delete = True
    if signals["age_days"] > STALE_DAYS and signals["citation_uses"] == 0:
        reasons.append(f"stale and unused: ingested {signals['age_days']:.0f} days ago "
                       f"(> {STALE_DAYS}) and never cited")
        wants_delete = True

    if signals["citation_uses"] == 0:
        reasons.append("never cited by any answered question")
    if signals["readability"] < READABILITY_REVIEW:
        reasons.append(f"low readability {signals['readability']:.2f} (< {READABILITY_REVIEW})")
    if signals["contradiction_flags"] > 0:
        reasons.append(f"{signals['contradiction_flags']} contradiction flag(s) in the knowledge graph")
    if 0 < signals["duplicate_passages"] and not wants_delete:
        reasons.append(f"{signals['duplicate_passages']}/{n_pass} passages duplicate another document")
    if duplicated_by:
        reasons.append(f"{duplicated_by}/{n_pass} passages are duplicated by a newer document; "
                       f"this one is the original{' (authoritative)' if authoritative else ''}")
    if signals["gap_hits"]:
        reasons.append(f"near {signals['gap_hits']} unanswered question(s) — expand rather than remove")

    if wants_delete and authoritative:
        reasons.append("authoritative document: never suggested for deletion, review instead")
        return "review", reasons
    if wants_delete:
        return "delete", reasons
    review_triggers = (signals["citation_uses"] == 0 or signals["readability"] < READABILITY_REVIEW
                       or signals["contradiction_flags"] > 0)
    if review_triggers:
        return "review", reasons
    if not reasons:
        reasons.append("cited, readable, unique and consistent")
    return "keep", reasons


def document_quality(platform, tenant: str, now_ms: Optional[int] = None) -> list[dict]:
    """Per-document signals, score, suggestion and reasons for every active document.

    Sorted worst-first: ``delete`` before ``review`` before ``keep``, then ascending
    score, then document id — so the curator sees what needs attention at the top.
    ``now_ms`` fixes the clock for reproducible ages (tests, replay).
    """
    _guard(tenant)
    now = int(now_ms if now_ms is not None else _now_ms())
    docs = platform.documents.list(tenant)
    usage = citation_usage(platform, tenant)
    dups = duplicates(platform, tenant)
    dup_passages: dict[str, set[str]] = {}      # copy side: passages that duplicate another doc
    dup_targets: dict[str, set[str]] = {}       # original side: passages copied by a newer doc
    for d in dups:
        dup_passages.setdefault(d["document_id"], set()).add(d["passage_id"])
        dup_targets.setdefault(d["dup_document_id"], set()).add(d["dup_of"])
    passages_by_doc = _live_passages_by_doc(platform, tenant)
    contradictions = _contradictions_by_hash(platform, tenant)
    hashes, located = _node_provenance(platform, tenant)
    gaps = [g["item"] for g in platform.curation.list(tenant, "gap")]
    vocab = list(_pack_for(platform, tenant).salient_vocab)

    out: list[dict] = []
    for doc in docs:
        passages = passages_by_doc.get(doc["id"], [])
        n_pass = len(passages)
        text = "\n".join(p["text"] or "" for p in passages)
        tokens = _salient_tokens(text)
        authoritative = bool(int(doc.get("authoritative") or 0))
        read = (round(sum(readability(p["text"] or "") for p in passages) / n_pass, 4)
                if n_pass else 0.0)
        signals = {
            "citation_uses": int(usage.get(doc["id"], 0)),
            "age_days": round(max(0, now - int(doc.get("ingested_at") or now)) / _MS_PER_DAY, 2),
            "duplicate_passages": len(dup_passages.get(doc["id"], ())),
            "contradiction_flags": int(contradictions.get(doc.get("content_hash") or "", 0)),
            "readability": read,
            "coverage_contribution": round(sum(1 for t in vocab if t in tokens) / max(1, len(vocab)), 4),
            "orphan_ratio": _orphan_ratio(doc, passages, hashes, located),
            "gap_hits": _gap_hits(gaps, doc.get("title") or "", tokens),
        }
        suggestion, reasons = _suggest(signals, authoritative, n_pass,
                                       duplicated_by=len(dup_targets.get(doc['id'], ())))
        out.append({
            "document_id": doc["id"], "title": doc.get("title"), "source": doc.get("source"),
            "uri": doc.get("uri"), "ingested_at": doc.get("ingested_at"), "passages": n_pass,
            "authoritative": authoritative, "signals": signals,
            "score": _score(signals, authoritative, n_pass),
            "suggestion": suggestion, "reasons": reasons,
        })
    out.sort(key=lambda d: (_SUGGESTION_RANK[d["suggestion"]], d["score"], d["document_id"]))
    return out


# ==========================================================================
# data quality roll-up
# ==========================================================================
def data_quality(platform, tenant: str) -> dict:
    """One dictionary for the Curator KPI tiles + risk register.

    Health-snapshot metrics come from ``health.metrics.latest`` (coverage,
    freshness, contradictions, gaps, connectedness, traceability); the
    knowledge-base evaluation adds readability, duplicate rate, citation
    coverage and the suggestion counts, and appends its own entries to
    ``metrics.risk_register`` in the same ``{risk, value, severity}`` shape.
    """
    _guard(tenant)
    snap = metrics.latest(platform, tenant)
    risks = list(metrics.risk_register(platform, tenant))
    quality = document_quality(platform, tenant)
    n_docs = len(quality)
    n_pass = sum(d["passages"] for d in quality)
    dup_pass = sum(d["signals"]["duplicate_passages"] for d in quality)
    cited = sum(1 for d in quality if d["signals"]["citation_uses"] > 0)
    counts = {"keep": 0, "review": 0, "delete": 0}
    for d in quality:
        counts[d["suggestion"]] += 1
    readability_avg = (round(sum(d["signals"]["readability"] for d in quality) / n_docs, 4)
                       if n_docs else 0.0)
    duplicate_rate = round(dup_pass / n_pass, 4) if n_pass else 0.0
    citation_coverage = round(cited / n_docs, 4) if n_docs else 0.0

    if duplicate_rate > 0:
        risks.append({"risk": "duplicate content", "value": duplicate_rate,
                      "severity": "high" if duplicate_rate >= 0.25 else "medium"})
    if counts["delete"]:
        risks.append({"risk": "documents suggested for deletion", "value": counts["delete"],
                      "severity": "medium"})
    if n_docs and citation_coverage < 0.5:
        risks.append({"risk": "most documents never cited", "value": citation_coverage,
                      "severity": "medium"})
    if n_docs and readability_avg < READABILITY_REVIEW:
        risks.append({"risk": "low readability", "value": readability_avg, "severity": "low"})

    return {
        "tenant": tenant,
        "coverage": round(float(snap.get("coverage") or 0.0), 4),
        "freshness": round(float(snap.get("freshness") or 0.0), 4),
        "contradictions": int(snap.get("contradictions") or 0),
        "gaps": int(snap.get("gaps") or 0),
        "connectedness": round(float(snap.get("connectedness") or 0.0), 4),
        "traceability": round(float(snap.get("traceability") or 0.0), 4),
        "readability_avg": readability_avg,
        "duplicate_rate": duplicate_rate,
        "citation_coverage": citation_coverage,
        "documents": n_docs,
        "passages": n_pass,
        "suggestions": counts,
        "risk_register": risks,
        "snapshot_at": snap.get("taken_at"),
    }
