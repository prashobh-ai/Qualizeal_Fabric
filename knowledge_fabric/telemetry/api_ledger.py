"""API call ledger (T36) — one row per model call, appended to
``data/api_calls/<day>.jsonl``. This is the proof that the key was used: the
run summary, Admin → Models and Admin → Tokens all read from here, so the
dashboard totals equal the ledger sums by construction.

Row: ``{ts, run_id, workflow, purpose, model, repo, doc_id, question_hash,
input_tokens, output_tokens, cache_read_input_tokens,
cache_creation_input_tokens, latency_ms, cost_usd, request_id}``.

``cost_usd`` comes from ``prices.json`` (per million tokens, maintained by
hand): the packaged copy beside this module, overridable by
``data/prices.json`` in a ``fabric-data`` checkout.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid

PURPOSES = frozenset(
    {
        "repo_summary",
        "capability_classify",
        "answer_bake",
        "ask_queue",
        "agent_step",
        "translate",
        "image_describe",
        "question_gen",
        "table_sql",
        "quality_harness",
        "doctor_ping",
    }
)

_LOCK = threading.Lock()
_PACKAGED_PRICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.json")


def data_root() -> str:
    from ..adapters.model import data_root as _root

    return _root()


def run_id() -> str:
    """The Actions run id when present, else a per-process id — so a bake's
    calls can be summed for its own run summary."""
    rid = os.environ.get("GITHUB_RUN_ID") or os.environ.get("KF_RUN_ID")
    if not rid:
        rid = os.environ["KF_RUN_ID"] = uuid.uuid4().hex[:12]
    return rid


def workflow() -> str:
    return os.environ.get("GITHUB_WORKFLOW") or os.environ.get("KF_WORKFLOW") or "local"


def prices() -> dict:
    """Per-million-token prices by model: fabric-data override, else packaged."""
    for path in (os.path.join(data_root(), "prices.json"), _PACKAGED_PRICES):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except (OSError, ValueError):
            continue
    return {}


def cost_for(model: str, usage: dict) -> float:
    """USD for one call from its real usage block."""
    p = prices().get(model)
    if not p:
        return 0.0
    m = 1_000_000.0
    return round(
        int(usage.get("input_tokens", 0) or 0) * p.get("input", 0.0) / m
        + int(usage.get("output_tokens", 0) or 0) * p.get("output", 0.0) / m
        + int(usage.get("cache_read_input_tokens", 0) or 0) * p.get("cache_read", 0.0) / m
        + int(usage.get("cache_creation_input_tokens", 0) or 0) * p.get("cache_write", 0.0) / m,
        8,
    )


def _day_path(ts: float | None = None) -> str:
    day = _dt.datetime.fromtimestamp(ts or _dt.datetime.now().timestamp(), _dt.UTC)
    return os.path.join(data_root(), "api_calls", day.strftime("%Y-%m-%d") + ".jsonl")


def record(
    *,
    purpose: str,
    model: str,
    usage: dict,
    latency_ms: float,
    request_id: str = "",
    repo: str = "",
    doc_id: str = "",
    question_hash: str = "",
) -> dict:
    """Append one row; returns it (with ``cost_usd``). Unknown purposes are
    recorded as given but flagged, never dropped — the ledger must be complete."""
    now = _dt.datetime.now(_dt.UTC)
    row = {
        "ts": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "run_id": run_id(),
        "workflow": workflow(),
        "purpose": purpose if purpose in PURPOSES else f"unknown:{purpose}",
        "model": model,
        "repo": repo or "",
        "doc_id": doc_id or "",
        "question_hash": question_hash or "",
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
        "latency_ms": round(float(latency_ms), 2),
        "cost_usd": cost_for(model, usage),
        "request_id": request_id or "",
    }
    path = _day_path(now.timestamp())
    with _LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def rows(days: int = 7) -> list[dict]:
    """Every row from the last ``days`` day-files (oldest first)."""
    base = os.path.join(data_root(), "api_calls")
    if not os.path.isdir(base):
        return []
    cutoff = (_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
    out: list[dict] = []
    for name in sorted(os.listdir(base)):
        if not name.endswith(".jsonl") or name[:-6] < cutoff:
            continue
        with open(os.path.join(base, name), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    return out


def summary(items: list[dict], run: str | None = None) -> dict:
    """Totals (optionally for one run): calls, tokens in/out, cache read/write,
    cost, and the models used."""
    sel = [r for r in items if run is None or r.get("run_id") == run]
    models: dict[str, int] = {}
    for r in sel:
        models[r.get("model", "")] = models.get(r.get("model", ""), 0) + 1
    return {
        "calls": len(sel),
        "input_tokens": sum(r.get("input_tokens", 0) for r in sel),
        "output_tokens": sum(r.get("output_tokens", 0) for r in sel),
        "cache_read": sum(r.get("cache_read_input_tokens", 0) for r in sel),
        "cache_write": sum(r.get("cache_creation_input_tokens", 0) for r in sel),
        "cost_usd": round(sum(float(r.get("cost_usd", 0.0)) for r in sel), 6),
        "models": models,
    }


def breakdown(items: list[dict], by: str) -> dict:
    """Consumption grouped by ``day | purpose | model | repo | workflow`` —
    each bucket a ``summary``-shaped dict."""
    keyf = {
        "day": lambda r: (r.get("ts") or "")[:10],
        "purpose": lambda r: r.get("purpose", ""),
        "model": lambda r: r.get("model", ""),
        "repo": lambda r: r.get("repo", "") or "—",
        "workflow": lambda r: r.get("workflow", ""),
    }[by]
    groups: dict[str, list[dict]] = {}
    for r in items:
        groups.setdefault(keyf(r), []).append(r)
    return {k: summary(v) for k, v in sorted(groups.items())}


def consumption(days: int = 7) -> dict:
    """The Admin → Models payload: totals + every breakdown + last 50 calls."""
    items = rows(days)
    return {
        "days": days,
        "totals": summary(items),
        "by_day": breakdown(items, "day"),
        "by_purpose": breakdown(items, "purpose"),
        "by_model": breakdown(items, "model"),
        "by_repo": breakdown(items, "repo"),
        "by_workflow": breakdown(items, "workflow"),
        "last_calls": list(reversed(items[-50:])),
        "prices": prices(),
    }


def step_summary_line(run: str | None = None) -> str:
    """``API calls: n · input .. · output .. · cache read .. · cost $x · model <id>``."""
    s = summary(rows(2), run or run_id())
    models = " + ".join(sorted(s["models"])) if s["models"] else "—"
    return (
        f"API calls: {s['calls']} · input {s['input_tokens']} · output {s['output_tokens']} · "
        f"cache read {s['cache_read']} · cost ${s['cost_usd']:.4f} · model {models}"
    )


def append_step_summary(text: str) -> None:
    """Append to ``$GITHUB_STEP_SUMMARY`` when running under Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")
