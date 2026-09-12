"""Telemetry insights (T55) — cost, efficiency and timing read from the ledger.

The API call ledger (``api_ledger``) is the source of truth: every function
here re-reads its rows and reconciles with the ledger by construction (the
phase costs sum to the ledger total, efficiency is a ratio of ledger tokens,
and so on). Functions that need per-answer wall-clock or phase timing take an
``events`` list — the flat answer-event rows from the telemetry adapter,
optionally carrying a ``timing`` block written by ``telemetry/timing.py`` (T56).
Every return value is a plain dict or list, JSON-serialisable, ready for the
Admin -> Models / Usage panels.

Concepts reproduced from the MIT-licensed *token-meter* project — cost
breakdown by phase, active-versus-idle time, waste, efficiency, per-phase cost,
provider-quota snapshots and percentile timing, plus its ``quotas/`` registry
pattern. This is a clean-room re-implementation (no token-meter code copied),
credited in ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from ..adapters import model
from . import api_ledger

# A small fixed reduction goal for prompt waste, as a percentage of prompt
# tokens (token-meter's "trim target"): keep unused evidence under this share.
WASTE_TARGET_PCT = 20.0

# Which ledger purpose belongs to which answer phase. Retrieval is a synthetic
# zero-cost phase (no model call); anything unmapped falls to "other" so the
# breakdown always sums back to the ledger total.
_PHASE_BY_PURPOSE = {
    "answer_bake": "summary",
    "repo_summary": "summary",
    "agent_step": "agent_step",
    "translate": "translate",
    "image_describe": "image_describe",
}
_PHASE_ORDER = ("retrieval", "summary", "agent_step", "translate", "image_describe", "other")

# Purposes whose output is cited, user-facing answer text (the numerator of the
# efficiency ratio). Bookkeeping calls (classify, question_gen, ...) are not.
_ANSWER_PURPOSES = frozenset({"answer_bake", "agent_step"})


# --------------------------------------------------------------------------- #
# Cost split by phase                                                         #
# --------------------------------------------------------------------------- #
def _phase_of(purpose: str) -> str:
    return _PHASE_BY_PURPOSE.get(purpose, "other")


def cost_breakdown(days: int = 7) -> list[dict]:
    """Cost, call count and tokens split by answer phase.

    Retrieval is always present with zero cost (it makes no model call). Every
    ledger row lands in exactly one phase, so the phase costs sum to the ledger
    total for ``days`` — reconciliation by construction.
    """
    items = api_ledger.rows(days)
    buckets: dict[str, dict] = {"retrieval": {"cost_usd": 0.0, "calls": 0, "tokens": 0}}
    for r in items:
        phase = _phase_of(r.get("purpose", ""))
        b = buckets.setdefault(phase, {"cost_usd": 0.0, "calls": 0, "tokens": 0})
        b["cost_usd"] += float(r.get("cost_usd", 0.0) or 0.0)
        b["calls"] += 1
        b["tokens"] += int(r.get("input_tokens", 0) or 0) + int(r.get("output_tokens", 0) or 0)
    ordered = list(_PHASE_ORDER) + [p for p in sorted(buckets) if p not in _PHASE_ORDER]
    out = []
    for phase in ordered:
        if phase not in buckets:
            continue
        b = buckets[phase]
        out.append(
            {
                "phase": phase,
                "cost_usd": round(b["cost_usd"], 8),
                "calls": b["calls"],
                "tokens": b["tokens"],
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Active versus idle time                                                     #
# --------------------------------------------------------------------------- #
def _event_active_total(event: dict) -> tuple[float, float]:
    """(active_ms, total_ms) for one answer event.

    Prefers a ``timing`` block (T56); falls back to flat ``active_ms`` /
    ``latency_ms`` fields, then to the sum of phase durations. Active is clamped
    to the wall clock so idle never goes negative.
    """
    timing = event.get("timing") if isinstance(event.get("timing"), dict) else {}
    total = float(timing.get("total_ms", event.get("latency_ms", 0.0)) or 0.0)
    if "active_ms" in timing:
        active = float(timing.get("active_ms") or 0.0)
    elif "active_ms" in event:
        active = float(event.get("active_ms") or 0.0)
    else:
        active = float(sum((timing.get("phase_ms") or {}).values()))
    if total:
        active = min(active, total)
    return active, total


def _split(active: float, idle: float) -> dict:
    total = active + idle
    return {
        "active_ms": round(active, 2),
        "idle_ms": round(idle, 2),
        "active_pct": round(100.0 * active / total, 2) if total else 0.0,
    }


def active_vs_idle(events: list[dict]) -> dict:
    """Per-answer and aggregate active/idle split.

    Active is the summed model-plus-tool time; idle is wall clock (ask ->
    answer) minus active. Returns ``{per_answer: [...], aggregate: {...}}`` with
    each entry ``{active_ms, idle_ms, active_pct}``.
    """
    per_answer, agg_active, agg_idle = [], 0.0, 0.0
    for e in events or []:
        active, total = _event_active_total(e)
        idle = max(0.0, total - active)
        per_answer.append(_split(active, idle))
        agg_active += active
        agg_idle += idle
    return {"per_answer": per_answer, "aggregate": _split(agg_active, agg_idle)}


# --------------------------------------------------------------------------- #
# Waste and efficiency                                                        #
# --------------------------------------------------------------------------- #
def waste(prompt_tokens: int, cited_tokens: int, target_pct: float = WASTE_TARGET_PCT) -> dict:
    """Evidence tokens that reached no cited sentence, as a share of the prompt.

    ``waste_tokens = prompt_tokens - cited_tokens`` — everything paid for in the
    prompt that did not end up in a cited sentence. ``target_pct`` is the fixed
    reduction goal to keep waste beneath.
    """
    prompt_tokens = int(prompt_tokens)
    cited_tokens = int(cited_tokens)
    waste_tokens = max(0, prompt_tokens - cited_tokens)
    waste_pct = round(100.0 * waste_tokens / prompt_tokens, 2) if prompt_tokens else 0.0
    return {
        "waste_tokens": waste_tokens,
        "prompt_tokens": prompt_tokens,
        "waste_pct": waste_pct,
        "target_pct": float(target_pct),
    }


def efficiency(days: int = 7) -> dict:
    """Cited output tokens divided by total tokens (input + output).

    The numerator is the output of answer-producing calls; the denominator is
    every token the ledger billed. The ratio is in ``[0, 1]`` by construction
    (cited output is a subset of output, which is part of the total).
    """
    items = api_ledger.rows(days)
    cited = sum(
        int(r.get("output_tokens", 0) or 0)
        for r in items
        if r.get("purpose", "") in _ANSWER_PURPOSES
    )
    total = sum(
        int(r.get("input_tokens", 0) or 0) + int(r.get("output_tokens", 0) or 0) for r in items
    )
    ratio = round(cited / total, 4) if total else 0.0
    return {
        "efficiency": max(0.0, min(1.0, ratio)),
        "cited_output_tokens": cited,
        "total_tokens": total,
    }


# --------------------------------------------------------------------------- #
# Cost per dimension                                                          #
# --------------------------------------------------------------------------- #
# Friendly dimension name -> the ledger column ``breakdown`` groups on.
_DIMENSION_COLUMN = {
    "question_type": "purpose",  # each purpose is a kind of answer/question
    "model": "model",
    "source": "repo",
    "workflow": "workflow",
}


def _reduce(bucketed: dict) -> dict:
    """Collapse a ``breakdown`` result to ``{bucket: {cost_usd, calls, tokens}}``."""
    return {
        k: {
            "cost_usd": v["cost_usd"],
            "calls": v["calls"],
            "tokens": v["input_tokens"] + v["output_tokens"],
        }
        for k, v in bucketed.items()
    }


def per_dimension(days: int = 7) -> dict:
    """Cost grouped by each ledger dimension, reusing ``api_ledger.breakdown``.

    Each dimension's buckets sum back to the ledger total cost, so any pivot the
    Admin -> Usage panel shows reconciles with the totals card.
    """
    items = api_ledger.rows(days)
    return {
        dim: _reduce(api_ledger.breakdown(items, col)) for dim, col in _DIMENSION_COLUMN.items()
    }


# --------------------------------------------------------------------------- #
# Provider quota (registry pattern)                                           #
# --------------------------------------------------------------------------- #
def _anthropic_quota() -> dict:
    """Balance/rate from ``data/provider_status.json`` if the doctor wrote them,
    else an honest ``unknown`` (no live headers offline)."""
    status = model.provider_status()
    out = {"provider": "anthropic"}
    fields = ("balance_usd", "rate_limit", "requests_per_minute", "tokens_per_minute", "limit")
    found = {k: status[k] for k in fields if k in status}
    if found:
        out.update(found)
        return out
    out["limit"] = "unknown"
    return out


def _open_source_quota() -> dict:
    return {"provider": "open-source", "limit": "unlimited (local)"}


# provider -> callable, adaptable like token-meter's ``quotas/`` folder: add a
# provider by registering another entry here.
QUOTA_REGISTRY: dict[str, Callable[[], dict]] = {
    "anthropic": _anthropic_quota,
    "open-source": _open_source_quota,
}


def _current_provider() -> str:
    """anthropic when a key/pin is in play, else the local open-source path."""
    status = model.provider_status()
    if status.get("provider"):
        return str(status["provider"])
    mode = (os.environ.get("KF_MODEL_MODE") or "").lower()
    if mode == "anthropic" or os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "open-source"


def provider_quota(provider: str | None = None) -> dict:
    """Quota snapshot for ``provider`` (default: the current one)."""
    name = provider or _current_provider()
    fn = QUOTA_REGISTRY.get(name)
    if fn is None:
        return {"provider": name, "limit": "unknown"}
    return fn()


# --------------------------------------------------------------------------- #
# Percentiles and timing                                                      #
# --------------------------------------------------------------------------- #
def percentiles(values: list[float]) -> dict:
    """p50/p95/p99 of ``values`` by the nearest-rank method (index ``n*p``)."""
    xs = sorted(float(v) for v in values)
    if not xs:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def at(p: float) -> float:
        return xs[min(len(xs) - 1, int(len(xs) * p / 100.0))]

    return {"p50": round(at(50), 2), "p95": round(at(95), 2), "p99": round(at(99), 2)}


def timing_percentiles(events: list[dict]) -> dict:
    """p50/p95/p99 of answer latency and of each phase across ``events``."""
    lat = [
        float(e.get("latency_ms") or (e.get("timing") or {}).get("total_ms") or 0.0)
        for e in (events or [])
    ]
    phases: dict[str, list[float]] = {}
    for e in events or []:
        pm = (e.get("timing") or {}).get("phase_ms") or {}
        for name, ms in pm.items():
            phases.setdefault(name, []).append(float(ms))
    return {
        "latency": percentiles(lat),
        "phases": {name: percentiles(v) for name, v in sorted(phases.items())},
    }


# --------------------------------------------------------------------------- #
# Burn rate                                                                   #
# --------------------------------------------------------------------------- #
def burn_rate(days: int = 1) -> dict:
    """Cost per hour at the current rate and the projected cost per day."""
    items = api_ledger.rows(days)
    total = sum(float(r.get("cost_usd", 0.0) or 0.0) for r in items)
    hours = max(1, int(days)) * 24.0
    cost_per_hour = total / hours
    return {
        "cost_per_hour": round(cost_per_hour, 6),
        "projected_per_day": round(cost_per_hour * 24.0, 6),
        "cost_usd": round(total, 6),
        "hours": hours,
    }


# --------------------------------------------------------------------------- #
# Definitions and overview                                                    #
# --------------------------------------------------------------------------- #
def definitions() -> dict:
    """One-line definition per metric for the ``?`` help sheet (T29 style)."""
    return {
        "cost_breakdown": "Model spend split by answer phase; retrieval is always zero.",
        "active_ms": "Time spent doing model and tool work on an answer.",
        "idle_ms": "Wall-clock time from ask to answer minus the active time.",
        "active_pct": "Active time as a percentage of total answer time.",
        "waste_tokens": "Prompt tokens that reached no cited sentence.",
        "waste_pct": "Waste tokens as a percentage of prompt tokens.",
        "target_pct": "The fixed goal to keep prompt waste beneath.",
        "efficiency": "Cited output tokens over total tokens billed, in zero to one.",
        "per_dimension": "Cost grouped by purpose, model, source and workflow.",
        "provider_quota": "Current provider balance or rate limit, or unlimited when local.",
        "cost_per_hour": "Cost divided by the hours in the window.",
        "projected_per_day": "The hourly burn rate scaled to a full day.",
        "latency_p50": "Median answer latency in milliseconds.",
        "latency_p95": "Answer latency at the ninety-fifth percentile.",
    }


def overview(days: int = 7, events: list[dict] | None = None) -> dict:
    """One bundled payload for the Admin -> Models / Usage panels.

    Reads the ledger for cost, efficiency, per-dimension spend, the provider
    quota and the burn rate; folds in active-versus-idle and timing percentiles
    when answer ``events`` are supplied.
    """
    items = api_ledger.rows(days)
    payload = {
        "days": days,
        "totals": api_ledger.summary(items),
        "cost_breakdown": cost_breakdown(days),
        "efficiency": efficiency(days),
        "per_dimension": per_dimension(days),
        "provider_quota": provider_quota(),
        "burn_rate": burn_rate(1),
        "definitions": definitions(),
    }
    if events is not None:
        payload["active_vs_idle"] = active_vs_idle(events)
        payload["timing_percentiles"] = timing_percentiles(events)
    return payload


__all__ = [
    "WASTE_TARGET_PCT",
    "QUOTA_REGISTRY",
    "active_vs_idle",
    "burn_rate",
    "cost_breakdown",
    "definitions",
    "efficiency",
    "overview",
    "per_dimension",
    "percentiles",
    "provider_quota",
    "timing_percentiles",
    "waste",
]
