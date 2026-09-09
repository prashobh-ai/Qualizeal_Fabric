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
    summary = demo.seed(p)
    print("  ingest summary:", summary)

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
    # isolated throwaway platform so the destructive corruption never poisons the demo
    gp = Platform(db_path=":memory:", blob_root="./data/demo-gate")
    demo.seed(gp, ["acme-assurance"])
    good = gate.evaluate(gp, "acme-assurance", 1)
    print("  clean index → passed:", good["passed"], "metrics:", good["metrics"]["citation_coverage"],
          "coverage /", good["metrics"]["recall"], "recall")
    gate.corrupt_citation_coordinates(gp, "acme-assurance")
    bad = gate.promote_if_passes(gp, "acme-assurance", 2)
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

    # ================= leadership-agreed capabilities (roadmap) =========
    rule("12. WS1 CONNECT — automated ingestion from GitHub + Jira + files (one canonical record)")
    from knowledge_fabric.ingestion.sync import SyncManager
    from knowledge_fabric.connectors import registry as _reg
    sm = SyncManager(p)
    print("  seed already auto-synced:", summary["acme-assurance"].get("connectors"))
    print("  registry (plug-and-play):", _reg.available())
    # a NEW Jira issue arrives -> only the delta is ingested (change detection)
    new_issue = dict(project="REL", key="REL-99", summary="Audit every promotion decision",
                     status="Open", updated=999999, acl=["public"],
                     description="The audit trail must capture every promotion decision with its trace id.")
    delta = sm.sync("acme-assurance", "jira", {"projects": ["REL"]},
                    records=demo.JIRA_RECORDS["acme-assurance"] + [new_issue])
    print(f"  new Jira issue REL-99 → pulled {delta['pulled']} (delta only), ingested {delta['ingested']}")
    print("  source health:", sm.source_health("acme-assurance"))

    rule("13. WS2 DECIDE — 4-level model selector with an explainable WHY + EN/FR/ES/JA")
    p.model = __import__("knowledge_fabric.adapters.model", fromlist=["MockModelClient"]).MockModelClient()
    p.policy.set_budget("acme-assurance", 100.0)
    p.db.execute("UPDATE budgets SET spent=0 WHERE tenant=?", ("acme-assurance",))
    for q in ["what is the coverage target?",
              "why does a component with an open defect block its dependent releases?",
              "quel est le critère d acceptation pour la couverture?"]:
        a = svc.ask(asker, q)
        w = a.why or {}
        print(f"  [{a.lang}] L{a.level} {w.get('level_name'):10s} tier={a.tier:5s} "
              f"reasons={[r['code'] for r in w.get('reasons', [])]}")
        print(f"     why: {w.get('explain')}")

    rule("14. WS3 PROVE — caching savings by technique + filtered analytics (24h/7d, user, role)")
    # repeat a question to show the answer cache saving model spend
    svc.ask(asker, "what is the coverage target?")
    a7 = p.telemetry.analytics("acme-assurance", "7d")
    print(f"  answers={a7['answers']} tokens_in={a7['tokens_in']} tokens_out={a7['tokens_out']} "
          f"cost=${a7['total_cost']} saved=${a7['total_cost_saved']} cache_hit_rate={a7['cache_hit_rate']}")
    print("  routing by level:", a7["routing_by_level"])
    print("  routing reasons (why):", a7["routing_reasons"])
    print("  savings by technique:", a7["savings_by_technique"])
    print("  per role:", a7["per_role"])
    print("  by language:", a7["by_language"])
    a24 = p.telemetry.analytics("acme-assurance", "24h", role="asker")
    print(f"  filter[24h, role=asker]: answers={a24['answers']} users={list(a24['per_user'])}")


    # ================= Stage 2 — Rasool's list ==========================
    from knowledge_fabric.answer import reasoning as _reasoning
    from knowledge_fabric.governance import authority as _auth
    from knowledge_fabric.stores import versioning as _ver
    from knowledge_fabric.ingestion import scheduler as _sched, runs as _runs
    from knowledge_fabric.connectors import admin as _cadmin
    from knowledge_fabric.health import kb_eval as _kb
    from knowledge_fabric.ingestion.intake import Intake as _Intake, IngestWorker as _Worker
    T = "acme-assurance"
    p.cache.invalidate(T)   # fresh numbers for the Stage-2 sections (no answer-cache replay)

    rule("15. MULTISTEP & CONDITIONAL REASONING — decomposed, every step governed")
    for q in ["what must a release achieve before promotion and which requirement has a traceability gap?",
              "if a critical defect is open, what happens to the release?",
              "compare the acceptance criteria in the test strategy and the release runbook"]:
        a = svc.ask(asker, q)
        r = a.reasoning or {}
        print(f"  [{r.get('mode','single'):11s}] {a.kind.value:7s} cx={a.complexity:7s} steps={len(r.get('steps', []))} "
              f"cites={len(a.citations)} model={a.model_name} tokens={a.tokens_in}/{a.tokens_out}")
        for st in r.get("steps", []):
            cond = "" if st.get("condition") is None else f" condition={st['condition']}"
            print(f"      {st['id']} {st['kind']:9s}{cond}{' SKIPPED' if st.get('skipped') else ''}: {st['question'][:70]}")
        print(f"      why: {r.get('explain','')[:120]}")

    rule("16. QUERY COMPLEXITY (simple / medium / complex) → MULTI-MODEL ROUTING, tokens in/out")
    p.cache.invalidate(T)
    mo = demo.principal_for(p, "meridian-health", "mo.asker")
    nia = demo.principal_for(p, "northwind-air", "nia.asker")
    for prin, q in [(mo, "what does triage category 1 require?"),
                    (nia, "which aircraft system was deferred?"),
                    (asker, "why does a component with an open defect block its dependent releases and what must a release achieve?")]:
        a = svc.ask(prin, q)
        print(f"  {a.complexity:7s} → tier={a.tier:5s} model={a.model_name:18s} tokens in/out={a.tokens_in}/{a.tokens_out} "
              f"cost=${a.cost:.6f} level={a.level}")

    rule("17. AUTHORITATIVE SOURCE — ranks per source, curator-marked docs win, conflicts flagged")
    print("  source ranks:", [(r["source"], r["rank"]) for r in _auth.list_ranks(p, T)])
    strat = p.documents.by_source_uri(T, "files", "file://qa/test-strategy.md")
    _auth.mark_authoritative(p, T, strat["id"], True, "carl.curator"); p.cache.invalidate(T)
    a = svc.ask(asker, "what must a release achieve before promotion?")
    card = a.authoritative_source or {}
    print(f"  authoritative for this answer: {card.get('document_title')} ({card.get('source')}) — {card.get('reason')}")
    print(f"  conflicts among cited sources: {len(card.get('conflicts', []))}")

    rule("18. DATA VERSIONING — history · diff · rollback · dataset versions · lineage")
    intake, worker = _Intake(p), _Worker(p, None); worker.intake = intake
    v2 = demo.CORPORA[T][0][4].replace("95% automated coverage", "97% automated coverage")
    intake.submit(intake.canonical(T, "files", "file://qa/test-strategy.md", "Test Strategy v3", v2.encode(), mime="text/markdown"))
    worker.drain()
    hist = _ver.history(p, T, strat["id"])
    print("  history:", [(h["version"], h["passages"]) for h in hist])
    d = _ver.diff(p, T, strat["id"], 1, 2)
    print(f"  diff v1→v2: +{len(d['added'])} −{len(d['removed'])} ={d['unchanged']} | added: {d['added'][0][:60] if d['added'] else ''}")
    rb = _ver.rollback(p, T, strat["id"], 1, "carl.curator"); p.cache.invalidate(T)
    print("  rollback to v1 →", rb)
    print("  dataset versions:", [(x["version"], x["reason"][:28]) for x in _ver.list_dataset_versions(p, T, 3)])
    pid = p.passages.by_document(T, strat["id"])[0].id
    print("  lineage of one passage:", {k: v for k, v in (_ver.lineage(p, T, pid) or {}).items() if k in ("version", "source", "content_hash")})

    rule("19. CONTINUOUS REFRESH — schedule · due · delta-only run · 7-stage run record · SLA health")
    t0 = 1_800_000_000.0
    _sched.set_schedule(p, T, "jira", 60, {"projects": ["REL"]}, enabled=True, now=t0)
    fresh = demo.JIRA_RECORDS[T] + [{"project": "REL", "key": "REL-777", "summary": "Refresh demo issue",
                                     "status": "Open", "updated": 999_999_999, "acl": ["public"],
                                     "description": "Picked up by the scheduled refresh; only the delta is ingested."}]
    ran = _sched.run_due(p, T, now=t0 + 61, records_by_source={"jira": fresh})
    for r in ran:
        print(f"  ran {r.get('source')}: pulled={r.get('pulled')} ingested={r.get('ingested')} "
              f"status={r.get('status') or r.get('last_status') or 'ok'} errors={r.get('error_count')} "
              f"next_run=+{int((r.get('next_run') or t0)-t0)}s run_id={str(r.get('run_id'))[:12]}")
    print("  health:", [{k: h[k] for k in ("source", "enabled", "last_status", "error_count", "sla_breach")} for h in _sched.health(p, T, now=t0 + 62)])
    last = _runs.list_runs(p, T, 1)[0]
    print("  last run steps:", [s["name"] for s in last["steps"]])

    rule("20. CURATOR — knowledge-base evaluation suggests what to delete / review / keep")
    intake.upload(T, "duplicate-strategy.md", demo.CORPORA[T][0][4].encode()); worker.drain()
    dq = _kb.data_quality(p, T)
    print("  data quality:", {k: dq[k] for k in ("coverage", "freshness", "contradictions", "gaps", "connectedness",
                                                  "traceability", "readability_avg", "duplicate_rate", "citation_coverage")})
    print("  suggestions:", dq["suggestions"], "| risk register:", [r["risk"] for r in dq["risk_register"]][:4])
    for doc in _kb.document_quality(p, T)[:3]:
        print(f"   {doc['suggestion'].upper():7s} score={doc['score']:.2f} {doc['title'][:34]:34s} — {'; '.join(doc['reasons'])[:80]}")

    rule("21. ADMIN — connector permissions, bulk delete, AWS readiness")
    _cadmin.disable(p, T, "github", "adar.admin")
    print("  github enabled after admin disable:", _cadmin.is_enabled(p, T, "github"), "| allow/scopes:", {k: _cadmin.get(p, T, 'github')[k] for k in ('allow', 'scopes')})
    ups = [d for d in p.documents.list(T) if d["source"] == "upload"]
    for d in ups:
        p.vindex.delete(T, p.passages.delete_document_passages(T, d["id"])); p.documents.tombstone(T, d["id"])
    print(f"  bulk delete by source='upload': removed {len(ups)} document(s); dataset v{_ver.bump_dataset(p, T, 'bulk delete')}")
    from knowledge_fabric.adapters import cloud as _cloud
    sel = _cloud.selection({})
    print("  adapter selection (env-driven, AWS parity):", {k: sel[k]["adapter"] for k in ("objectstore", "queue", "database")})
    print("  readiness: run `python3 scripts/doctor.py --target aws` for the exact AWS gap list")

    rule("DONE — Stage 2 complete: Connect → Understand → Decide → Answer, every capability shown, no cloud.")


if __name__ == "__main__":
    main()
