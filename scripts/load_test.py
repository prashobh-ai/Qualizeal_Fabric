"""Load test (T33) — the governed answer path under concurrent load.

The correctness-concurrency tests prove no cross-tenant leakage and unique
traces; this measures the answer path's *behaviour under sustained concurrent
load* and gates it against SLOs: throughput (QPS), latency percentiles
(p50/p95/p99), a zero error/throttle budget, and that the answer cache actually
speeds a warm run. It runs on the model-free extractive path (the deployed
floor), so it is deterministic and needs no credits.

Load is spread across many synthetic principals (each a distinct user) so the
per-user rate limit never dominates — a realistic "many users at once" shape.

Run:  python scripts/load_test.py --requests 400 --concurrency 12
      python scripts/load_test.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "off")  # deterministic, model-free floor

from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.contracts.types import AnswerKind, Principal  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402

TENANT = "qualizeal"

# A mixed workload — a definition, a code look-up, an asset discovery, a
# multi-fact question and an out-of-corpus decline — so the load exercises every
# retrieval path, not just a hot cache line.
_QUESTIONS = [
    "what is QMentisAI",
    "what does ValidAIte do",
    "how does retry_call work",
    "has anyone made sso and auth code which I can reuse",
    "what is QMentisAI pricing and who is it for",
    "what is the capital of France",  # decline
]
_DOCS = [
    (
        "internal",
        "internal://q/qm.md",
        "QMentisAI",
        "text/markdown",
        "# QMentisAI\n\nQMentisAI is an AI test-intelligence platform. QMentisAI pricing "
        "is usage-based. QMentisAI serves QA engineers and testers.\n",
    ),
    (
        "internal",
        "internal://q/va.md",
        "ValidAIte",
        "text/markdown",
        "# ValidAIte\n\nValidAIte is a validation harness. ValidAIte grades model outputs.\n",
    ),
    (
        "github",
        "github://acme/app/retry.py",
        "retry.py",
        "text/x-python;code",
        '"""Retry helpers."""\n\n\ndef retry_call(fn, attempts=3):\n'
        '    """Retry a flaky call with backoff until it succeeds."""\n'
        "    for _ in range(attempts):\n        try:\n            return fn()\n"
        "        except Exception:\n            continue\n",
    ),
    (
        "github",
        "github://acme/app/auth.py",
        "auth.py",
        "text/x-python;code",
        '"""Auth helpers for single sign-on."""\n\n\n'
        "def mint_session_token(subject, roles, scopes):\n"
        '    """Mint a signed session token for single sign-on."""\n    return {}\n',
    ),
]


def _build_fabric():
    from knowledge_fabric.app import Platform

    p = Platform(db_path=":memory:", blob_root="./data/load-blobs")
    demo.seed(p)
    p.policy.set_budget(TENANT, 1000.0)  # generous; the model-free path costs $0 anyway
    intake, worker = Intake(p), IngestWorker(p, None)
    worker.intake = intake
    for source, uri, title, mime, body in _DOCS:
        intake.submit(
            intake.canonical(TENANT, source, uri, title, body.encode(), mime=mime, acl=["public"])
        )
    worker.drain()
    return p


def _principals(p, n):
    """Mint ``n`` distinct asker principals so the per-user rate limit (60/min)
    never dominates a burst — each simulated user carries its own budget line."""
    out = []
    for i in range(n):
        token = p.idp.mint(
            Principal(subject=f"load-user-{i}", tenant=TENANT, roles=["asker"], scopes=["public"])
        )
        out.append(p.idp.authenticate({"token": token}))
    return out


def _percentile(sorted_ms, q):
    if not sorted_ms:
        return 0.0
    idx = min(len(sorted_ms) - 1, int(len(sorted_ms) * q))
    return round(sorted_ms[idx], 2)


def _phase(svc, principals, requests, concurrency):
    """Fire ``requests`` asks across ``concurrency`` workers; return timing +
    outcome stats. Each request rotates the principal and the question so the
    load is spread and the cache is exercised, not just one hot line."""
    lat_ms, traces, errors, throttled, declined = [], [], 0, 0, 0

    def one(i):
        prin = principals[i % len(principals)]
        q = _QUESTIONS[i % len(_QUESTIONS)]
        t0 = time.perf_counter()
        try:
            a = svc.ask(prin, q)
        except Exception:
            return ("error", 0.0, None)
        dt = (time.perf_counter() - t0) * 1000.0
        if "rate limit" in (a.answer_text or "").lower():
            return ("throttled", dt, a.trajectory_id)
        if a.kind != AnswerKind.ANSWER and q != "what is the capital of France":
            return ("declined", dt, a.trajectory_id)
        return ("ok", dt, a.trajectory_id)

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for outcome, dt, tid in ex.map(one, range(requests)):
            if outcome == "error":
                errors += 1
                continue
            lat_ms.append(dt)
            if tid:
                traces.append(tid)
            if outcome == "throttled":
                throttled += 1
            elif outcome == "declined":
                declined += 1
    wall = time.perf_counter() - t_start
    sl = sorted(lat_ms)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "wall_s": round(wall, 3),
        "qps": round(requests / wall, 1) if wall else 0.0,
        "latency_ms": {
            "p50": _percentile(sl, 0.50),
            "p95": _percentile(sl, 0.95),
            "p99": _percentile(sl, 0.99),
            "max": round(max(sl), 2) if sl else 0.0,
            "mean": round(sum(sl) / len(sl), 2) if sl else 0.0,
        },
        "errors": errors,
        "throttled": throttled,
        "declined": declined,
        "distinct_traces": len(set(traces)),
    }


# SLOs for the model-free path on a small corpus. Generous headroom over
# measured values so the gate flags a real regression, not normal variance.
SLO = {"p95_ms": 750.0, "min_qps": 20.0}


def run_load(requests: int = 400, concurrency: int = 12) -> dict:
    p = _build_fabric()
    svc = AnswerService(p)
    # Both phases reuse the pool inside one 60s window, so size it for the
    # combined burst: 2 phases × (requests / n) per user must stay under the
    # 60/min limit. requests // 20 → ~40 asks/user across both phases (headroom).
    principals = _principals(p, max(16, requests // 20))

    # Cold: fresh cache. Warm: the same load again, now served largely from the
    # answer cache — so a warm run should be at least as fast.
    cold = _phase(svc, principals, requests, concurrency)
    warm = _phase(svc, principals, requests, concurrency)

    breaches = []
    if cold["errors"] or warm["errors"]:
        breaches.append(f"errors: cold={cold['errors']} warm={warm['errors']}")
    if cold["throttled"] or warm["throttled"]:
        breaches.append(f"throttled: cold={cold['throttled']} warm={warm['throttled']}")
    # every answered request must carry its own trajectory (no request bleed)
    answered_cold = cold["requests"] - cold["errors"]
    if cold["distinct_traces"] != answered_cold:
        breaches.append(
            f"trace bleed: {cold['distinct_traces']} distinct != {answered_cold} answered"
        )
    if cold["latency_ms"]["p95"] > SLO["p95_ms"]:
        breaches.append(f"p95 {cold['latency_ms']['p95']}ms > {SLO['p95_ms']}ms")
    if cold["qps"] < SLO["min_qps"]:
        breaches.append(f"qps {cold['qps']} < {SLO['min_qps']}")
    if warm["qps"] < cold["qps"] * 0.9:  # warm cache must not be slower
        breaches.append(f"warm qps {warm['qps']} < cold {cold['qps']} (cache not helping)")

    return {"passed": not breaches, "slo": SLO, "cold": cold, "warm": warm, "breaches": breaches}


def format_report(r: dict) -> str:
    def line(name, ph):
        lm = ph["latency_ms"]
        return (
            f"  {name}: {ph['qps']} qps · p50 {lm['p50']}ms · p95 {lm['p95']}ms · "
            f"p99 {lm['p99']}ms · max {lm['max']}ms · errors {ph['errors']} · "
            f"throttled {ph['throttled']} · traces {ph['distinct_traces']}/{ph['requests']}"
        )

    out = [
        f"Load test: {r['cold']['requests']} req × {r['cold']['concurrency']} workers "
        f"— {'PASS' if r['passed'] else 'FAIL'}",
        line("cold", r["cold"]),
        line("warm", r["warm"]),
    ]
    if r["breaches"]:
        out += ["", "SLO breaches:"] + [f"  - {b}" for b in r["breaches"]]
    return "\n".join(out)


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="load_test")
    ap.add_argument("--requests", type=int, default=400)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = run_load(args.requests, args.concurrency)
    print(json.dumps(r, indent=2) if args.json else format_report(r))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
