"""Nightly quality runner (T49).

    python3 eval/quality.py --sets general code followup role facts tables images gap jira agent \
                            --model real --sample 40

Sets:
  general code followup role gap  — the T28 golden suite, by dimension
  facts tables images jira       — eval/sets/*.jsonl over the deterministic fixture
                                    (eval/fixture.py), answered at Level 0 / by table
                                    query / from the image description — model-free
  agent                          — eval/sets/agent.jsonl through the tool-using agent;
                                    needs --model real (a key); skipped with --model
                                    extractive and reported as such
Gates: false_answer_rate = 0, exact_count_accuracy = 1.0, citation_rate >= 0.95,
agent_success >= 0.85 (when the agent set ran). Writes data/quality/<date>.json and
appends a line to $GITHUB_STEP_SUMMARY; exits non-zero on any gate failure or if a
requested set cannot run.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

GATES = {
    "false_answer_rate": 0.0,
    "exact_count_accuracy": 1.0,
    "citation_rate": 0.95,
    "agent_success": 0.85,
}
T28_SETS = {
    "general": ("answering", "grounding"),
    "code": ("code",),
    "followup": ("context",),
    "role": ("persona",),
    "gap": ("decline", "robustness", "isolation"),
}
FIXTURE_SETS = ("facts", "tables", "images", "jira")


def load_set(name: str) -> list[dict]:
    """Rows of ``eval/sets/<name>.jsonl``; an empty list when the set does not
    exist (the gate then reports the set as unavailable and fails)."""
    p = os.path.join(ROOT, "eval", "sets", f"{name}.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _sample(rows, n, seed=7):
    if n and n < len(rows):
        return random.Random(seed).sample(rows, n)
    return rows


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _numbers_in(text: str) -> list[float]:
    import re

    return [float(t.replace(",", "")) for t in re.findall(r"\d[\d,]*\.?\d*", text or "")]


def _matches(expect, answer_text: str, kind: str) -> bool:
    text = (answer_text or "").lower()
    if kind == "list":
        return all(str(e).lower() in text for e in expect)
    if isinstance(expect, dict):
        return all(str(v) in text for v in expect.values())
    want = _num(expect)
    if want is None:
        return str(expect).lower() in text
    nums = _numbers_in(text)
    if float(want).is_integer():
        # Counts are exact: "1,285 commits" is a wrong answer to "1,284".
        return any(n == want for n in nums)
    return any(abs(n - want) < 0.01 or abs(n - want) <= 0.005 * abs(want) for n in nums)


# ---- runners per set ------------------------------------------------------
def run_t28(sets: list[str]) -> dict:
    from knowledge_fabric.evaluation import quality as q

    res = q.run_quality_suite()
    out = {}
    for s in sets:
        dims = T28_SETS[s]
        rows = [r for r in res.rows if r["dimension"] in dims]
        out[s] = {
            "n": len(rows),
            "pass": sum(1 for r in rows if r["ok"]),
            "failures": [r["name"] for r in rows if not r["ok"]],
        }
    return out


def _platform_for_fixture():
    os.environ.setdefault("KF_MODEL_MODE", "off")
    from knowledge_fabric.answer.service import AnswerService
    from knowledge_fabric.app import Platform
    from knowledge_fabric.evaluation import quality as q

    p = Platform(
        db_path=":memory:", blob_root=os.path.join(tempfile.mkdtemp(prefix="kf-eval-"), "blobs")
    )
    q.build_eval_fabric(p)
    return p, AnswerService(p), q._principal(p, q.EVAL_TENANT)


def run_facts_like(name: str, rows: list[dict]) -> dict:
    """facts + jira sets: Level-0 answers from answer.aggregate over the fixture."""
    try:
        from knowledge_fabric.answer import aggregate
    except ImportError as e:
        return {
            "n": len(rows),
            "pass": 0,
            "unavailable": f"answer.aggregate missing: {e}",
            "failures": [],
        }
    p, svc, prin = _platform_for_fixture()
    ok, failures, cited, false_answers = 0, [], 0, 0
    for r in rows:
        a = aggregate.try_answer(p, prin, r["question"], None)
        if a is None:
            failures.append(f"{r['id']}: no Level-0 answer")
            continue
        text = a.answer_text or ""
        if a.citations:
            cited += 1
        if _matches(r["expect"], text, r.get("kind", "count")):
            ok += 1
        else:
            false_answers += 1
            failures.append(f"{r['id']}: expected {r['expect']!r}, got {text[:100]!r}")
    return {
        "n": len(rows),
        "pass": ok,
        "cited": cited,
        "false_answers": false_answers,
        "failures": failures,
    }


def run_tables(rows: list[dict]) -> dict:
    try:
        from knowledge_fabric.answer import tables
    except ImportError as e:
        return {
            "n": len(rows),
            "pass": 0,
            "unavailable": f"answer.tables missing: {e}",
            "failures": [],
        }
    ok, failures, cited = 0, [], 0
    for r in rows:
        res = tables.run_table_query(r["doc_id"], r["sheet"], r["sql_hint"])
        val = res["rows"][0][0] if res.get("rows") else None
        if res.get("citations") or res.get("citation"):
            cited += 1
        want = _num(r["expect"])
        got = _num(val)
        if got is not None and want is not None and abs(got - want) < 0.01:
            ok += 1
        else:
            failures.append(f"{r['id']}: expected {r['expect']}, got {val}")
    return {
        "n": len(rows),
        "pass": ok,
        "cited": cited,
        "false_answers": len(failures),
        "failures": failures,
    }


def run_images(rows: list[dict]) -> dict:
    from knowledge_fabric import fabric_data as fd

    ok, failures = 0, []
    for r in rows:
        d = fd.read_json(fd.path("images", r["doc_id"], r["name"] + ".json"), {})
        blob = json.dumps(d).lower()
        if r["expect_substring"].lower() in blob and d.get("citation_url"):
            ok += 1
        else:
            failures.append(f"{r['id']}: {r['expect_substring']!r} not in description")
    return {
        "n": len(rows),
        "pass": ok,
        "cited": ok,
        "false_answers": len(failures),
        "failures": failures,
    }


def run_agent(rows: list[dict], model: str) -> dict:
    if model != "real":
        return {
            "n": len(rows),
            "pass": 0,
            "skipped": "agent set needs --model real",
            "failures": [],
        }
    try:
        from knowledge_fabric.answer import agent
    except ImportError as e:
        return {
            "n": len(rows),
            "pass": 0,
            "unavailable": f"answer.agent missing: {e}",
            "failures": [],
        }
    os.environ["KF_LEDGER_PURPOSE"] = "quality_harness"
    p, svc, prin = _platform_for_fixture()
    ok, failures, cited = 0, [], 0
    for r in rows:
        try:
            a = agent.run(p, prin, r["question"])
        except Exception as e:  # noqa: BLE001 — a crash is a failed case, recorded
            failures.append(f"{r['id']}: {type(e).__name__}: {e}")
            continue
        tools = {s.get("tool") for s in ((a.why or {}).get("steps") or [])}
        if a.citations:
            cited += 1
        if a.citations and set(r["expect_tools"]) & tools:
            ok += 1
        else:
            failures.append(f"{r['id']}: tools={sorted(tools)} cited={bool(a.citations)}")
    return {"n": len(rows), "pass": ok, "cited": cited, "false_answers": 0, "failures": failures}


def evaluate(sets: list[str], model: str = "extractive", sample: int = 0) -> dict:
    from eval import fixture

    results: dict = {}
    t28 = [s for s in sets if s in T28_SETS]
    if t28:
        results.update(run_t28(t28))
    if any(s in sets for s in FIXTURE_SETS + ("agent",)):
        fixture.build(tempfile.mkdtemp(prefix="kf-eval-fixture-"))
    for s in sets:
        if s in T28_SETS:
            continue
        rows = _sample(load_set(s), sample)
        if not rows:
            results[s] = {"n": 0, "pass": 0, "unavailable": f"no rows for set {s}", "failures": []}
            continue
        if s in ("facts", "jira"):
            results[s] = run_facts_like(s, rows)
        elif s == "tables":
            results[s] = run_tables(rows)
        elif s == "images":
            results[s] = run_images(rows)
        elif s == "agent":
            results[s] = run_agent(rows, model)
        else:
            results[s] = {"n": 0, "pass": 0, "unavailable": f"unknown set {s}", "failures": []}

    # ---- gates ---------------------------------------------------------
    counted = [
        v for k, v in results.items() if k in ("facts", "tables", "jira") and "unavailable" not in v
    ]
    n_counted = sum(v["n"] for v in counted)
    exact = (sum(v["pass"] for v in counted) / n_counted) if n_counted else None
    fa = (sum(v.get("false_answers", 0) for v in counted) / n_counted) if n_counted else None
    cite_sets = [
        v
        for k, v in results.items()
        if k in ("facts", "tables", "images", "jira", "agent")
        and "unavailable" not in v
        and "skipped" not in v
    ]
    n_cite = sum(v["n"] for v in cite_sets)
    cite = (sum(v.get("cited", 0) for v in cite_sets) / n_cite) if n_cite else None
    ag = results.get("agent")
    agent_success = (
        (ag["pass"] / ag["n"])
        if ag and ag.get("n") and "skipped" not in ag and "unavailable" not in ag
        else None
    )
    t28_ok = all(v["pass"] == v["n"] for k, v in results.items() if k in T28_SETS)
    metrics = {
        "false_answer_rate": fa,
        "exact_count_accuracy": exact,
        "citation_rate": cite,
        "agent_success": agent_success,
        "golden_suite": t28_ok,
    }
    unavailable = {k: v.get("unavailable") for k, v in results.items() if v.get("unavailable")}
    gate_fail = []
    if not t28_ok:
        gate_fail.append("golden suite")
    if fa is not None and fa > GATES["false_answer_rate"]:
        gate_fail.append(f"false_answer_rate {fa:.3f} > 0")
    if exact is not None and exact < GATES["exact_count_accuracy"]:
        gate_fail.append(f"exact_count_accuracy {exact:.3f} < 1.0")
    if cite is not None and cite < GATES["citation_rate"]:
        gate_fail.append(f"citation_rate {cite:.3f} < 0.95")
    if agent_success is not None and agent_success < GATES["agent_success"]:
        gate_fail.append(f"agent_success {agent_success:.3f} < 0.85")
    for k, why in unavailable.items():
        gate_fail.append(f"set {k} could not run: {why}")
    return {
        "at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "model": model,
        "sets": results,
        "metrics": metrics,
        "gates": GATES,
        "gate_failures": gate_fail,
        "passed": not gate_fail,
    }


def format_report(rep: dict) -> str:
    lines = [f"Quality sets ({rep['model']}): {'PASS' if rep['passed'] else 'FAIL'}"]
    for k, v in rep["sets"].items():
        extra = v.get("unavailable") or v.get("skipped") or ""
        lines.append(f"  {k:10s} {v['pass']}/{v['n']}" + (f"  — {extra}" if extra else ""))
        for fl in v.get("failures", [])[:5]:
            lines.append(f"      ✗ {fl}")
    m = rep["metrics"]
    lines.append(
        "  metrics: " + ", ".join(f"{k}={('—' if v is None else v)}" for k, v in m.items())
    )
    if rep["gate_failures"]:
        lines.append("  gates failed: " + "; ".join(rep["gate_failures"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quality")
    ap.add_argument("--sets", nargs="+", default=["general", "code", "followup", "role", "gap"])
    ap.add_argument("--model", choices=["real", "extractive"], default="extractive")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = evaluate(args.sets, args.model, args.sample)
    from knowledge_fabric import fabric_data as fd
    from knowledge_fabric.telemetry import api_ledger

    out = fd.data_path(os.path.join("quality", rep["at"][:10] + ".json"), mkdir=True)
    fd.write_json(out, rep)
    text = json.dumps(rep, indent=2) if args.json else format_report(rep)
    print(text)
    api_ledger.append_step_summary(
        format_report(rep).splitlines()[0]
        + " · "
        + ", ".join(f"{k}={('—' if v is None else v)}" for k, v in rep["metrics"].items())
    )
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
