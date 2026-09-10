"""Telemetry adapter: one span row per stage, one trace per answer/ingest job.

Spans carry cost, tokens (in/out), latency, grounding, the model-selector
level + why, cache hits + cost saved by technique, language, sources, and the
requesting subject/roles — so the WS3 dashboards and /metrics + /api/analytics
read directly from the store. Local OpenTelemetry stand-in; the cloud adapter
exports the same spans to a managed backend.
"""

from __future__ import annotations

import json
import time

from ..contracts.types import new_id
from ..stores.db import Database


class _Span:
    def __init__(self, tel: SqlTelemetry, name: str, attrs: dict):
        self.tel = tel
        self.name = name
        self.attrs = dict(attrs)
        self.trace_id = attrs.get("trace_id") or new_id("trace_")
        self.attrs["trace_id"] = self.trace_id
        self._start = 0.0

    def set(self, **attrs) -> None:
        self.attrs.update(attrs)

    def __enter__(self) -> _Span:
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
        s._start = time.time()
        self._write(s, attrs.get("duration_ms", 0.0))

    def _write(self, s: _Span, dur: float) -> None:
        a = s.attrs
        why = a.get("why")
        if s.name.startswith("ingest."):  # mirror pipeline stages into the active run
            try:
                from ..ingestion import runs as _runs

                _runs.step_from_span(self, s.name, a, dur)
            except Exception:
                pass
        self.db.execute(
            """INSERT INTO spans(trace_id,tenant,name,attrs,started_at,duration_ms,cost,
               tokens,tier,grounding,citations_count,stage,subject,roles,level,why,
               tokens_in,tokens_out,cache_hit,cache_technique,cost_saved,lang,sources,
               model_name,complexity,dataset_version,reasoning)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                s.trace_id,
                a.get("tenant", ""),
                s.name,
                json.dumps(a, default=str),
                s._start or time.time(),
                dur,
                float(a.get("cost", 0.0)),
                int(a.get("tokens", 0)),
                a.get("tier", ""),
                float(a.get("grounding", 0.0)),
                int(a.get("citations_count", 0)),
                a.get("stage", s.name),
                a.get("subject", ""),
                ",".join(a.get("roles", []) or []),
                a.get("level", ""),
                json.dumps(why, default=str) if why is not None else None,
                int(a.get("tokens_in", 0)),
                int(a.get("tokens_out", 0)),
                int(a.get("cache_hit", 0)),
                a.get("cache_technique", ""),
                float(a.get("cost_saved", 0.0)),
                a.get("lang", ""),
                json.dumps(a.get("sources", []), default=str),
                a.get("model_name", ""),
                a.get("complexity", ""),
                int(a.get("dataset_version", 0) or 0),
                json.dumps(a.get("reasoning"), default=str)
                if a.get("reasoning") is not None
                else None,
            ),
        )

    # ---------------- read side ----------------------------------------
    def trace(self, trace_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.query("SELECT * FROM spans WHERE trace_id=? ORDER BY id", (trace_id,))
        ]

    def metrics(self, tenant: str) -> dict:
        rows = [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM spans WHERE tenant=? AND name='answer'", (tenant,)
            )
        ]
        n = len(rows)
        lat = sorted(r["duration_ms"] for r in rows)

        def pct(p):
            return lat[min(len(lat) - 1, int(len(lat) * p))] if lat else 0.0

        by_tier, by_stage = {}, {}
        for r in self.db.query(
            "SELECT tier, SUM(cost) c FROM spans WHERE tenant=? GROUP BY tier", (tenant,)
        ):
            by_tier[r["tier"] or "none"] = round(r["c"] or 0.0, 6)
        for r in self.db.query(
            "SELECT stage, SUM(cost) c FROM spans WHERE tenant=? GROUP BY stage", (tenant,)
        ):
            by_stage[r["stage"] or "?"] = round(r["c"] or 0.0, 6)
        return {
            "tenant": tenant,
            "answers": n,
            "total_cost": round(sum(r["cost"] for r in rows), 6),
            "total_tokens": sum(r["tokens"] for r in rows),
            "latency_p50_ms": round(pct(0.5), 2),
            "latency_p95_ms": round(pct(0.95), 2),
            "cost_by_tier": by_tier,
            "cost_by_stage": by_stage,
            "grounding_avg": round(sum(r["grounding"] for r in rows) / n, 4) if n else 0.0,
            "citation_coverage": round(sum(1 for r in rows if r["citations_count"] > 0) / n, 4)
            if n
            else 0.0,
            "clarify_back_rate": round(sum(1 for r in rows if r["level"] == "clarify") / n, 4)
            if n
            else 0.0,
        }

    def analytics(
        self, tenant: str, window: str = "7d", subject: str | None = None, role: str | None = None
    ) -> dict:
        """Filtered analytics for the Power BI-style dashboard (WS3 PROVE)."""
        now = time.time()
        horizon = {"24h": 86400, "7d": 7 * 86400, "all": 10**12}.get(window, 7 * 86400)
        floor = now - horizon
        where = ["tenant=?", "name='answer'", "started_at>=?"]
        params: list = [tenant, floor]
        if subject:
            where.append("subject=?")
            params.append(subject)
        if role:
            where.append("(','||roles||',') LIKE ?")
            params.append(f"%,{role},%")
        rows = [
            dict(r)
            for r in self.db.query(
                f"SELECT * FROM spans WHERE {' AND '.join(where)} ORDER BY started_at",
                tuple(params),
            )
        ]

        n = len(rows) or 1
        lat = sorted(r["duration_ms"] for r in rows)

        def pct(p):
            return round(lat[min(len(lat) - 1, int(len(lat) * p))], 1) if lat else 0.0

        # model routing with reasons: count by level + collected reason codes
        by_level, reason_counts = {}, {}
        for r in rows:
            by_level[r["level"] or "?"] = by_level.get(r["level"] or "?", 0) + 1
            try:
                why = json.loads(r["why"]) if r["why"] else {}
            except Exception:
                why = {}
            for rc in why.get("reasons") or []:
                code = rc.get("code", "?")
                reason_counts[code] = reason_counts.get(code, 0) + 1

        by_tier = {}
        for r in rows:
            by_tier[r["tier"] or "none"] = by_tier.get(r["tier"] or "none", 0) + 1
        by_complexity, models_used = {}, {}
        for r in rows:
            c = r["complexity"] or "n/a"
            by_complexity[c] = by_complexity.get(c, 0) + 1
            m = r["model_name"] or ("none (extractive)" if (r["tier"] in ("", "none")) else "?")
            models_used[m] = models_used.get(m, 0) + 1

        # savings by cache technique
        savings, cache_hits = {}, 0
        for r in rows:
            if r["cache_hit"]:
                cache_hits += 1
                t = r["cache_technique"] or "other"
                savings[t] = round(savings.get(t, 0.0) + (r["cost_saved"] or 0.0), 6)

        # per-user & per-role rollups
        per_user, per_role = {}, {}
        for r in rows:
            u = r["subject"] or "?"
            per_user.setdefault(u, {"answers": 0, "cost": 0.0, "tokens": 0})
            per_user[u]["answers"] += 1
            per_user[u]["cost"] = round(per_user[u]["cost"] + r["cost"], 6)
            per_user[u]["tokens"] += r["tokens"]
            for role_ in (r["roles"] or "").split(","):
                if not role_:
                    continue
                per_role.setdefault(role_, {"answers": 0, "cost": 0.0})
                per_role[role_]["answers"] += 1
                per_role[role_]["cost"] = round(per_role[role_]["cost"] + r["cost"], 6)

        # volume timeseries: hourly buckets for 24h, daily for 7d/all
        bucket = 3600 if window == "24h" else 86400
        series = {}
        for r in rows:
            b = int((r["started_at"] - floor) // bucket)
            series.setdefault(
                b,
                {
                    "bucket": b,
                    "answers": 0,
                    "cost": 0.0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_saved": 0.0,
                },
            )
            series[b]["answers"] += 1
            series[b]["cost"] = round(series[b]["cost"] + r["cost"], 6)
            series[b]["tokens_in"] += r["tokens_in"]
            series[b]["tokens_out"] += r["tokens_out"]
            series[b]["cost_saved"] = round(series[b]["cost_saved"] + (r["cost_saved"] or 0.0), 6)
        timeseries = [series[k] for k in sorted(series)]

        by_lang = {}
        for r in rows:
            by_lang[r["lang"] or "en"] = by_lang.get(r["lang"] or "en", 0) + 1

        # T30 — persona spread and context-resolution rate, parsed from the span
        # attrs (persona/context_resolved were stamped on the answer span).
        by_persona, context_resolved = {}, 0
        for r in rows:
            try:
                aa = json.loads(r["attrs"]) if r["attrs"] else {}
            except Exception:
                aa = {}
            persona = aa.get("persona") or "general"
            by_persona[persona] = by_persona.get(persona, 0) + 1
            context_resolved += 1 if aa.get("context_resolved") else 0

        [r for r in rows if r["level"] not in ("clarify", "gap", "")]
        return {
            "tenant": tenant,
            "window": window,
            "filters": {"subject": subject, "role": role},
            "answers": len(rows),
            "tokens_in": sum(r["tokens_in"] for r in rows),
            "tokens_out": sum(r["tokens_out"] for r in rows),
            "total_cost": round(sum(r["cost"] for r in rows), 6),
            "total_cost_saved": round(sum(r["cost_saved"] or 0.0 for r in rows), 6),
            "cache_hit_rate": round(cache_hits / len(rows), 4) if rows else 0.0,
            "latency_p50_ms": pct(0.5),
            "latency_p95_ms": pct(0.95),
            "grounding_avg": round(sum(r["grounding"] for r in rows) / n, 4),
            "citation_coverage": round(sum(1 for r in rows if r["citations_count"] > 0) / n, 4),
            "clarify_back_rate": round(sum(1 for r in rows if r["level"] == "clarify") / n, 4),
            "routing_by_level": by_level,
            "routing_reasons": reason_counts,
            "routing_by_tier": by_tier,
            "routing_by_complexity": by_complexity,
            "models_used": models_used,
            "savings_by_technique": savings,
            "per_user": per_user,
            "per_role": per_role,
            "by_language": by_lang,
            "answers_by_persona": by_persona,  # T30
            "context_resolution_rate": round(context_resolved / n, 4),  # T30 (T26 follow-ups)
            "timeseries": timeseries,
            "bucket_seconds": bucket,
        }

    def events(self, tenant: str) -> list[dict]:
        """Flat one-row-per-answer telemetry for the self-serve Explorer (T29):
        every dimension (role, persona, designation, scope, context, level, model,
        language, complexity, kind — T30 adds the middle four) beside every metric
        (tokens, cost, cost saved, latency, trust, cited), so any permutation can
        be filtered and grouped in one table."""
        out = []
        for r in self.db.query(
            "SELECT * FROM spans WHERE tenant=? AND name='answer' ORDER BY started_at", (tenant,)
        ):
            try:
                a = json.loads(r["attrs"]) if r["attrs"] else {}
            except Exception:
                a = {}
            why = a.get("why") or {}
            kind = a.get("kind") or ("gap" if r["level"] in ("gap", "clarify") else "answer")
            roles = (r["roles"] or "").split(",")
            role = (roles[0] if roles and roles[0] else "asker").split(".")[0]
            out.append(
                {
                    "subject": r["subject"] or "",
                    "role": role,
                    # T30 — persona/designation (T27), access scope, and whether a
                    # follow-up was resolved from context (T26), as first-class
                    # Explorer dimensions beside role/level/model/lang.
                    "persona": a.get("persona") or "general",
                    "designation": a.get("designation") or "—",
                    "scope": a.get("scope") or "public",
                    "context": "resolved" if a.get("context_resolved") else "direct",
                    "level": why.get("level_name") or r["level"] or "—",
                    "model": r["model_name"] or "demo model",
                    "lang": (r["lang"] or "en").upper(),
                    "complexity": r["complexity"] or "simple",
                    "kind": kind,
                    "answered": 1 if kind == "answer" else 0,
                    "declined": 0 if kind == "answer" else 1,
                    "cited": 1 if (r["citations_count"] or 0) > 0 else 0,
                    "tokens_in": int(r["tokens_in"] or 0),
                    "tokens_out": int(r["tokens_out"] or 0),
                    "cost": round(float(r["cost"] or 0.0), 6),
                    "cost_saved": round(float(r["cost_saved"] or 0.0), 6),
                    "latency_ms": round(float(r["duration_ms"] or 0.0), 2),
                    "grounding": round(float(r["grounding"] or 0.0), 4),
                }
            )
        return out
