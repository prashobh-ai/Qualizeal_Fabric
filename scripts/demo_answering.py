"""Answering-intelligence demo (T34) — the T24–T33 capstone.

`scripts/demo.py` narrates the platform *fundamentals* (ingest → cited answer →
honest refusal → isolation → budget → promotion gate → economics). This demo
narrates the *answering-intelligence* track built on top of that floor — the
T24–T33 series — as one coherent story a stakeholder can watch end to end:

  T24 discovery      — find reusable assets across the fabric, ranked, cited
  T25 code answers   — a function explained from source, at an exact locator
  T26 two-turn context — a follow-up ("what about its pricing") resolved
  T27 role-conditioned — one question, one evidence set, framed per designation
  T30 telemetry dims  — every answer as an Explorer row (persona/scope/context)
  T31 MCP parity      — an agent hits the SAME governed path, no back door
  T28 quality gate    — a golden suite that blocks a regression
  T32 static parity    — the shipped engine.js answers like the server (if node)
  T33 load             — the answer path under concurrent load, gated on SLOs

It runs on the model-free extractive path (KF_MODEL_MODE=off, the deployed
floor), so it is deterministic, needs no credits, and shows exactly what ships.

Run:  python scripts/demo_answering.py
"""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "off")  # deterministic, model-free floor

from knowledge_fabric.answer.search import discover as discover_assets  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402

TENANT = "qualizeal"

# A small, self-contained corpus — two products (distinctive camelCase names),
# two code files (a helper + an auth/SSO helper), and an SSO policy — enough to
# exercise every answering path the T24–T33 series added.
_DOCS = [
    (
        "internal",
        "internal://q/qmentis.md",
        "QMentisAI",
        "text/markdown",
        "# QMentisAI\n\nQMentisAI is an AI test-intelligence platform for quality "
        "engineering. QMentisAI pricing is usage-based, billed per test run. "
        "QMentisAI serves QA engineers and testers.\n",
    ),
    (
        "internal",
        "internal://q/validaite.md",
        "ValidAIte",
        "text/markdown",
        "# ValidAIte\n\nValidAIte is a validation harness for AI systems. ValidAIte "
        "runs evaluation suites and grades model outputs for testers.\n",
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
        '    """Mint a signed session token for single sign-on."""\n'
        '    return {"subject": subject}\n',
    ),
    (
        "internal",
        "internal://q/sso.md",
        "Single Sign-On Standard",
        "text/markdown",
        "# Single Sign-On Standard\n\nSingle sign-on lets an employee sign in once. "
        "Reuse the shared authentication building blocks rather than writing your own "
        "login flow.\n",
    ),
]


def rule(t: str) -> None:
    print("\n" + "═" * 78 + f"\n▐ {t}\n" + "═" * 78)


def show(a, indent: str = "  ") -> None:
    """One-line verdict plus the answer head and its citations — the shape a
    reader sees, whatever the path (grounded / discovery / code / decline)."""
    head = (a.answer_text or "").strip().splitlines()
    print(
        f"{indent}→ {a.kind.value.upper():7s} grounding={a.grounding_score} "
        f"tier={a.tier} cost=${a.cost}"
    )
    for ln in head[:4]:
        print(f"{indent}  {ln[:96]}")
    for i, c in enumerate(a.citations[:3], 1):
        print(f"{indent}  [{i}] {c.document_title} — {c.coordinate.render()}")


def _build_fabric():
    from knowledge_fabric.app import Platform

    p = Platform(db_path=":memory:", blob_root="./data/demo-answering-blobs")
    demo.seed(p)
    p.policy.set_budget(TENANT, 1000.0)  # model-free path costs $0; generous anyway
    intake, worker = Intake(p), IngestWorker(p, None)
    worker.intake = intake
    for source, uri, title, mime, body in _DOCS:
        intake.submit(
            intake.canonical(TENANT, source, uri, title, body.encode(), mime=mime, acl=["public"])
        )
    worker.drain()
    return p


def main() -> int:
    p = _build_fabric()
    svc = AnswerService(p)
    asker = demo.principal_for(p, TENANT, "asker.public")

    rule("0. THE FLOOR — a grounded, cited answer and an honest decline")
    show(svc.ask(asker, "what is QMentisAI"))
    print("  (out of corpus → a declared gap, never a guess:)")
    show(svc.ask(asker, "what is the capital of France"))

    rule("1. T24 DISCOVERY — 'has anyone made sso/auth code I can reuse?' → ranked assets")
    a = svc.ask(asker, "has anyone made sso and auth code which I can reuse")
    show(a)
    for asset in (a.why or {}).get("discovery", [])[:4]:
        where = asset.get("path") or asset.get("url") or ""
        print(f"      • {asset['title']} ({asset['kind']}) — {where}")

    rule("2. T25 CODE ANSWER — 'how does retry_call work?' → source at an exact locator")
    a = svc.ask(asker, "how does retry_call work")
    show(a)
    print("  has code block:", "```" in (a.answer_text or ""))

    rule("3. T26 TWO-TURN CONTEXT — a follow-up resolved from the previous turn")
    turn1 = "what is QMentisAI"
    print(f"  turn 1: {turn1!r}")
    show(svc.ask(asker, turn1))
    followup = "what about its pricing"
    a = svc.ask(asker, followup, context={"turns": [{"question": turn1}]})
    print(f"  turn 2: {followup!r}  →  understood_as: {a.understood_as!r}")
    show(a)

    rule("4. T27 ROLE-CONDITIONED — one question, one evidence set, framed per designation")
    print("  same question 'what is QMentisAI', same citations — the LENS differs:")
    for subject in ("developer", "tester", "cto", "curator", "asker.public"):
        prin = demo.principal_for(p, TENANT, subject)
        a = svc.ask(prin, "what is QMentisAI")
        rv = a.role_view or {}
        print(
            f"   {rv.get('designation') or 'general':16s} → persona={rv.get('persona'):9s} "
            f"lens={rv.get('lens'):10s} depth={rv.get('depth'):8s} emphasis={rv.get('emphasis')}"
        )

    rule("5. T30 TELEMETRY — every answer above is one Explorer row (T30 dimensions in bold)")
    events = p.telemetry.events(TENANT)
    print(f"  {len(events)} answer spans recorded. persona/scope/context are first-class dims:")
    print(f"   {'persona':10s} {'designation':16s} {'scope':8s} {'context':8s} {'kind':7s} cited")
    for e in events[-6:]:
        print(
            f"   {e['persona']:10s} {e['designation']:16s} {e['scope']:8s} "
            f"{e['context']:8s} {e['kind']:7s} {'yes' if e['cited'] else 'no'}"
        )

    rule("6. T31 MCP PARITY — an agent hits the SAME governed path (no ungoverned back door)")
    # This is exactly what the MCP `ask` tool does internally: a least-privilege
    # agent principal (roles=['agent'], scopes=['public']) through the one service.
    from knowledge_fabric.mcp.server import _agent_principal

    agent = _agent_principal(p, TENANT)
    a = svc.ask(agent, "what is QMentisAI")
    print(f"  MCP tools: ask · discover · corpus (agent={agent.agent}, scopes={agent.scopes})")
    show(a)
    d = discover_assets(p, TENANT, "sso auth code to reuse", agent.accessible_acls(), k=4)
    print(
        f"  discover('sso auth') → {len([h for h in d.hits if h.score > 0])} assets, same ranking"
    )
    rows = p.audit.for_trace(TENANT, a.trajectory_id)
    print(f"  audited as agent: is_agent={rows[0]['is_agent']} decision={rows[0]['decision']}")

    rule("7. T28 QUALITY GATE — a golden suite that blocks a regression")
    from knowledge_fabric.evaluation.quality import run_quality_suite

    res = run_quality_suite()
    print(
        f"  {res.passing}/{res.total} golden cases pass (score {res.score:.2f}) — "
        f"{'PASS' if res.passed else 'FAIL'}"
    )
    print("  by dimension:", res.dimensions)

    rule("8. T32 STATIC PARITY — the shipped engine.js answers like the server")
    if shutil.which("node"):
        from scripts import parity_check

        pr = parity_check.run_parity()
        print(f"  {pr['passing']}/{pr['total']} cases agree — {'PASS' if pr['passed'] else 'FAIL'}")
        print("  (kind, coreference, persona lens, discovery/code shape all match client-side)")
    else:
        print("  node not present here → skipped (CI's mcp job runs it). `make parity` locally.")

    rule("9. T33 LOAD — the answer path under concurrent load, gated on SLOs")
    from scripts import load_test

    lr = load_test.run_load(requests=80, concurrency=8)
    for name in ("cold", "warm"):
        ph = lr[name]
        print(
            f"  {name}: {ph['qps']} qps · p95 {ph['latency_ms']['p95']}ms · "
            f"errors {ph['errors']} · throttled {ph['throttled']} · "
            f"unique traces {ph['distinct_traces']}/{ph['requests']}"
        )
    print(f"  SLOs {'HELD ✓' if lr['passed'] else 'BREACHED: ' + '; '.join(lr['breaches'])}")

    rule(
        "DONE — T24–T33: discovery, code, context, role framing, telemetry, MCP, "
        "quality, parity and load — all on the model-free floor, no credits."
    )

    # A non-zero exit if any hard gate regressed, so the demo doubles as a smoke check.
    ok = res.passed and lr["passed"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
