"""End-to-end execution demo (Runbook Sections 3, 4, 6, 8, 10).

Narrates the whole golden path on synthetic tenants so a reviewer sees the
platform behave like a product: ingest -> cited answer -> honest refusal ->
idempotency -> tenant isolation -> permission-before-ranking -> model-off
extractive core -> budget cap under a race -> eval gate blocking a corrupt
index -> one trace per answer with cost.
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("KF_MODEL_MODE", "mock")

from knowledge_fabric.app import Platform                       # noqa: E402
from knowledge_fabric.answer.service import AnswerService        # noqa: E402
from knowledge_fabric.contracts.types import AnswerKind, Principal  # noqa: E402
from knowledge_fabric.evaluation import gate                     # noqa: E402
from knowledge_fabric.health import metrics as health            # noqa: E402
from knowledge_fabric.tenants import demo                        # noqa: E402


def rule(t):
    print("\n" + "═" * 78 + f"\n▐ {t}\n" + "═" * 78)


def show(a):
    print(f"  → {a.kind.value.upper():8s}  grounding={a.grounding_score}  "
          f"confidence={a.confidence}  tier={a.tier}  cost=${a.cost}")
    if a.answer_text:
        print(f"    {a.answer_text[:220]}")
    for i, c in enumerate(a.citations, 1):
        print(f"      [{i}] {c.document_title} — {c.coordinate.render()}  ·  “{c.snippet[:60]}…”")
    if a.clarify_back:
        print(f"    CLARIFY: {a.clarify_back[:150]}")


def main():
    p = Platform(db_path=":memory:", blob_root="./data/demo-blobs")

    rule("1. SEED — synthetic demo tenants (identifier-safety validated)")
    print("  identifier-safety problems:", demo.validate_identifiers() or "NONE ✓")
    print("  ingest summary:", demo.seed(p))

    svc = AnswerService(p)
    asker = demo.principal_for(p, "acme-assurance", "asha.asker")
    curator = demo.principal_for(p, "acme-assurance", "carl.curator")
    restricted = demo.principal_for(p, "acme-assurance", "rana.restricted")
    agent = demo.principal_for(p, "acme-assurance", "qa-agent")

    rule("2. GROUNDED ANSWER — cited to exact coordinates across modalities")
    show(svc.ask(asker, "what must a release achieve before promotion?"))
    show(svc.ask(asker, "which requirement has a traceability gap?"))
    show(svc.ask(asker, "what blocks the release according to the standup?"))

    rule("3. HONEST REFUSAL — out-of-corpus question is a declared gap, not a guess")
    show(svc.ask(asker, "what is the capital of France?"))

    rule("4. IDEMPOTENCY (I9) — re-ingesting identical content is a no-op")
    from knowledge_fabric.ingestion.intake import Intake, IngestWorker
    intake, worker = Intake(p), IngestWorker(p, None); worker.intake = intake
    raw = intake.canonical("acme-assurance", "files", "file://qa/test-strategy.md", "Test Strategy v3",
                           demo.CORPORA["acme-assurance"][0][4].encode(), mime="text/markdown")
    intake.submit(raw)
    print("  re-ingest result (same content-hash):", [r["status"] for r in worker.drain()])

    rule("5. TENANT ISOLATION (I5) — a store call without a tenant fails closed")
    try:
        p.passages.count("")
    except PermissionError as e:
        print("  blocked ✓ :", e)

    rule("6. PERMISSION-BEFORE-RANKING (I6) — restricted policy never retrieved for an asker")
    qv = p.embedder.embed(["how fast must critical defects be triaged"])[0]
    hits = p.vindex.search("acme-assurance", qv, 20, restricted.accessible_acls())
    leaked = [pid for pid, _ in hits if "restricted" in p.passages.acl_of("acme-assurance", pid)]
    print("  restricted passages in asker's retrieval set:", leaked, "→", "NONE ✓" if not leaked else "LEAK!")
    show(svc.ask(restricted, "how fast must critical defects be triaged?"))
    print("  (same question as curator, who MAY see the restricted policy:)")
    show(svc.ask(curator, "how fast must critical defects be triaged?"))

    rule("7. THE MODEL IS A DIAL (I4) — disable the model, core still answers extractively")
    p.model = __import__("knowledge_fabric.adapters.model", fromlist=["DisabledModelClient"]).DisabledModelClient()
    print("  model available:", p.model_available())
    show(svc.ask(asker, "what is the acceptance criteria for coverage?"))

    rule("8. AGENT PARITY (I7) — an agent uses the SAME gate and is audited")
    a = svc.ask(agent, "what must a release achieve before promotion?")
    show(a)
    rows = p.audit.for_trace("acme-assurance", a.trajectory_id)
    print("  audit row:", {"subject": rows[0]["subject"], "is_agent": rows[0]["is_agent"],
                           "decision": rows[0]["decision"]})

    rule("9. BUDGET CAP UNDER A 100-WAY RACE (I12) — atomic, never exceeded")
    p.policy.set_budget("acme-assurance", 10.0)
    p.db.execute("UPDATE budgets SET spent=0 WHERE tenant=?", ("acme-assurance",))
    ok = []
    ts = [threading.Thread(target=lambda: ok.append(p.policy.try_spend("acme-assurance", 1.0)))
          for _ in range(100)]
    [t.start() for t in ts]; [t.join() for t in ts]
    print(f"  cap=10  requests=100  granted={sum(ok)}  spent=${p.policy.spent('acme-assurance')}  "
          f"→ {'HELD ✓' if p.policy.spent('acme-assurance') <= 10 else 'BLOWN!'}")

    rule("10. PROMOTION GATE (I10) — a corrupted citation blocks going live")
    p.policy.set_budget("acme-assurance", 100.0)      # restore headroom after the race in step 9
    p.db.execute("UPDATE budgets SET spent=0 WHERE tenant=?", ("acme-assurance",))
    good = gate.evaluate(p, "acme-assurance", 1)
    print("  clean index → passed:", good["passed"], "metrics:", good["metrics"]["citation_coverage"],
          "coverage /", good["metrics"]["recall"], "recall")
    gate.corrupt_citation_coordinates(p, "acme-assurance")
    bad = gate.promote_if_passes(p, "acme-assurance", 2)
    print("  corrupted index → passed:", bad["passed"], " promoted:", bad["promoted"],
          " regressions:", bad["regressions"])

    rule("11. ECONOMICS & HEALTH — one trace per answer; cost by tier/stage; risk register")
    m = p.telemetry.metrics("acme-assurance")
    print("  answers:", m["answers"], " p95 latency(ms):", m["latency_p95_ms"],
          " total cost:$", m["total_cost"])
    print("  cost by tier:", m["cost_by_tier"])
    print("  cost by stage:", m["cost_by_stage"])
    print("  clarify-back rate:", m["clarify_back_rate"], " citation coverage:", m["citation_coverage"])
    print("  risk register:", health.risk_register(p, "acme-assurance"))

    rule("DONE — every invariant demonstrated on hardware you can carry, no cloud.")


if __name__ == "__main__":
    main()
