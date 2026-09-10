"""OpenTelemetry exporter for the telemetry spine — OTLP/JSON over HTTP, stdlib only.

Why this exists
---------------
Leadership asked whether the platform needs a licensed LLM-observability
product (LangSmith — proprietary; LangFuse — MIT core, commercial cloud).
``docs/OBSERVABILITY_DECISION.md`` answers *no*, on one condition: the spans
we already record (``stores/db.py::spans``, written by
``adapters/telemetry.py``) must be consumable by the open, Apache-2.0
OpenTelemetry ecosystem. This module is that proof. It maps our span rows to
the OTLP/JSON ``resourceSpans`` shape every OTLP receiver accepts (the
OpenTelemetry Collector, Jaeger v2, SigNoz, Tempo, vendor back-ends) and
POSTs it with ``urllib`` — no SDK, no third-party package, no change to the
application code that records the spans.

What one answer looks like once exported
----------------------------------------
One trace per answer (``traceId`` derived from the answer's replayable
trajectory id). The root span ``answer`` carries the leadership fields as
attributes: tier + selector level + the explainable *why* (reason codes),
tokens in/out (GenAI semantic-convention names ``gen_ai.usage.input_tokens``
/ ``gen_ai.usage.output_tokens``), cost and cost saved by cache technique,
grounding score and its five signals, citations count, language, sources,
user + roles (``user.id`` / ``user.roles``), model name
(``gen_ai.response.model``), complexity, dataset version and the reasoning
plan. Children (``answer.retrieve``, ``answer.graph``, ``answer.ground``,
``answer.compose``) are linked by ``parentSpanId`` so a waterfall view shows
where the latency went.

Wire-format notes (OTLP/JSON = proto3 JSON mapping with OTLP deviations)
------------------------------------------------------------------------
* ``traceId`` is 32 hex chars and ``spanId`` 16 hex chars (hex, not base64).
* 64-bit integers (``startTimeUnixNano``, ``endTimeUnixNano``, ``intValue``)
  are encoded as decimal strings.
* Enum fields (span ``kind``, ``status.code``) are encoded as integers.
* Attribute values use the ``AnyValue`` one-of: ``stringValue`` /
  ``boolValue`` / ``intValue`` / ``doubleValue`` / ``arrayValue`` /
  ``kvlistValue``.

Configuration
-------------
``KF_OTLP_ENDPOINT``  base URL of an OTLP/HTTP receiver, e.g.
                      ``http://otel-collector:4318`` (``/v1/traces`` is
                      appended when missing). Unset → ``export`` is a dry run
                      that returns the payload instead of sending it.
``KF_OTLP_HEADERS``   optional ``key=value,key2=value2`` list added to the
                      request (same convention as ``OTEL_EXPORTER_OTLP_HEADERS``)
                      — e.g. an auth token for a hosted collector.

Every read is tenant-filtered (invariant I5) through the store guard, and the
output is deterministic for a given set of rows (ids are derived, never random),
so exports are idempotent and testable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from ..stores.repositories import _guard

ENDPOINT_ENV = "KF_OTLP_ENDPOINT"
HEADERS_ENV = "KF_OTLP_HEADERS"
TRACES_PATH = "/v1/traces"

SCOPE_NAME = "knowledge_fabric.telemetry"
SCOPE_VERSION = "1.0.0"
SERVICE_NAME = "knowledge-fabric"
SERVICE_NAMESPACE = "qualizeal"

# OTLP enums (opentelemetry/proto/trace/v1/trace.proto)
SPAN_KIND_INTERNAL = 1
STATUS_CODE_UNSET = 0
STATUS_CODE_OK = 1
STATUS_CODE_ERROR = 2

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_NO_MODEL = "none (extractive core)"

# (span-table column, OTLP attribute key, value type). Named with OpenTelemetry
# semantic conventions where one exists (gen_ai.*, user.*) and ``kf.*`` otherwise.
_COLUMN_ATTRS: tuple[tuple[str, str, str], ...] = (
    ("tenant", "kf.tenant", "str"),
    ("stage", "kf.stage", "str"),
    ("subject", "user.id", "str"),
    ("level", "kf.selector.level", "str"),
    ("tier", "kf.model.tier", "str"),
    ("model_name", "gen_ai.response.model", "str"),
    ("complexity", "kf.complexity", "str"),
    ("tokens_in", "gen_ai.usage.input_tokens", "int"),
    ("tokens_out", "gen_ai.usage.output_tokens", "int"),
    ("tokens", "kf.tokens.total", "int"),
    ("cost", "kf.cost.usd", "float"),
    ("cost_saved", "kf.cost.saved_usd", "float"),
    ("cache_hit", "kf.cache.hit", "bool"),
    ("cache_technique", "kf.cache.technique", "str"),
    ("grounding", "kf.grounding.score", "float"),
    ("citations_count", "kf.citations.count", "int"),
    ("lang", "kf.lang", "str"),
    ("dataset_version", "kf.dataset.version", "int"),
)
# attrs-json keys that are either mapped explicitly or are plumbing, never echoed raw
_KNOWN_ATTR_KEYS = {c for c, _, _ in _COLUMN_ATTRS} | {
    "roles",
    "why",
    "sources",
    "reasoning",
    "kind",
    "signals",
    "trajectory",
    "trace_id",
    "duration_ms",
    "error",
}


class OtlpExportError(RuntimeError):
    """A configured OTLP endpoint rejected, or could not receive, the payload."""


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------
def _load_json(value):
    """Span rows hold JSON text; tolerate already-decoded values and garbage."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _any_value(v) -> dict:
    """Encode a Python value as an OTLP ``AnyValue`` (proto3 JSON mapping)."""
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_any_value(x) for x in v]}}
    if isinstance(v, dict):
        return {
            "kvlistValue": {
                "values": [{"key": str(k), "value": _any_value(x)} for k, x in v.items()]
            }
        }
    return {"stringValue": str(v)}


def _kv(key: str, value) -> dict:
    return {"key": key, "value": _any_value(value)}


def _coerce(value, typ: str):
    """Coerce a stored column to the attribute type; ``None`` means "omit"."""
    if value is None:
        return None
    try:
        if typ == "int":
            return int(value)
        if typ == "float":
            return float(value)
        if typ == "bool":
            return value if isinstance(value, bool) else bool(int(value))
    except (TypeError, ValueError):
        return None
    s = str(value)
    return s if s else None


def trace_id_hex(trace_id: str) -> str:
    """OTLP trace id (16 bytes as 32 hex chars) for one of our trace ids.

    Our ids are ``traj_<uuid4 hex>`` / ``trace_<uuid4 hex>``; the hex tail is
    already a valid 128-bit id and is kept verbatim so the OTLP trace can be
    joined back to ``/api/trace?trace_id=``. Anything else is hashed
    deterministically to 16 bytes.
    """
    tail = (trace_id or "").rsplit("_", 1)[-1].lower()
    if _HEX32.match(tail):
        return tail
    return hashlib.blake2b((trace_id or "").encode(), digest_size=16).hexdigest()


def span_id_hex(trace_id: str, row: dict) -> str:
    """Deterministic 8-byte span id from the row's identity (never random)."""
    seed = f"{trace_id}|{row.get('id')}|{row.get('name')}|{row.get('started_at')}"
    return hashlib.blake2b(seed.encode(), digest_size=8).hexdigest()


def traces_url(endpoint: str) -> str:
    """``http://host:4318`` → ``http://host:4318/v1/traces`` (idempotent)."""
    e = (endpoint or "").strip().rstrip("/")
    return e if e.endswith(TRACES_PATH) else e + TRACES_PATH


def _env_headers() -> dict:
    out = {}
    for part in (os.environ.get(HEADERS_ENV, "") or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


# --------------------------------------------------------------------------
# span → OTLP span
# --------------------------------------------------------------------------
def span_attributes(row: dict) -> list[dict]:
    """The attribute list for one span row (deterministic order).

    Root spans (no ``.`` in the name, e.g. ``answer``/``ingest``) carry every
    counter even when zero — an extractive answer with 0 tokens and $0 cost is
    a fact worth exporting. Child stage spans omit zero counters, which are
    just column defaults there.
    """
    root = "." not in (row.get("name") or "")
    out: list[dict] = []
    for column, key, typ in _COLUMN_ATTRS:
        v = _coerce(row.get(column), typ)
        if v is None:
            continue
        if typ in ("int", "float", "bool") and not root and not v:
            continue
        if key == "gen_ai.response.model" and v == _NO_MODEL:
            continue
        out.append(_kv(key, v))

    roles_raw = row.get("roles")
    roles = (
        [r for r in roles_raw.split(",") if r]
        if isinstance(roles_raw, str)
        else [str(r) for r in (roles_raw or [])]
    )
    if roles:
        out.append(_kv("user.roles", roles))

    why = _load_json(row.get("why"))
    if isinstance(why, dict):
        if why.get("level_name"):
            out.append(_kv("kf.selector.level_name", str(why["level_name"])))
        if why.get("explain"):
            out.append(_kv("kf.selector.why.explain", str(why["explain"])))
        codes = [
            str(r.get("code"))
            for r in (why.get("reasons") or [])
            if isinstance(r, dict) and r.get("code")
        ]
        if codes:
            out.append(_kv("kf.selector.why.reasons", codes))
        out.append(_kv("kf.selector.why", json.dumps(why, sort_keys=True, default=str)))

    sources = _load_json(row.get("sources"))
    if isinstance(sources, list) and sources:
        out.append(_kv("kf.sources", [str(s) for s in sources]))

    reasoning = _load_json(row.get("reasoning"))
    if isinstance(reasoning, dict):
        if reasoning.get("mode"):
            out.append(_kv("kf.reasoning.mode", str(reasoning["mode"])))
        steps = reasoning.get("steps")
        if isinstance(steps, list):
            out.append(_kv("kf.reasoning.steps", len(steps)))
        out.append(_kv("kf.reasoning", json.dumps(reasoning, sort_keys=True, default=str)))

    attrs = _load_json(row.get("attrs"))
    if isinstance(attrs, dict):
        if attrs.get("kind"):
            out.append(_kv("kf.answer.kind", str(attrs["kind"])))
        signals = attrs.get("signals")
        if isinstance(signals, dict):
            for k in sorted(signals):
                v = _coerce(signals[k], "float")
                if v is not None:
                    out.append(_kv(f"kf.grounding.signal.{k}", v))
        traj = attrs.get("trajectory")
        if isinstance(traj, dict):
            sel = traj.get("selected")
            if isinstance(sel, list) and sel:
                out.append(_kv("kf.trajectory.selected", [str(x) for x in sel]))
            keys = traj.get("graph_node_keys")
            if isinstance(keys, list) and keys:
                out.append(_kv("kf.trajectory.graph_node_keys", [str(x) for x in keys]))
        for k in sorted(attrs):
            if k in _KNOWN_ATTR_KEYS:
                continue
            v = attrs[k]
            if isinstance(v, (str, int, float, bool)) and v != "":
                out.append(_kv(f"kf.attr.{k}", v))

    out.append(_kv("kf.trace_id", str(row.get("trace_id") or "")))
    if row.get("id") is not None:
        out.append(_kv("kf.span.row_id", int(row["id"])))
    return out


def _status(attrs) -> dict:
    err = attrs.get("error") if isinstance(attrs, dict) else None
    if err:
        return {"code": STATUS_CODE_ERROR, "message": str(err)}
    return {"code": STATUS_CODE_OK}


def _times(row: dict) -> tuple[str, str]:
    start = float(row.get("started_at") or 0.0)
    dur_ms = max(0.0, float(row.get("duration_ms") or 0.0))
    start_ns = int(round(start * 1e9))
    return str(start_ns), str(start_ns + int(round(dur_ms * 1e6)))


def _enclosing(rows: list[dict], candidates: list[int], child: dict) -> int:
    """Among same-named candidates pick the one whose window contains the child."""
    cs = float(child.get("started_at") or 0.0)
    for c in candidates:
        s = float(rows[c].get("started_at") or 0.0)
        e = s + float(rows[c].get("duration_ms") or 0.0) / 1000.0
        if s - 1e-3 <= cs <= e + 1e-3:
            return c
    return candidates[0]


def _parents(rows: list[dict], ids: dict[int, str]) -> dict[int, str | None]:
    """Parent span id per row of ONE trace.

    The telemetry adapter nests stage spans by dotted name (``answer`` →
    ``answer.retrieve``), so the parent of ``a.b.c`` is the longest existing
    prefix (``a.b``, else ``a``). A dotted span with no named prefix falls
    back to the trace's single root, if there is exactly one.
    """
    by_name: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_name.setdefault(r.get("name") or "", []).append(i)
    roots = [i for i, r in enumerate(rows) if "." not in (r.get("name") or "")]
    out: dict[int, str | None] = {}
    for i, r in enumerate(rows):
        name = r.get("name") or ""
        parts = name.split(".")
        parent = None
        for cut in range(len(parts) - 1, 0, -1):
            cands = by_name.get(".".join(parts[:cut]))
            if cands:
                parent = _enclosing(rows, cands, r)
                break
        if parent is None and "." in name and len(roots) == 1 and roots[0] != i:
            parent = roots[0]
        out[i] = ids[parent] if parent is not None else None
    return out


def _resource_attributes(tenant: str) -> list[dict]:
    attrs = [
        _kv("service.name", SERVICE_NAME),
        _kv("service.namespace", SERVICE_NAMESPACE),
        _kv("service.version", SCOPE_VERSION),
        _kv("kf.exporter", "knowledge_fabric.adapters.otel_export"),
    ]
    if tenant:
        attrs.append(_kv("kf.tenant", tenant))
    return attrs


def to_otlp(spans: list[dict]) -> dict:
    """Map span rows (``dict`` per ``spans`` table row) to OTLP/JSON.

    Returns ``{"resourceSpans": [...]}`` — one ``resourceSpans`` entry per
    tenant present in ``spans`` (tenant is a resource attribute, so a
    multi-tenant batch never mixes tenants inside one resource), each with one
    ``scopeSpans`` for this exporter. Rows are ordered by trace, start time and
    row id so parents precede children; ids are derived deterministically.
    """
    rows = [dict(r) for r in (spans or [])]
    by_tenant: dict[str, list[dict]] = {}
    for r in rows:
        by_tenant.setdefault(str(r.get("tenant") or ""), []).append(r)

    resource_spans = []
    for tenant in sorted(by_tenant):
        trows = sorted(
            by_tenant[tenant],
            key=lambda r: (
                str(r.get("trace_id") or ""),
                float(r.get("started_at") or 0.0),
                int(r.get("id") or 0),
            ),
        )
        by_trace: dict[str, list[dict]] = {}
        for r in trows:
            by_trace.setdefault(str(r.get("trace_id") or ""), []).append(r)

        otlp_spans = []
        for trace_id in sorted(by_trace):
            group = by_trace[trace_id]
            thex = trace_id_hex(trace_id)
            ids = {i: span_id_hex(trace_id, r) for i, r in enumerate(group)}
            parents = _parents(group, ids)
            for i, r in enumerate(group):
                start, end = _times(r)
                span = {"traceId": thex, "spanId": ids[i]}
                if parents[i]:
                    span["parentSpanId"] = parents[i]
                span.update(
                    {
                        "name": str(r.get("name") or "span"),
                        "kind": SPAN_KIND_INTERNAL,
                        "startTimeUnixNano": start,
                        "endTimeUnixNano": end,
                        "attributes": span_attributes(r),
                        "status": _status(_load_json(r.get("attrs"))),
                    }
                )
                otlp_spans.append(span)

        resource_spans.append(
            {
                "resource": {"attributes": _resource_attributes(tenant)},
                "scopeSpans": [
                    {"scope": {"name": SCOPE_NAME, "version": SCOPE_VERSION}, "spans": otlp_spans}
                ],
            }
        )
    return {"resourceSpans": resource_spans}


# --------------------------------------------------------------------------
# store read + export
# --------------------------------------------------------------------------
def read_spans(
    platform,
    tenant: str,
    *,
    since: float | None = None,
    limit: int = 5000,
    trace_id: str | None = None,
) -> list[dict]:
    """Tenant-filtered span rows (oldest first). ``since`` is epoch seconds."""
    _guard(tenant)
    where, params = ["tenant=?"], [tenant]
    if since is not None:
        where.append("started_at>=?")
        params.append(float(since))
    if trace_id:
        where.append("trace_id=?")
        params.append(trace_id)
    sql = f"SELECT * FROM spans WHERE {' AND '.join(where)} ORDER BY id"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [dict(r) for r in platform.db.query(sql, tuple(params))]


def export(
    platform,
    tenant: str,
    endpoint_url: str | None = None,
    *,
    since: float | None = None,
    limit: int = 5000,
    timeout_s: float = 5.0,
    headers: dict | None = None,
    trace_id: str | None = None,
) -> dict:
    """Export a tenant's spans as OTLP/JSON.

    ``endpoint_url`` overrides ``KF_OTLP_ENDPOINT``; pass ``""`` to force a dry
    run even when the env var is set.

    * No endpoint configured → returns the OTLP payload (``{"resourceSpans":
      [...]}``) without any network I/O — useful for tests, ``/admin/otlp``
      previews and hand-off to a file-based collector.
    * Endpoint configured → POSTs the payload to ``<endpoint>/v1/traces`` with
      ``urllib`` and returns ``{"ok", "endpoint", "http_status", "tenant",
      "spans", "traces", "bytes"}``. Network or HTTP failures raise
      ``OtlpExportError`` so a scheduler can retry rather than silently drop.
    """
    _guard(tenant)
    rows = read_spans(platform, tenant, since=since, limit=limit, trace_id=trace_id)
    payload = to_otlp(rows)
    endpoint = endpoint_url if endpoint_url is not None else os.environ.get(ENDPOINT_ENV, "")
    if not endpoint:
        return payload

    url = traces_url(endpoint)
    body = json.dumps(payload, separators=(",", ":")).encode()
    hdrs = {"Content-Type": "application/json", "User-Agent": f"{SCOPE_NAME}/{SCOPE_VERSION}"}
    hdrs.update(_env_headers())
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = int(resp.status)
            resp.read()
    except urllib.error.HTTPError as e:
        raise OtlpExportError(f"OTLP endpoint {url} returned HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OtlpExportError(f"OTLP endpoint {url} unreachable: {e}") from e
    return {
        "ok": 200 <= status < 300,
        "endpoint": url,
        "http_status": status,
        "tenant": tenant,
        "spans": len(rows),
        "traces": len({r.get("trace_id") for r in rows}),
        "bytes": len(body),
    }


def export_trace(platform, tenant: str, trace_id: str, endpoint_url: str | None = None) -> dict:
    """Export exactly one answer's trace (its trajectory id) — for replay/debug."""
    return export(platform, tenant, endpoint_url, trace_id=trace_id, limit=0)
