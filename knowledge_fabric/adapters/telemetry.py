"""Telemetry adapter: one span row per stage, one trace per answer/ingest job.

Spans carry cost, tokens, latency, grounding and citation count so the
dashboards and /metrics API (Section 13.5) read directly from the store.
This is the local OpenTelemetry stand-in; the cloud adapter exports the same
spans to a managed backend.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from ..contracts.types import new_id
from ..stores.db import Database


class _Span:
    def __init__(self, tel: "SqlTelemetry", name: str, attrs: dict):
        self.tel = tel
        self.name = name
        self.attrs = dict(attrs)
        self.trace_id = attrs.get("trace_id") or new_id("trace_")
        self.attrs["trace_id"] = self.trace_id
        self._start = 0.0

    def set(self, **attrs) -> None:
        self.attrs.update(attrs)

    def __enter__(self) -> "_Span":
        self._start = time.time()
        return self

    def __exit__(self, *exc) -> None:
        dur = (time.time() - self._start) * 1000
        self.tel._write(self, dur)


class SqlTelemetry:
    def __init__(self, db: Database):
        self.db = db

    def span(self, name: str, attrs: dict) -> _Span:
        return _Span(self, name, attrs)

    def record(self, name: str, attrs: dict) -> None:
        s = _Span(self, name, attrs)
        self._write(s, attrs.get("duration_ms", 0.0))

    def _write(self, s: _Span, dur: float) -> None:
        a = s.attrs
        self.db.execute(
            """INSERT INTO spans(trace_id,tenant,name,attrs,started_at,duration_ms,cost,
               tokens,tier,grounding,citations_count,stage) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (s.trace_id, a.get("tenant", ""), s.name, json.dumps(a, default=str),
             s._start or time.time(), dur, float(a.get("cost", 0.0)), int(a.get("tokens", 0)),
             a.get("tier", ""), float(a.get("grounding", 0.0)),
             int(a.get("citations_count", 0)), a.get("stage", s.name)))

    # --- read side (dashboards / /metrics) ---
    def trace(self, trace_id: str) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM spans WHERE trace_id=? ORDER BY id", (trace_id,))]

    def metrics(self, tenant: str) -> dict:
        rows = self.db.query(
            "SELECT * FROM spans WHERE tenant=? AND name='answer'", (tenant,))
        answers = [dict(r) for r in rows]
        n = len(answers)
        cost = sum(a["cost"] for a in answers)
        tokens = sum(a["tokens"] for a in answers)
        lat = sorted(a["duration_ms"] for a in answers)
        clarifies = sum(1 for a in answers if '"clarify"' in (a["attrs"] or "") or a["grounding"] == 0)

        def pct(p):
            if not lat:
                return 0.0
            return lat[min(len(lat) - 1, int(len(lat) * p))]

        # cost by tier and by stage
        by_tier: dict[str, float] = {}
        for r in self.db.query("SELECT tier, SUM(cost) c FROM spans WHERE tenant=? GROUP BY tier", (tenant,)):
            by_tier[r["tier"] or "none"] = round(r["c"] or 0.0, 6)
        by_stage: dict[str, float] = {}
        for r in self.db.query("SELECT stage, SUM(cost) c FROM spans WHERE tenant=? GROUP BY stage", (tenant,)):
            by_stage[r["stage"] or "?"] = round(r["c"] or 0.0, 6)

        return {
            "tenant": tenant,
            "answers": n,
            "total_cost": round(cost, 6),
            "total_tokens": tokens,
            "latency_p50_ms": round(pct(0.5), 2),
            "latency_p95_ms": round(pct(0.95), 2),
            "cost_by_tier": by_tier,
            "cost_by_stage": by_stage,
            "grounding_avg": round(sum(a["grounding"] for a in answers) / n, 4) if n else 0.0,
            "citation_coverage": round(sum(1 for a in answers if a["citations_count"] > 0) / n, 4) if n else 0.0,
            "clarify_back_rate": round(clarifies / n, 4) if n else 0.0,
        }
