"""Two curation modes (T53) and the ingestion timeline (T54).

A tenant runs each source in one of two curation modes:

* **automated** — an ingested document goes live at once; the pipeline's own
  quality signals stand in for a human. The event is logged as ``auto_kept``.
* **manual** — an ingested document waits in a review queue. It is *not*
  answerable (``is_live`` is ``False``) until a curator ``accept``\\ s it. The
  event is logged as ``ingested``.

Mode is a per-source setting with a tenant-wide default (``curation_settings``);
``get_mode`` resolves *source override → global default → "automated"*.

Every state change appends to ``curation_log`` (one row per action), which the
ingestion **timeline** aggregates by month so the console can show a stacked bar
of what was ingested/accepted/rejected/deleted per month, with a drill-down.

Document state (``live`` / ``review`` / ``rejected`` / ``deleted``) is kept as a
``curation_state`` key inside the document's ``meta`` — no schema change to the
``documents`` table. A document with no key is ``live`` (so the seeded corpus is
answerable by default); only manual-mode items that have not been accepted sit
in ``review`` and are held back by ``live_filter``.

Quality scoring reuses ``health.kb_eval`` / ``health.metrics``; every function is
tenant-filtered (invariant I5) and deterministic (no clock, no randomness beyond
an injectable ``now_ms``).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re

from .contracts.types import new_id
from .contracts.types import now_ms as _now_ms
from .health import kb_eval
from .stores import versioning
from .stores.repositories import _guard

__all__ = [
    "MODES",
    "ACTIONS",
    "GLOBAL_SOURCE",
    "set_mode",
    "set_default_mode",
    "get_mode",
    "score",
    "recommendation",
    "review_id",
    "on_ingest",
    "accept",
    "reject",
    "delete",
    "restore",
    "is_live",
    "live_filter",
    "get_state",
    "review_queue",
    "timeline",
    "log_event",
]

#: the two curation modes a source can run in
MODES = ("manual", "automated")
#: actions recorded in ``curation_log``
ACTIONS = ("ingested", "accepted", "rejected", "deleted", "restored", "auto_kept")
#: an action that closes a review item — a resolved review id is never re-logged
RESOLVED_ACTIONS = frozenset({"accepted", "rejected", "deleted"})
#: pseudo-source under which the tenant-wide default mode is stored
GLOBAL_SOURCE = "*"
#: state of a document with no explicit ``curation_state`` in its meta
DEFAULT_STATE = "live"
_DEFAULT_MODE = "automated"
_MS_PER_DAY = 86_400_000
_WS = re.compile(r"\s+")

# how a resolved review id maps back onto a document that re-arrives
_RESOLUTION_STATE = {"accepted": "live", "rejected": "rejected", "deleted": "deleted"}


# ==========================================================================
# modes (per-source setting + tenant default)
# ==========================================================================
def set_mode(platform, tenant: str, source: str, mode: str) -> str:
    """Set the curation mode for one ``source`` (``GLOBAL_SOURCE`` = the default).

    ``mode`` must be one of :data:`MODES`. Returns the stored mode.
    """
    _guard(tenant)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    src = source or GLOBAL_SOURCE
    platform.db.execute(
        """INSERT INTO curation_settings(tenant,source,mode) VALUES(?,?,?)
           ON CONFLICT(tenant,source) DO UPDATE SET mode=excluded.mode""",
        (tenant, src, mode),
    )
    return mode


def set_default_mode(platform, tenant: str, mode: str) -> str:
    """Set the tenant-wide default mode (used by any source without an override)."""
    return set_mode(platform, tenant, GLOBAL_SOURCE, mode)


def get_mode(platform, tenant: str, source: str) -> str:
    """Resolve the mode for ``source``: source override, else the tenant default,
    else ``"automated"``."""
    _guard(tenant)
    for key in (source, GLOBAL_SOURCE):
        if not key:
            continue
        r = platform.db.one(
            "SELECT mode FROM curation_settings WHERE tenant=? AND source=?", (tenant, key)
        )
        if r and r["mode"] in MODES:
            return r["mode"]
    return _DEFAULT_MODE


# ==========================================================================
# quality score + recommendation
# ==========================================================================
def _empty_score() -> dict:
    return {
        "depth": 0.0,
        "connectedness": 0.0,
        "traceability": 0.0,
        "readability": 0.0,
        "currency": 0.0,
        "duplicate_pct": 0.0,
        "contradiction": 0,
        "overall": 0.0,
    }


def score(platform, tenant: str, doc, now_ms: int | None = None) -> dict:
    """Deterministic quality score for one document.

    Reuses ``health.kb_eval.document_quality`` (citation, duplicate, contradiction,
    readability, coverage and orphan signals) and re-expresses them as the eight
    curation dimensions: ``depth``, ``connectedness``, ``traceability``,
    ``readability``, ``currency``, ``duplicate_pct``, ``contradiction`` and a
    weighted ``overall`` (0..1). ``doc`` may be a document row/dict or its id.
    """
    _guard(tenant)
    doc_id = doc["id"] if isinstance(doc, dict) else doc
    row = next(
        (
            r
            for r in kb_eval.document_quality(platform, tenant, now_ms=now_ms)
            if r["document_id"] == doc_id
        ),
        None,
    )
    if row is None:
        return _empty_score()
    s = row["signals"]
    n_pass = int(row["passages"] or 0)
    readability = float(s["readability"])
    currency = round(1.0 - min(1.0, s["age_days"] / kb_eval.STALE_DAYS), 4)
    duplicate_pct = round(s["duplicate_passages"] / n_pass, 4) if n_pass else 0.0
    contradiction = int(s["contradiction_flags"])
    connectedness = round(1.0 - float(s["orphan_ratio"]), 4)
    # every stored passage carries provenance by construction (invariant I2/I8)
    traceability = 1.0 if n_pass else 0.0
    cov = float(s["coverage_contribution"])
    depth = round(min(1.0, 0.6 * min(1.0, n_pass / 8.0) + 0.4 * cov), 4)
    consistent = 0.0 if contradiction else 1.0
    overall = round(
        min(
            1.0,
            max(
                0.0,
                0.18 * depth
                + 0.15 * connectedness
                + 0.12 * traceability
                + 0.15 * readability
                + 0.15 * currency
                + 0.15 * (1.0 - duplicate_pct)
                + 0.10 * consistent,
            ),
        ),
        4,
    )
    return {
        "depth": depth,
        "connectedness": connectedness,
        "traceability": traceability,
        "readability": readability,
        "currency": currency,
        "duplicate_pct": duplicate_pct,
        "contradiction": contradiction,
        "overall": overall,
    }


def recommendation(sc: dict) -> tuple[str, list[str]]:
    """Turn a :func:`score` dict into ``("Keep"|"Delete", [reasons])``.

    Every fired rule contributes a plain-language reason so a curator can audit
    the call. A document is a delete candidate when it is mostly duplicate
    content or its overall score is very low; otherwise it is kept.
    """
    reasons: list[str] = []
    wants_delete = False
    if sc["duplicate_pct"] >= kb_eval.DUPLICATE_DELETE_SHARE:
        reasons.append(
            f"{sc['duplicate_pct']:.0%} of passages duplicate another document's content"
        )
        wants_delete = True
    if sc["contradiction"]:
        reasons.append(f"{sc['contradiction']} contradiction flag(s) in the knowledge graph")
    if sc["readability"] < kb_eval.READABILITY_REVIEW:
        reasons.append(f"low readability {sc['readability']:.2f}")
    if sc["currency"] < 0.25:
        reasons.append(f"low currency {sc['currency']:.2f} (stale)")
    if not wants_delete and sc["overall"] < 0.35:
        reasons.append(f"low overall quality {sc['overall']:.2f}")
        wants_delete = True
    if wants_delete:
        return "Delete", reasons
    if not reasons:
        reasons.append("readable, unique, current and consistent")
    return "Keep", reasons


# ==========================================================================
# stable review id (T58)
# ==========================================================================
def _normalise_title(title: str) -> str:
    return _WS.sub(" ", (title or "").strip().lower())


def review_id(doc) -> str:
    """Stable 16-hex review id for a document, so rebuilding the queue does not
    re-number a resolved item. Derived from ``type`` + normalised ``title`` only,
    both of which survive a re-ingest of the same logical document."""
    dtype = str((doc.get("type") if isinstance(doc, dict) else "") or "")
    title = _normalise_title(doc.get("title") if isinstance(doc, dict) else "")
    digest = hashlib.sha256(f"{dtype}\x00{title}".encode()).hexdigest()
    return digest[:16]


# ==========================================================================
# document state (meta key, no schema change)
# ==========================================================================
def _parse_meta(doc) -> dict:
    if not isinstance(doc, dict):
        return {}
    meta = doc.get("meta")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            return {}
    return meta if isinstance(meta, dict) else {}


def get_state(doc) -> str:
    """The curation state of a document row/dict (default ``"live"``)."""
    return _parse_meta(doc).get("curation_state") or DEFAULT_STATE


def is_live(doc) -> bool:
    """``True`` when a document is answerable: active and not held in ``review``
    (nor rejected/deleted). The retrieval layer calls this so a review item never
    reaches an asker."""
    status = doc.get("status") if isinstance(doc, dict) else None
    if status and status != "active":
        return False
    return get_state(doc) == "live"


def live_filter(docs: list) -> list:
    """Keep only the answerable documents of an iterable (see :func:`is_live`)."""
    return [d for d in docs if is_live(d)]


def _set_state(platform, tenant: str, doc_id: str, state: str) -> None:
    meta = platform.documents.meta_of(tenant, doc_id)
    meta["curation_state"] = state
    platform.db.execute(
        "UPDATE documents SET meta=? WHERE tenant=? AND id=?",
        (json.dumps(meta, sort_keys=True, default=str), tenant, doc_id),
    )


# ==========================================================================
# curation log
# ==========================================================================
def log_event(
    platform,
    tenant: str,
    action: str,
    *,
    mode: str | None = None,
    source: str | None = None,
    document_id: str | None = None,
    title: str | None = None,
    score_value: dict | None = None,
    recommendation_value: str | None = None,
    reason: str | None = None,
    actor: str = "system",
    review: str | None = None,
    ts: int | None = None,
) -> str:
    """Append one row to ``curation_log`` and return its id.

    ``ts`` (epoch milliseconds) is injectable so callers and tests can place an
    event in a specific month; it defaults to now.
    """
    _guard(tenant)
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, got {action!r}")
    log_id = new_id("clog_")
    platform.db.execute(
        """INSERT INTO curation_log(id,tenant,ts,action,mode,source,document_id,title,score,
           recommendation,reason,actor,review_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            log_id,
            tenant,
            int(ts if ts is not None else _now_ms()),
            action,
            mode,
            source,
            document_id,
            title,
            json.dumps(score_value) if score_value is not None else None,
            recommendation_value,
            reason,
            actor,
            review,
        ),
    )
    return log_id


def _latest_row(platform, tenant: str, review: str) -> dict | None:
    r = platform.db.one(
        "SELECT * FROM curation_log WHERE tenant=? AND review_id=? ORDER BY id DESC LIMIT 1",
        (tenant, review),
    )
    return dict(r) if r else None


def _is_resolved(platform, tenant: str, review: str) -> bool:
    row = _latest_row(platform, tenant, review)
    return bool(row and row["action"] in RESOLVED_ACTIONS)


# ==========================================================================
# ingest hook + curator actions
# ==========================================================================
def on_ingest(platform, tenant: str, doc, source: str) -> dict:
    """Decide what happens to a freshly-ingested document under the source's mode.

    * **automated** — mark the document ``live`` and log ``auto_kept``.
    * **manual** — mark the document ``review`` (held back from answers) and log
      ``ingested``; it waits for a curator.

    A review id that is already resolved (accepted/rejected/deleted) is kept
    resolved: the document is re-settled to that outcome and nothing is re-logged.
    Returns ``{state, score, recommendation, review_id, log_id}``.
    """
    _guard(tenant)
    doc_id = doc["id"] if isinstance(doc, dict) else doc
    doc_row = doc if isinstance(doc, dict) else (platform.documents.get(tenant, doc_id) or {})
    rid = review_id(doc_row)
    mode = get_mode(platform, tenant, source)
    sc = score(platform, tenant, doc_row)
    rec, reasons = recommendation(sc)

    if _is_resolved(platform, tenant, rid):
        outcome = _RESOLUTION_STATE[_latest_row(platform, tenant, rid)["action"]]
        if outcome == "live":
            platform.db.execute(
                "UPDATE documents SET status='active' WHERE tenant=? AND id=?", (tenant, doc_id)
            )
            _set_state(platform, tenant, doc_id, "live")
        else:
            platform.documents.tombstone(tenant, doc_id)
            _set_state(platform, tenant, doc_id, outcome)
        return {
            "state": outcome,
            "score": sc,
            "recommendation": (rec, reasons),
            "review_id": rid,
            "log_id": None,
        }

    if mode == "manual":
        state, action = "review", "ingested"
    else:
        state, action = "live", "auto_kept"
    _set_state(platform, tenant, doc_id, state)
    log_id = log_event(
        platform,
        tenant,
        action,
        mode=mode,
        source=source,
        document_id=doc_id,
        title=doc_row.get("title") if isinstance(doc_row, dict) else None,
        score_value=sc,
        recommendation_value=rec,
        reason="; ".join(reasons),
        actor="system",
        review=rid,
    )
    return {
        "state": state,
        "score": sc,
        "recommendation": (rec, reasons),
        "review_id": rid,
        "log_id": log_id,
    }


def _resolve_target(platform, tenant: str, review: str) -> dict:
    row = _latest_row(platform, tenant, review)
    if row is None or not row["document_id"]:
        raise KeyError(f"no curation log entry for review id {review!r}")
    return row


def accept(platform, tenant: str, review: str, actor: str) -> dict:
    """Accept a review item: make its document ``live`` and answerable, log
    ``accepted`` and bump the dataset version (best effort)."""
    _guard(tenant)
    row = _resolve_target(platform, tenant, review)
    doc_id = row["document_id"]
    platform.db.execute(
        "UPDATE documents SET status='active' WHERE tenant=? AND id=?", (tenant, doc_id)
    )
    _set_state(platform, tenant, doc_id, "live")
    doc = platform.documents.get(tenant, doc_id) or {}
    sc = score(platform, tenant, doc)
    rec, reasons = recommendation(sc)
    log_id = log_event(
        platform,
        tenant,
        "accepted",
        mode=row["mode"],
        source=row["source"],
        document_id=doc_id,
        title=doc.get("title"),
        score_value=sc,
        recommendation_value=rec,
        reason="; ".join(reasons),
        actor=actor,
        review=review,
    )
    try:  # dataset versioning is best-effort — never fail an accept over it
        versioning.bump_dataset(platform, tenant, f"curation accept {review}")
    except Exception:  # pragma: no cover - defensive
        pass
    return {"state": "live", "document_id": doc_id, "review_id": review, "log_id": log_id}


def _close(platform, tenant: str, review: str, actor: str, action: str, reason: str) -> dict:
    row = _resolve_target(platform, tenant, review)
    doc_id = row["document_id"]
    platform.documents.tombstone(tenant, doc_id)
    state = _RESOLUTION_STATE[action]
    _set_state(platform, tenant, doc_id, state)
    doc = platform.documents.get(tenant, doc_id) or {}
    log_id = log_event(
        platform,
        tenant,
        action,
        mode=row["mode"],
        source=row["source"],
        document_id=doc_id,
        title=doc.get("title"),
        reason=reason,
        actor=actor,
        review=review,
    )
    return {"state": state, "document_id": doc_id, "review_id": review, "log_id": log_id}


def reject(platform, tenant: str, review: str, actor: str, reason: str = "") -> dict:
    """Reject a review item: tombstone its document and log ``rejected``."""
    _guard(tenant)
    return _close(platform, tenant, review, actor, "rejected", reason)


def delete(platform, tenant: str, review: str, actor: str, reason: str = "") -> dict:
    """Delete a document from the live set (tombstone) and log ``deleted``."""
    _guard(tenant)
    return _close(platform, tenant, review, actor, "deleted", reason)


def restore(platform, tenant: str, review: str, actor: str, reason: str = "") -> dict:
    """Restore a rejected/deleted document: make it active and ``live`` again and
    log ``restored``."""
    _guard(tenant)
    row = _resolve_target(platform, tenant, review)
    doc_id = row["document_id"]
    platform.db.execute(
        "UPDATE documents SET status='active' WHERE tenant=? AND id=?", (tenant, doc_id)
    )
    _set_state(platform, tenant, doc_id, "live")
    doc = platform.documents.get(tenant, doc_id) or {}
    log_id = log_event(
        platform,
        tenant,
        "restored",
        mode=row["mode"],
        source=row["source"],
        document_id=doc_id,
        title=doc.get("title"),
        reason=reason,
        actor=actor,
        review=review,
    )
    return {"state": "live", "document_id": doc_id, "review_id": review, "log_id": log_id}


# ==========================================================================
# review queue
# ==========================================================================
def review_queue(platform, tenant: str) -> list[dict]:
    """The items waiting in ``review`` (manual mode, not yet resolved).

    One entry per review id whose latest action is ``ingested``. Score and
    recommendation are recomputed from the current document so the queue reflects
    the fabric as it stands now.
    """
    _guard(tenant)
    latest: dict[str, dict] = {}
    for r in platform.db.query("SELECT * FROM curation_log WHERE tenant=? ORDER BY id", (tenant,)):
        latest[r["review_id"]] = dict(r)
    out: list[dict] = []
    for rid, row in latest.items():
        if row["action"] != "ingested":
            continue
        doc = platform.documents.get(tenant, row["document_id"])
        if not doc:
            continue
        sc = score(platform, tenant, doc)
        rec, reasons = recommendation(sc)
        out.append(
            {
                "review_id": rid,
                "title": doc.get("title"),
                "source": row["source"],
                "score": sc,
                "recommendation": rec,
                "reasons": reasons,
                "ts": row["ts"],
            }
        )
    out.sort(key=lambda d: (d["score"]["overall"], d["review_id"]))
    return out


# ==========================================================================
# ingestion timeline
# ==========================================================================
def _ym(ts_ms) -> tuple[int, int]:
    d = _dt.datetime.fromtimestamp(int(ts_ms or 0) / 1000, tz=_dt.UTC)
    return d.year, d.month


def timeline(
    platform,
    tenant: str,
    year: int | None = None,
    source: str | None = None,
    mode: str | None = None,
) -> dict:
    """Aggregate ``curation_log`` into a per-month view for one year.

    Returns ``{"year", "months", "rows", "years"}`` where ``months`` is always
    twelve buckets (1..12) each stacking the five per-action counts, ``rows`` is
    the matching drill-down of individual events (oldest first) and ``years``
    lists every year present in the log. Optional ``source`` / ``mode`` filters
    narrow both the stacks and the rows. Defaults to the latest year present.
    """
    _guard(tenant)
    all_rows = [
        dict(r)
        for r in platform.db.query(
            "SELECT * FROM curation_log WHERE tenant=? ORDER BY ts, id", (tenant,)
        )
    ]
    years = sorted({_ym(r["ts"])[0] for r in all_rows})
    if year is None:
        year = years[-1] if years else _ym(_now_ms())[0]
    else:
        year = int(year)

    months = [
        {
            "month": m,
            "ingested": 0,
            "accepted": 0,
            "rejected": 0,
            "deleted": 0,
            "auto_kept": 0,
        }
        for m in range(1, 13)
    ]
    rows: list[dict] = []
    for r in all_rows:
        y, m = _ym(r["ts"])
        if y != year:
            continue
        if source is not None and r["source"] != source:
            continue
        if mode is not None and r["mode"] != mode:
            continue
        action = r["action"]
        if action in months[m - 1]:
            months[m - 1][action] += 1
        rows.append(
            {
                "id": r["id"],
                "ts": r["ts"],
                "month": m,
                "action": action,
                "mode": r["mode"],
                "source": r["source"],
                "document_id": r["document_id"],
                "title": r["title"],
                "review_id": r["review_id"],
                "recommendation": r["recommendation"],
                "reason": r["reason"],
                "actor": r["actor"],
                "score": json.loads(r["score"]) if r["score"] else None,
            }
        )
    return {"year": year, "months": months, "rows": rows, "years": years}
