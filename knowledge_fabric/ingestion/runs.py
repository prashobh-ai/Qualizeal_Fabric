"""Pipeline-run progress records (Stage-2 Section C).

A *run* is one unit of ingestion work the admin surface can watch live: a
connector sync triggered by the refresh scheduler or by "Sync now", or a bulk
upload. Each run is a row in ``ingest_runs`` carrying an ordered list of
*steps* (``{name, status, count, ms, at[, detail]}``) appended as the work
progresses, and a final status + item count.

Two layers append steps:

* the caller that owns the run (``start_run`` / ``step`` / ``finish``), e.g.
  the scheduler records ``sync``, ``tombstone`` and ``ingest``;
* the 7-step ingestion pipeline, through ``active(run_id)`` +
  ``step_if_active`` / ``step_from_span``: while a run is bound to the current
  thread, every pipeline stage span (``ingest.detect`` … ``ingest.health``)
  can be mirrored into the run without the pipeline knowing about run ids.

Because a sync ingests many documents, the raw ``steps`` list holds one entry
per (document, stage). ``rollup`` folds them into one entry per stage name
(summed counts/ms, worst status) — that is the "7 steps with status" view the
admin page shows; ``list_runs`` returns both as ``steps`` and ``stages``.

Tenant isolation (I5): ``start_run`` and ``list_runs`` take the tenant
explicitly. ``step``/``finish`` are addressed by ``run_id`` per the contract;
the tenant is resolved from a process-local registry populated by
``start_run`` (a run is always started and finished by the same process),
and every UPDATE is ``WHERE tenant=? AND id=?``. Pass ``tenant=`` to address
a run started elsewhere. No query in this module runs without a tenant.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..contracts.types import new_id, now_ms
from ..stores.repositories import _guard

__all__ = [
    "STEP_STATUSES",
    "RUN_STATUSES",
    "RUNNING",
    "PIPELINE_STAGES",
    "start_run",
    "step",
    "finish",
    "list_runs",
    "get_run",
    "active_runs",
    "rollup",
    "active",
    "current_run_id",
    "step_if_active",
    "step_from_span",
]

log = logging.getLogger("knowledge_fabric.runs")

#: statuses a single step may carry
STEP_STATUSES = ("ok", "error", "skipped")
#: terminal statuses of a run (``finish``)
RUN_STATUSES = ("ok", "error")
#: status of a run between ``start_run`` and ``finish``
RUNNING = "running"
#: the seven pipeline stages, in order (mirrors ``ingestion/pipeline.py``)
PIPELINE_STAGES = ("detect", "convert", "chunk", "extract", "graph", "embed", "health")

_MAX_DETAIL = 500
_SPAN_PREFIX = "ingest."
#: worst-status ordering used by ``rollup``
_SEVERITY = {"error": 2, "ok": 1, "skipped": 0}

# process-local registry run_id -> tenant (see module docstring)
_tenants: dict[str, str] = {}
_tenants_lock = threading.Lock()
# serialises read-modify-write of the steps json inside this process
_steps_lock = threading.Lock()
# thread-local binding of the "current" run for step_if_active
_active = threading.local()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _db(platform):
    """Accept a ``Platform``, a ``Database`` or any holder exposing ``.db``.

    Lets the telemetry adapter (which has ``.db`` but no platform) mirror
    pipeline spans into the active run.
    """
    return getattr(platform, "db", platform)


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return val if isinstance(val, type(default)) else default


def _tenant_of(run_id: str, tenant: str | None) -> str:
    if tenant:
        return _guard(tenant)
    if not run_id or not isinstance(run_id, str):
        raise KeyError("run_id is required")
    with _tenants_lock:
        found = _tenants.get(run_id)
    if not found:
        raise KeyError(f"unknown run '{run_id}' in this process; pass tenant= to address it")
    return found


def _check_source(source: str) -> str:
    if not source or not isinstance(source, str):
        raise ValueError("source must be a non-empty string")
    return source


def _row(r) -> dict:
    steps = _loads(r["steps"], [])
    started, finished = r["started_at"], r["finished_at"]
    return {
        "id": r["id"],
        "source": r["source"],
        "started_at": started,
        "finished_at": finished,
        "status": r["status"],
        "steps": steps,
        "items": int(r["items"] or 0),
        "stages": rollup(steps),
        "duration_ms": (finished - started)
        if (started is not None and finished is not None)
        else None,
    }


# --------------------------------------------------------------------------
# contract: start_run / step / finish / list_runs
# --------------------------------------------------------------------------
def start_run(platform, tenant: str, source: str) -> str:
    """Open a run for ``source`` and return its id (status ``running``)."""
    _guard(tenant)
    _check_source(source)
    run_id = new_id("run_")
    _db(platform).execute(
        """INSERT INTO ingest_runs(id,tenant,source,started_at,finished_at,status,steps,items)
           VALUES(?,?,?,?,?,?,?,?)""",
        (run_id, tenant, source, now_ms(), None, RUNNING, "[]", 0),
    )
    with _tenants_lock:
        _tenants[run_id] = tenant
    return run_id


def step(
    platform,
    run_id: str,
    name: str,
    status: str,
    count: int = 0,
    ms: float = 0.0,
    detail: str | None = None,
    tenant: str | None = None,
) -> None:
    """Append one step ``{name, status, count, ms, at[, detail]}`` to a running run.

    ``status`` is one of ``ok`` / ``error`` / ``skipped``. Raises ``KeyError``
    for an unknown run and ``ValueError`` for a finished one or a bad status.
    """
    if status not in STEP_STATUSES:
        raise ValueError(f"step status must be one of {STEP_STATUSES}, got {status!r}")
    if not name or not isinstance(name, str):
        raise ValueError("step name must be a non-empty string")
    t = _tenant_of(run_id, tenant)
    db = _db(platform)
    entry: dict[str, Any] = {
        "name": name,
        "status": status,
        "count": int(count),
        "ms": round(float(ms), 2),
        "at": now_ms(),
    }
    if detail:
        entry["detail"] = str(detail)[:_MAX_DETAIL]
    with _steps_lock:
        row = db.one("SELECT status, steps FROM ingest_runs WHERE tenant=? AND id=?", (t, run_id))
        if row is None:
            raise KeyError(f"unknown run '{run_id}' for tenant '{t}'")
        if row["status"] != RUNNING:
            raise ValueError(f"run '{run_id}' already finished with status {row['status']!r}")
        steps = _loads(row["steps"], [])
        steps.append(entry)
        db.execute(
            "UPDATE ingest_runs SET steps=? WHERE tenant=? AND id=?", (json.dumps(steps), t, run_id)
        )


def finish(platform, run_id: str, status: str, items: int, tenant: str | None = None) -> None:
    """Close a run with a terminal ``status`` (``ok``/``error``) and its item count."""
    if status not in RUN_STATUSES:
        raise ValueError(f"run status must be one of {RUN_STATUSES}, got {status!r}")
    t = _tenant_of(run_id, tenant)
    db = _db(platform)
    with _steps_lock:
        row = db.one("SELECT status FROM ingest_runs WHERE tenant=? AND id=?", (t, run_id))
        if row is None:
            raise KeyError(f"unknown run '{run_id}' for tenant '{t}'")
        if row["status"] != RUNNING:
            raise ValueError(f"run '{run_id}' already finished with status {row['status']!r}")
        db.execute(
            "UPDATE ingest_runs SET finished_at=?, status=?, items=? WHERE tenant=? AND id=?",
            (now_ms(), status, int(items), t, run_id),
        )
    with _tenants_lock:
        _tenants.pop(run_id, None)
    if getattr(_active, "run_id", None) == run_id:
        _active.run_id = None


def list_runs(platform, tenant: str, limit: int = 20) -> list[dict]:
    """Most recent runs first: ``{id, source, started_at, finished_at, status, steps, items,
    stages, duration_ms}``."""
    _guard(tenant)
    limit = max(1, int(limit))
    rows = _db(platform).query(
        "SELECT * FROM ingest_runs WHERE tenant=? ORDER BY started_at DESC, rowid DESC LIMIT ?",
        (tenant, limit),
    )
    return [_row(r) for r in rows]


def get_run(platform, tenant: str, run_id: str) -> dict | None:
    """One run by id (tenant-filtered), or ``None``."""
    _guard(tenant)
    r = _db(platform).one("SELECT * FROM ingest_runs WHERE tenant=? AND id=?", (tenant, run_id))
    return _row(r) if r else None


def active_runs(platform, tenant: str) -> list[dict]:
    """Runs still in progress — the admin page polls while this is non-empty."""
    _guard(tenant)
    rows = _db(platform).query(
        (
            "SELECT * FROM ingest_runs WHERE tenant=? AND status=? ORDER BY started_at DESC, rowid "
            "DESC"
        ),
        (tenant, RUNNING),
    )
    return [_row(r) for r in rows]


# --------------------------------------------------------------------------
# rollup: one entry per stage name
# --------------------------------------------------------------------------
def rollup(steps: list[dict]) -> list[dict]:
    """Fold raw steps into one entry per name, in first-seen order.

    ``count`` and ``ms`` are summed, ``entries`` counts the folded steps and
    ``status`` is the worst seen (``error`` > ``ok`` > ``skipped``).
    """
    out: dict[str, dict] = {}
    for s in steps or []:
        name = s.get("name", "?")
        cur = out.get(name)
        if cur is None:
            cur = out[name] = {
                "name": name,
                "status": "skipped",
                "count": 0,
                "ms": 0.0,
                "entries": 0,
                "last_at": None,
            }
        cur["count"] += int(s.get("count", 0) or 0)
        cur["ms"] = round(cur["ms"] + float(s.get("ms", 0.0) or 0.0), 2)
        cur["entries"] += 1
        cur["last_at"] = s.get("at", cur["last_at"])
        st = s.get("status", "ok")
        if _SEVERITY.get(st, 1) > _SEVERITY.get(cur["status"], 0):
            cur["status"] = st
        if "detail" in s:
            cur["detail"] = s["detail"]
    return list(out.values())


# --------------------------------------------------------------------------
# thread-bound active run (pipeline instrumentation without run ids)
# --------------------------------------------------------------------------
@contextmanager
def active(run_id: str) -> Iterator[str]:
    """Bind ``run_id`` as the current thread's active run for the block."""
    prev = getattr(_active, "run_id", None)
    _active.run_id = run_id
    try:
        yield run_id
    finally:
        _active.run_id = prev


def current_run_id() -> str | None:
    """The run bound to this thread by ``active`` (``None`` outside one)."""
    return getattr(_active, "run_id", None)


def step_if_active(
    platform,
    name: str,
    status: str = "ok",
    count: int = 0,
    ms: float = 0.0,
    detail: str | None = None,
) -> bool:
    """Append a step to the thread's active run; no-op (``False``) when none is bound.

    Never raises — it is called from inside pipeline spans, where a bookkeeping
    failure must not abort ingestion.
    """
    run_id = current_run_id()
    if not run_id:
        return False
    try:
        step(platform, run_id, name, status, count=count, ms=ms, detail=detail)
        return True
    except Exception as e:  # pragma: no cover - defensive: never break the pipeline
        log.debug("step_if_active(%s) ignored: %s", name, e)
        return False


def step_from_span(platform, span_name: str, attrs: dict, duration_ms: float) -> bool:
    """Mirror a pipeline stage span (``ingest.<stage>``) into the active run.

    Hook point for ``adapters/telemetry.py``: stage name = span suffix,
    status ``error`` if the span attrs say so, ``skipped`` for a ``noop``,
    else ``ok``; ``count`` = 1 document. Non-pipeline spans are ignored.
    """
    if not isinstance(span_name, str) or not span_name.startswith(_SPAN_PREFIX):
        return False
    stage = span_name[len(_SPAN_PREFIX) :]
    if not stage:
        return False
    raw = (attrs or {}).get("status", "ok")
    status = "error" if raw == "error" else ("skipped" if raw == "noop" else "ok")
    return step_if_active(platform, stage, status, count=1, ms=duration_ms)
