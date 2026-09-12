"""Business SLA and path panel (T86).

The article's measurement point — where did the time go — made a leadership
view. From the one-row-per-answer telemetry (``SqlTelemetry.events``) plus the
``kf.explain.requested`` signal (T81), this computes, per persona and per data
type: median and p95 time-to-answer, the fast-path vs agent share, the
explain-request rate, and the token cost per answer — and a headline SLA line
that turns "where did the time go" from a trace detail into a commitment:

    KPI and known questions answered in <X> s (target 1–3 s); exploratory
    questions <Y> s.

Fast path = the router answered on the extractive/fast tier (a known or KPI
question, fast by design); agent = it reasoned (deep/escalation) — expected to
take longer.
"""

from __future__ import annotations

import datetime as _dt

_FAST_TIERS = {"none", "fast"}
_AGENT_TIERS = {"deep", "escalation"}

#: lead-citation source kind → the data type a leader reads
SOURCE_LABEL = {
    "": "Uncited",
    "internal": "Documents",
    "files": "Documents",
    "upload": "Documents",
    "github": "Code / repos",
    "jira": "Jira",
    "confluence": "Confluence",
}


def _label(source_kind: str) -> str:
    return SOURCE_LABEL.get(source_kind or "", (source_kind or "other").replace("_", " ").title())


def _pct(values: list[float], q: float) -> float:
    """The ``q`` quantile (0..1) of ``values`` by nearest-rank; 0.0 when empty."""
    xs = sorted(values)
    if not xs:
        return 0.0
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(xs[i], 2)


def _is_fast(row: dict) -> bool:
    """Fast path = a known / KPI question answered on the extractive or fast
    tier; agent = the router reasoned. The reader-facing level is decisive first
    (a reasoning/escalation answer is agent even when the extractive floor left
    its tier ``none``); tier settles the rest."""
    level = str(row.get("level") or "").lower()
    if level.startswith(("reason", "escalation")):
        return False
    tier = (row.get("tier") or "none").lower()
    if tier in _AGENT_TIERS:
        return False
    return True


def _cell(rows: list[dict]) -> dict:
    lat = [r["latency_ms"] for r in rows]
    n = len(rows)
    fast = sum(1 for r in rows if _is_fast(r))
    cost = sum(float(r.get("cost") or 0.0) for r in rows)
    return {
        "n": n,
        "p50_ms": _pct(lat, 0.5),
        "p95_ms": _pct(lat, 0.95),
        "fast_share": round(fast / n, 3) if n else 0.0,
        "agent_share": round((n - fast) / n, 3) if n else 0.0,
        "cost_per_answer": round(cost / n, 6) if n else 0.0,
    }


def _explain_counts(platform, tenant: str) -> tuple[int, dict[str, int]]:
    """Total ``kf.explain.requested`` signals and the per-persona breakdown."""
    import json

    total, by_persona = 0, {}
    try:
        rows = platform.db.query(
            "SELECT attrs FROM spans WHERE tenant=? AND name='kf.explain.requested'", (tenant,)
        )
    except Exception:
        return 0, {}
    for r in rows:
        total += 1
        try:
            a = json.loads(r["attrs"]) if r["attrs"] else {}
        except Exception:
            a = {}
        p = a.get("persona") or "general"
        by_persona[p] = by_persona.get(p, 0) + 1
    return total, by_persona


def service_levels(platform, tenant: str) -> dict:
    """The Service-levels report: headline SLA line, per-persona and per-data-type
    time-to-answer with the fast-vs-agent split, explain-request rate and cost."""
    events = [e for e in platform.telemetry.events(tenant) if e.get("answered")]
    n = len(events)
    explain_total, explain_by_persona = _explain_counts(platform, tenant)

    # per persona
    personas: dict[str, list[dict]] = {}
    for e in events:
        personas.setdefault(e.get("persona") or "general", []).append(e)
    by_persona = []
    for p, rows in sorted(personas.items()):
        cell = _cell(rows)
        cell["persona"] = p
        cell["explain_rate"] = round(explain_by_persona.get(p, 0) / len(rows), 3) if rows else 0.0
        by_persona.append(cell)

    # per data type (lead-citation source kind)
    dtypes: dict[str, list[dict]] = {}
    for e in events:
        dtypes.setdefault(e.get("source_kind") or "", []).append(e)
    by_data_type = []
    for sk, rows in dtypes.items():
        cell = _cell(rows)
        cell["source_kind"] = sk
        cell["data_type"] = _label(sk)
        by_data_type.append(cell)
    by_data_type.sort(key=lambda c: c["n"], reverse=True)

    fast_rows = [e for e in events if _is_fast(e)]
    agent_rows = [e for e in events if not _is_fast(e)]
    fast_lat = [r["latency_ms"] for r in fast_rows]
    agent_lat = [r["latency_ms"] for r in agent_rows]
    total_cost = sum(float(e.get("cost") or 0.0) for e in events)

    fast_p50_s = round(_pct(fast_lat, 0.5) / 1000.0, 2)
    fast_p95_s = round(_pct(fast_lat, 0.95) / 1000.0, 2)
    agent_p50_s = round(_pct(agent_lat, 0.5) / 1000.0, 2)
    sla_line = (
        f"KPI and known questions answered in {fast_p50_s} s "
        f"(p95 {fast_p95_s} s; target 1–3 s); exploratory questions {agent_p50_s} s."
    )
    return {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "tenant": tenant,
        "n_answers": n,
        "headline": {
            "fast_p50_ms": _pct(fast_lat, 0.5),
            "fast_p95_ms": _pct(fast_lat, 0.95),
            "agent_p50_ms": _pct(agent_lat, 0.5),
            "agent_p95_ms": _pct(agent_lat, 0.95),
            "fast_share": round(len(fast_rows) / n, 3) if n else 0.0,
            "agent_share": round(len(agent_rows) / n, 3) if n else 0.0,
            "explain_rate": round(explain_total / n, 3) if n else 0.0,
            "cost_per_answer": round(total_cost / n, 6) if n else 0.0,
            "sla_line": sla_line,
            "reading_line": (
                "Known questions are fast by design; reasoning takes longer and that is expected."
            ),
        },
        "by_persona": by_persona,
        "by_data_type": by_data_type,
    }
