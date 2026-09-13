"""T96 — leadership ROI and technical OTel/observability, read from the spine.

Two dashboards, both composed (no new counters) from what the platform already
records — the telemetry spine (answer spans), the SLA report (T86), the API
ledger insights (T55) and the analytics rollup (WS3):

* :func:`overview` — the leadership view: value delivered (hours saved), cost and
  cost avoided, the ROI ratio, adoption, quality and the service SLA line. Two
  Settings knobs drive the money math — minutes saved per answered question and
  the loaded hourly rate — persisted to ``data/roi_settings.json``.
* :func:`observability` — the technical view: a recent-trace list, error rate,
  latency percentiles, and a reconciliation check that the dashboard totals match
  the telemetry counters within one percent. The per-answer span waterfall is
  served by :func:`waterfall` (the spine's spans for one trace).

Every number reconciles with a source the Admin can already see, so leadership
and engineering read the same fabric two ways. JSON-serialisable throughout.
"""

from __future__ import annotations

from .. import fabric_data
from . import api_ledger, insights, sla

# Settings knobs (leadership-tunable) with defensible defaults. These now drive
# only the SECONDARY productivity view (hours saved) — the headline value and the
# ROI ratio are computed from real token consumption (T125), not fed in here.
SETTINGS_DEFAULTS = {
    "minutes_saved_per_question": 8,  # analyst minutes an answered question saves
    "loaded_rate_per_hour": 75.0,  # fully-loaded cost of an hour of that time (USD)
}
_SETTINGS_FILE = "roi_settings.json"

# T125 — the frontier baseline for "value delivered". Value is what it WOULD cost
# to answer the same real token volume on a premium commercial model; the ratio is
# that value over what the fabric actually spent (paid API + imputed open-source
# compute). Both come from live consumption, so the ROI page stops being a
# calculator we feed and becomes a real-time read of question count × tokens.
_FRONTIER_FALLBACK = {"input": 3.0, "output": 15.0}  # USD / million tokens


def frontier_price() -> dict:
    """Per-million-token price of the premium (frontier) model — the baseline the
    fabric's answering is valued against. Read from ``prices.json`` for the large
    model, with a defensible fallback so the page renders even without prices."""
    from ..adapters import model as modelmod

    p = api_ledger.prices().get(modelmod.DEFAULT_LARGE) or {}
    return {
        "model": modelmod.DEFAULT_LARGE,
        "input": float(p.get("input", _FRONTIER_FALLBACK["input"])),
        "output": float(p.get("output", _FRONTIER_FALLBACK["output"])),
    }


def _frontier_value(tokens_in: int, tokens_out: int) -> float:
    """USD the same real token consumption would cost at the frontier model's
    published rate — the market value of the answering the fabric delivered."""
    fp = frontier_price()
    m = 1_000_000.0
    return round(int(tokens_in or 0) * fp["input"] / m + int(tokens_out or 0) * fp["output"] / m, 6)


def get_settings() -> dict:
    """The ROI settings, defaults filled in for any missing/invalid knob."""
    stored = fabric_data.read_json(fabric_data.data_path(_SETTINGS_FILE), {}) or {}
    out = dict(SETTINGS_DEFAULTS)
    for k in SETTINGS_DEFAULTS:
        v = stored.get(k)
        if isinstance(v, (int, float)) and v >= 0:
            out[k] = v
    return out


def set_settings(patch: dict) -> dict:
    """Update the ROI settings (only the known, non-negative numeric knobs) and
    return the full settings. Invalid values are ignored, never stored."""
    cur = get_settings()
    for k in SETTINGS_DEFAULTS:
        if k in (patch or {}):
            try:
                v = float(patch[k])
            except (TypeError, ValueError):
                continue
            if v >= 0:
                cur[k] = int(v) if k == "minutes_saved_per_question" else round(v, 2)
    fabric_data.write_json(fabric_data.data_path(_SETTINGS_FILE, mkdir=True), cur)
    return cur


def _wow_growth(timeseries: list[dict]) -> float:
    """Week-over-week (or bucket-over-bucket) answer growth from the analytics
    timeseries: the last bucket's answers over the previous bucket's, minus one."""
    ts = [b for b in (timeseries or []) if b.get("answers")]
    if len(ts) < 2:
        return 0.0
    prev, last = ts[-2]["answers"], ts[-1]["answers"]
    return round((last - prev) / prev, 4) if prev else 0.0


def _negative_feedback_rate(platform, tenant: str, answered: int) -> float:
    try:
        n = len(platform.curation.list(tenant, "negative-feedback"))
    except Exception:
        n = 0
    return round(n / answered, 4) if answered else 0.0


def overview(platform, tenant: str, window: str = "7d") -> dict:
    """The leadership ROI page: value, cost, ROI ratio, adoption, quality, service.

    T125 — the headline value and the ROI ratio are REAL-TIME, driven by the live
    question count and the real token consumption of EVERY answer (paid provider,
    open-source LLM, or extractive NLP alike) — not a fed calculator:

    * **value delivered** = what it WOULD cost to answer the same real token
      volume on the frontier model (:func:`_frontier_value`) — the market worth of
      the answering the fabric produced;
    * **model spend** = what the fabric actually spent (paid API + the imputed
      self-hosted compute cost of the open-source / extractive path);
    * **ROI ratio** = value delivered ÷ model spend.

    The minutes-saved / loaded-rate settings now drive only the SECONDARY labour
    view (hours saved, labour value) — kept for leadership, but no longer the
    definition of value and no longer needed for the ratio. Every rollup still
    reconciles with the analytics / SLA / insights panels the Admin can open.
    """
    a = platform.telemetry.analytics(tenant, window)
    svc = sla.service_levels(platform, tenant)
    head = svc.get("headline", {})
    burn = insights.burn_rate(1)
    settings = get_settings()

    answered = int(a.get("answers", 0) or 0)
    tokens_in = int(a.get("tokens_in", 0) or 0)
    tokens_out = int(a.get("tokens_out", 0) or 0)
    total_tokens = tokens_in + tokens_out

    # Secondary (labour) view — still tunable, no longer the headline value.
    minutes = float(settings["minutes_saved_per_question"])
    rate = float(settings["loaded_rate_per_hour"])
    hours_saved = round(answered * minutes / 60.0, 2)
    labour_value_usd = round(hours_saved * rate, 2)

    # Real-time, consumption-driven value + spend (the headline).
    spend = round(float(a.get("total_cost", 0.0) or 0.0), 6)
    value_usd = _frontier_value(tokens_in, tokens_out)
    cost_avoided = float(a.get("total_cost_saved", 0.0) or 0.0)
    cost_per_answer = round(spend / answered, 6) if answered else 0.0
    tokens_per_answer = round(total_tokens / answered, 1) if answered else 0.0
    per_user = a.get("per_user", {}) or {}
    fp = frontier_price()

    return {
        "tenant": tenant,
        "window": window,
        "settings": settings,
        "value": {
            "questions_answered": answered,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "total_tokens": total_tokens,
            "tokens_per_answer": tokens_per_answer,
            # secondary labour view (derived from the tunable knobs, kept for leadership)
            "hours_saved": hours_saved,
            "minutes_saved_per_question": minutes,
            "labour_value_usd": labour_value_usd,
            "by_persona": a.get("answers_by_persona", {}),
            "by_role": {r: v.get("answers", 0) for r, v in (a.get("per_role", {}) or {}).items()},
        },
        "cost": {
            "total_spend_usd": spend,
            "cost_avoided_usd": round(cost_avoided, 6),
            "cost_per_answer_usd": cost_per_answer,
            "projected_monthly_usd": round(float(burn.get("projected_per_day", 0.0)) * 30.0, 2),
        },
        "roi": {
            "value_delivered_usd": value_usd,
            "spend_usd": spend,
            "ratio": round(value_usd / spend, 2) if spend else None,
            "frontier_model": fp["model"],
            "frontier_input_per_mtok": fp["input"],
            "frontier_output_per_mtok": fp["output"],
            "loaded_rate_per_hour": rate,
            "labour_value_usd": labour_value_usd,
            "wow_answer_growth": _wow_growth(a.get("timeseries", [])),
        },
        "adoption": {
            "active_users": len(per_user),
            "questions_per_user": round(answered / len(per_user), 2) if per_user else 0.0,
            "wow_growth": _wow_growth(a.get("timeseries", [])),
            "by_user": per_user,
        },
        "quality": {
            "trust_avg": a.get("grounding_avg", 0.0),
            "citation_coverage": a.get("citation_coverage", 0.0),
            "negative_feedback_rate": _negative_feedback_rate(platform, tenant, answered),
            "clarify_back_rate": a.get("clarify_back_rate", 0.0),
        },
        "service": {
            "latency_p50_ms": a.get("latency_p50_ms", 0.0),
            "latency_p95_ms": a.get("latency_p95_ms", 0.0),
            "fast_share": head.get("fast_share", 0.0),
            "agent_share": head.get("agent_share", 0.0),
            "sla_line": head.get("sla_line", ""),
        },
        "definitions": {
            "value_delivered_usd": (
                "What answering the same real token volume would cost on the premium "
                f"model ({fp['model']}): tokens in × ${fp['input']}/M + out × ${fp['output']}/M. "
                "Derived live from consumption, not a fed rate."
            ),
            "total_spend_usd": (
                "What the fabric actually spent: paid provider calls plus the imputed "
                "self-hosted compute cost of the open-source / extractive answers."
            ),
            "ratio": "Value delivered divided by real model spend (blank only when nothing ran).",
            "total_tokens": "Real tokens consumed across every answer, keyless paths included.",
            "cost_avoided_usd": "Spend avoided by routing and caching (cost saved).",
            "cost_per_answer_usd": "Real model spend divided by answered questions.",
            "projected_monthly_usd": "The current daily burn scaled to thirty days.",
            "hours_saved": "Secondary view: answered questions × minutes each saves, in hours.",
            "labour_value_usd": "Secondary view: hours saved valued at the loaded hourly rate.",
            "trust_avg": "Average grounding score across answers.",
            "negative_feedback_rate": "Thumbs-down feedback over answered questions.",
            "fast_share": "Share of answers served on the fast (non-agent) path.",
        },
    }


def _is_error(ev: dict) -> bool:
    """An answer 'error' for the error-rate: a gap (no evidence) or a clarify —
    an answer that could not be delivered as asked."""
    return (ev.get("kind") or ev.get("level") or "") in ("gap", "clarify")


def observability(platform, tenant: str, limit: int = 20) -> dict:
    """The technical OTel page (list view): recent traces, error rate, latency
    percentiles, and the reconciliation check. The per-trace span waterfall is
    :func:`waterfall`."""
    events = platform.telemetry.events(tenant)
    metrics = platform.telemetry.metrics(tenant)
    a = platform.telemetry.analytics(tenant, "all")

    n = len(events)
    errors = sum(1 for e in events if _is_error(e))
    recent = list(reversed(events))[: max(1, int(limit))]
    traces = [
        {
            "trace_id": e.get("trace_id") or e.get("trajectory_id") or "",
            "subject": e.get("subject", ""),
            "level": e.get("level", ""),
            "kind": e.get("kind", ""),
            "latency_ms": e.get("latency_ms") or (e.get("timing") or {}).get("total_ms") or 0.0,
            "cost_usd": e.get("cost", 0.0),
            "citations": e.get("citations") or e.get("cited") or 0,
            "error": _is_error(e),
        }
        for e in recent
    ]

    # reconciliation: the analytics rollup and the raw span metrics must agree on
    # answer count and total cost within one percent (they read the same spans).
    def _within(x, y, pct=1.0):
        x, y = float(x or 0.0), float(y or 0.0)
        if x == 0 and y == 0:
            return True, 0.0
        base = max(abs(x), abs(y)) or 1.0
        delta = round(100.0 * abs(x - y) / base, 4)
        return delta <= pct, delta

    ans_ok, ans_delta = _within(a.get("answers", 0), metrics.get("answers", 0))
    cost_ok, cost_delta = _within(a.get("total_cost", 0.0), metrics.get("total_cost", 0.0))

    return {
        "tenant": tenant,
        "traces": traces,
        "error_rate": round(errors / n, 4) if n else 0.0,
        "answers": n,
        "latency_p50_ms": metrics.get("latency_p50_ms", 0.0),
        "latency_p95_ms": metrics.get("latency_p95_ms", 0.0),
        "reconciliation": {
            "answers": {
                "analytics": a.get("answers", 0),
                "metrics": metrics.get("answers", 0),
                "delta_pct": ans_delta,
                "ok": ans_ok,
            },
            "cost": {
                "analytics": round(float(a.get("total_cost", 0.0)), 6),
                "metrics": round(float(metrics.get("total_cost", 0.0)), 6),
                "delta_pct": cost_delta,
                "ok": cost_ok,
            },
            "ok": ans_ok and cost_ok,
            "tolerance_pct": 1.0,
        },
        "definitions": {
            "error_rate": "Share of answers that were a gap or a clarify, not a direct answer.",
            "reconciliation": "Dashboard totals must match the raw span counters within 1 percent.",
            "latency_p95_ms": "Answer latency at the ninety-fifth percentile.",
        },
    }


def waterfall(platform, tenant: str, trace_id: str) -> dict:
    """The span waterfall for one answer: each span's name, stage, start offset and
    duration, ordered, so the OTel page can draw the timeline. Tenant-scoped."""
    spans = [s for s in platform.telemetry.trace(trace_id) if s.get("tenant") in (tenant, None)]
    spans = sorted(spans, key=lambda s: s.get("started_at", 0))
    base = spans[0].get("started_at", 0) if spans else 0
    rows = [
        {
            "name": s.get("name", ""),
            "stage": s.get("stage", ""),
            "offset_ms": round((s.get("started_at", base) - base) * 1000.0, 2),
            "duration_ms": round(float(s.get("duration_ms", 0.0) or 0.0), 2),
            "status": s.get("status", ""),
        }
        for s in spans
    ]
    total = round(sum(r["duration_ms"] for r in rows), 2)
    return {"trace_id": trace_id, "spans": rows, "total_ms": total}


__all__ = [
    "SETTINGS_DEFAULTS",
    "get_settings",
    "set_settings",
    "frontier_price",
    "overview",
    "observability",
    "waterfall",
]
