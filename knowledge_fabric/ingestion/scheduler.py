"""Continuous refresh scheduler (Stage-2 Section C).

Keeps every connector's knowledge fresh without anyone pressing "sync":

* ``set_schedule`` registers ``source`` to be synced every ``interval_s``
  seconds (``refresh_schedules``). A new schedule is due immediately so the
  first backfill happens on the next tick.
* ``due`` lists the enabled schedules whose ``next_run`` has passed.
* ``run_due`` executes them: each sync runs inside a pipeline run
  (``runs.start_run`` … ``runs.finish``) so the admin page can watch it,
  goes through ``SyncManager.sync`` (delta pull → tombstones → 7-step
  pipeline → cursor), honours the connector admin (a disabled connector is
  skipped, its allow-list is applied) and on failure backs off:
  ``error_count += 1``, ``last_status = "error:<msg>"`` and
  ``next_run = now + interval × min(4, 1 + error_count)``. A success resets
  ``error_count`` and stamps ``last_run``.
* ``health`` gives the per-connector card: freshness in minutes since the
  last successful sync (scheduler *or* manual), the SLA verdict
  (``sla_breach`` when freshness exceeds ``2 × interval``, or when an
  enabled schedule has never succeeded and is erroring), error count, next
  run and cumulative items.
* ``RefreshLoop`` is the daemon thread that calls ``run_due`` every
  ``tick_s`` seconds. ``sync_now`` is the "Sync now" button — the same
  wrapped path, regardless of due-ness or of a schedule existing.

All timestamps in this module are epoch **seconds** (floats); ``now`` is
injectable everywhere for deterministic tests. Every store query is
tenant-filtered (I5).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from ..connectors import admin
from ..stores.repositories import _guard
from . import runs
from .sync import SyncManager

__all__ = [
    "MIN_INTERVAL_S", "MAX_BACKOFF_FACTOR", "SLA_FACTOR", "DEFAULT_ONTOLOGY",
    "set_schedule", "get_schedule", "schedules", "remove_schedule", "enable_schedule",
    "due", "run_due", "sync_now", "health", "health_for", "RefreshLoop",
]

log = logging.getLogger("knowledge_fabric.refresh")

#: shortest accepted refresh interval
MIN_INTERVAL_S = 1
#: back-off multiplier cap: next_run = now + interval × min(cap, 1 + error_count)
MAX_BACKOFF_FACTOR = 4
#: a source breaches its SLA when freshness exceeds SLA_FACTOR × interval
SLA_FACTOR = 2.0
DEFAULT_ONTOLOGY = "quality-assurance"

_SKIPPED_DISABLED = "skipped:connector disabled"
_MAX_STATUS = 200

# one lock per tenant so a loop tick and a "Sync now" never run the same
# tenant's schedules concurrently (the second caller simply finds nothing due)
_tenant_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _lock_for(tenant: str) -> threading.Lock:
    with _locks_guard:
        lock = _tenant_locks.get(tenant)
        if lock is None:
            lock = _tenant_locks[tenant] = threading.Lock()
        return lock


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _check_source(source: str) -> str:
    if not source or not isinstance(source, str):
        raise ValueError("source must be a non-empty string")
    return source


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return val if isinstance(val, type(default)) else default


def _sched_row(r) -> dict:
    return {"source": r["source"], "interval_s": int(r["interval_s"]),
            "next_run": r["next_run"], "last_run": r["last_run"],
            "last_status": r["last_status"], "error_count": int(r["error_count"] or 0),
            "enabled": bool(r["enabled"]), "config": _loads(r["config"], {})}


# --------------------------------------------------------------------------
# schedules
# --------------------------------------------------------------------------
def set_schedule(platform, tenant: str, source: str, interval_s: int,
                 config: Optional[dict] = None, enabled: bool = True,
                 now: Optional[float] = None) -> dict:
    """Create or update the refresh schedule of ``source``.

    A new schedule is due at ``now``; an existing one is re-armed at
    ``last_run + interval_s`` (which clears any error back-off). ``last_run``,
    ``last_status`` and ``error_count`` survive an update.
    """
    _guard(tenant)
    _check_source(source)
    interval = int(interval_s)
    if interval < MIN_INTERVAL_S:
        raise ValueError(f"interval_s must be >= {MIN_INTERVAL_S}, got {interval_s!r}")
    config = {} if config is None else config
    if not isinstance(config, dict):
        raise TypeError("config must be a dict")
    now = _now(now)
    existing = platform.db.one(
        "SELECT last_run FROM refresh_schedules WHERE tenant=? AND source=?", (tenant, source))
    next_run = (existing["last_run"] + interval
                if existing is not None and existing["last_run"] is not None else now)
    platform.db.execute(
        """INSERT INTO refresh_schedules(tenant,source,interval_s,next_run,last_run,last_status,
           error_count,enabled,config) VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(tenant,source) DO UPDATE SET interval_s=excluded.interval_s,
           next_run=excluded.next_run, enabled=excluded.enabled, config=excluded.config""",
        (tenant, source, interval, next_run, None, None, 0, 1 if enabled else 0,
         json.dumps(config, default=str)))
    return get_schedule(platform, tenant, source)


def get_schedule(platform, tenant: str, source: str) -> Optional[dict]:
    _guard(tenant)
    _check_source(source)
    r = platform.db.one("SELECT * FROM refresh_schedules WHERE tenant=? AND source=?",
                        (tenant, source))
    return _sched_row(r) if r else None


def schedules(platform, tenant: str) -> list[dict]:
    """All schedules of the tenant, sorted by source, each with the admin's
    ``connector_enabled`` verdict alongside the schedule's own ``enabled``."""
    _guard(tenant)
    out = []
    for r in platform.db.query(
            "SELECT * FROM refresh_schedules WHERE tenant=? ORDER BY source", (tenant,)):
        d = _sched_row(r)
        d["connector_enabled"] = admin.is_enabled(platform, tenant, d["source"])
        out.append(d)
    return out


def remove_schedule(platform, tenant: str, source: str) -> bool:
    _guard(tenant)
    _check_source(source)
    cur = platform.db.execute("DELETE FROM refresh_schedules WHERE tenant=? AND source=?",
                              (tenant, source))
    return bool(cur.rowcount)


def enable_schedule(platform, tenant: str, source: str, enabled: bool) -> Optional[dict]:
    """Pause/resume a schedule without touching its interval or history."""
    _guard(tenant)
    _check_source(source)
    platform.db.execute("UPDATE refresh_schedules SET enabled=? WHERE tenant=? AND source=?",
                        (1 if enabled else 0, tenant, source))
    return get_schedule(platform, tenant, source)


def due(platform, tenant: str, now: Optional[float] = None) -> list[str]:
    """Sources whose enabled schedule has ``next_run <= now`` (earliest first)."""
    _guard(tenant)
    now = _now(now)
    return [r["source"] for r in platform.db.query(
        """SELECT source FROM refresh_schedules WHERE tenant=? AND enabled=1 AND next_run<=?
           ORDER BY next_run, source""", (tenant, now))]


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------
def _update_schedule(platform, tenant: str, source: str, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    platform.db.execute(f"UPDATE refresh_schedules SET {cols} WHERE tenant=? AND source=?",
                        (*fields.values(), tenant, source))


def _execute(platform, tenant: str, source: str, now: float, config: dict,
             records: Optional[list], has_records: bool, sched: Optional[dict]) -> dict:
    """Run one wrapped sync for ``source`` and update its schedule (if any)."""
    interval = sched["interval_s"] if sched else None
    base = {"source": source, "run_id": None, "pulled": 0, "ingested": 0, "noops": 0,
            "tombstoned": 0, "next_run": None, "error_count": sched["error_count"] if sched else 0,
            "duration_ms": 0.0}

    if not admin.is_enabled(platform, tenant, source):
        if sched:
            base["next_run"] = now + interval
            _update_schedule(platform, tenant, source, next_run=base["next_run"],
                             last_status=_SKIPPED_DISABLED)
        base.update(status="skipped", reason="connector disabled")
        return base

    cfg = admin.effective_config(platform, tenant, source, config)
    ontology = cfg.pop("ontology", DEFAULT_ONTOLOGY)
    kw = {"records": records} if has_records else {}
    run_id = runs.start_run(platform, tenant, source)
    base["run_id"] = run_id
    t0 = time.perf_counter()
    try:
        with runs.active(run_id):
            summary = SyncManager(platform).sync(tenant, source, cfg, ontology, **kw)
        ms = (time.perf_counter() - t0) * 1000
        runs.step(platform, run_id, "sync", "ok", count=summary["pulled"], ms=ms,
                  detail=f"cursor={summary.get('cursor')}")
        runs.step(platform, run_id, "tombstone", "ok" if summary["tombstoned"] else "skipped",
                  count=summary["tombstoned"])
        runs.step(platform, run_id, "ingest", "ok", count=summary["ingested"],
                  detail=f"noops={summary['noops']}")
        runs.finish(platform, run_id, "ok", items=summary["ingested"])
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        msg = f"{type(e).__name__}: {e}"[:_MAX_STATUS]
        try:
            runs.step(platform, run_id, "sync", "error", ms=ms, detail=msg)
            runs.finish(platform, run_id, "error", items=0)
        except Exception:  # pragma: no cover - bookkeeping must not mask the sync error
            log.exception("could not close run %s", run_id)
        error_count = base["error_count"] + 1
        factor = min(MAX_BACKOFF_FACTOR, 1 + error_count)
        base.update(status="error", error=msg, error_count=error_count,
                    backoff_factor=factor, duration_ms=round(ms, 2))
        if sched:
            base["next_run"] = now + interval * factor
            _update_schedule(platform, tenant, source, next_run=base["next_run"],
                             last_status=("error:" + msg)[:_MAX_STATUS], error_count=error_count)
        log.warning("refresh %s/%s failed (%d): %s", tenant, source, error_count, msg)
        return base

    base.update(status="ok", pulled=summary["pulled"], ingested=summary["ingested"],
                noops=summary["noops"], tombstoned=summary["tombstoned"],
                cursor=summary.get("cursor"), error_count=0, duration_ms=round(ms, 2))
    if sched:
        base["next_run"] = now + interval
        _update_schedule(platform, tenant, source, last_run=now, next_run=base["next_run"],
                         last_status="ok", error_count=0)
    return base


def run_due(platform, tenant: str, now: Optional[float] = None,
            records_by_source: Optional[dict] = None) -> list[dict]:
    """Sync every due, enabled schedule; one summary dict per source.

    ``records_by_source`` injects offline records (``{source: [record...]}``)
    into connectors that accept ``records=`` (Jira, GitHub) for demos/tests.
    Summary: ``{source, status: ok|error|skipped, run_id, pulled, ingested,
    noops, tombstoned, next_run, error_count, duration_ms[, error, reason]}``.
    """
    _guard(tenant)
    now = _now(now)
    records_by_source = records_by_source or {}
    out = []
    with _lock_for(tenant):
        for source in due(platform, tenant, now):
            sched = get_schedule(platform, tenant, source)
            if sched is None:      # removed between due() and here
                continue
            out.append(_execute(platform, tenant, source, now, sched["config"],
                                records_by_source.get(source), source in records_by_source,
                                sched))
    return out


def sync_now(platform, tenant: str, source: str, config: Optional[dict] = None,
             records: Optional[list] = None, now: Optional[float] = None) -> dict:
    """"Sync now": run ``source`` immediately through the same wrapped path.

    Works with or without a schedule; with one, ``last_run``/``next_run``/
    ``error_count`` are updated exactly as a due run would. ``config`` (if
    given) overrides the schedule's config for this call.
    """
    _guard(tenant)
    _check_source(source)
    now = _now(now)
    with _lock_for(tenant):
        sched = get_schedule(platform, tenant, source)
        cfg = config if config is not None else (sched["config"] if sched else {})
        return _execute(platform, tenant, source, now, cfg, records, records is not None, sched)


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------
def _health_entry(platform, tenant: str, source: str, sched: Optional[dict],
                  cursor, now: float) -> dict:
    last_ok = [t for t in (
        sched["last_run"] if sched else None,
        (cursor["last_sync"] / 1000.0) if (cursor is not None and cursor["last_sync"]) else None,
    ) if t is not None]
    last_run = max(last_ok) if last_ok else None
    freshness = round(max(0.0, now - last_run) / 60.0, 1) if last_run is not None else None
    schedule_enabled = bool(sched["enabled"]) if sched else False
    connector_enabled = admin.is_enabled(platform, tenant, source)
    enabled = schedule_enabled and connector_enabled
    interval = sched["interval_s"] if sched else None
    error_count = sched["error_count"] if sched else 0
    last_status = sched["last_status"] if sched else ("ok" if cursor is not None else None)
    breach = False
    if enabled and interval:
        if freshness is not None:
            breach = freshness > SLA_FACTOR * interval / 60.0
        else:
            breach = error_count > 0       # never succeeded and already failing
    return {"source": source, "enabled": enabled, "schedule_enabled": schedule_enabled,
            "connector_enabled": connector_enabled, "freshness_minutes": freshness,
            "last_run": last_run, "last_status": last_status, "error_count": error_count,
            "next_run": sched["next_run"] if sched else None, "interval_s": interval,
            "items": int(cursor["items"] or 0) if cursor is not None else 0,
            "sla_breach": breach}


def health(platform, tenant: str, now: Optional[float] = None) -> list[dict]:
    """Per-connector health for every scheduled or ever-synced source:
    ``{source, enabled, freshness_minutes, last_status, error_count, next_run,
    interval_s, items, sla_breach, …}`` sorted by source."""
    _guard(tenant)
    now = _now(now)
    scheds = {s["source"]: s for s in schedules(platform, tenant)}
    cursors = {r["source"]: r for r in platform.db.query(
        "SELECT source, last_sync, items FROM connector_cursors WHERE tenant=?", (tenant,))}
    return [_health_entry(platform, tenant, s, scheds.get(s), cursors.get(s), now)
            for s in sorted(set(scheds) | set(cursors))]


def health_for(platform, tenant: str, source: str, now: Optional[float] = None) -> dict:
    """Health of one source (a never-scheduled, never-synced source gets a
    neutral entry) — what the admin connector card joins on."""
    _guard(tenant)
    _check_source(source)
    cursor = platform.db.one(
        "SELECT source, last_sync, items FROM connector_cursors WHERE tenant=? AND source=?",
        (tenant, source))
    return _health_entry(platform, tenant, source, get_schedule(platform, tenant, source),
                         cursor, _now(now))


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------
class RefreshLoop:
    """Daemon thread calling ``run_due(now=time.time())`` every ``tick_s`` seconds.

    ``records_by_source`` (optional) is passed through for offline connectors.
    ``ticks``, ``last_results`` and ``last_error`` expose progress; the loop
    never dies on a failing tick (the error is logged and kept).
    """

    def __init__(self, platform, tenant: str, tick_s: float = 5,
                 records_by_source: Optional[dict] = None):
        _guard(tenant)
        if float(tick_s) <= 0:
            raise ValueError("tick_s must be positive")
        self.p = platform
        self.tenant = tenant
        self.tick_s = float(tick_s)
        self.records_by_source = records_by_source
        self.ticks = 0
        self.last_results: list[dict] = []
        self.last_error: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start the thread; ``False`` if it is already running."""
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name=f"kf-refresh-{self.tenant}")
            self._thread.start()
            return True

    def stop(self, timeout: float = 10.0) -> bool:
        """Signal the thread to stop and wait up to ``timeout``; ``True`` once stopped."""
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout)
        return not self.running

    def run_once(self, now: Optional[float] = None) -> list[dict]:
        """One tick, synchronously (also what the thread calls)."""
        results = run_due(self.p, self.tenant, _now(now), self.records_by_source)
        self.ticks += 1
        self.last_results = results
        return results

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:  # pragma: no cover - keep the loop alive
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("refresh tick failed for %s", self.tenant)
            self._stop.wait(self.tick_s)

    def __enter__(self) -> "RefreshLoop":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
