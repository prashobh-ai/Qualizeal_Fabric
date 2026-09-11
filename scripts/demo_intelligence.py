"""Knowledge-intelligence demo (T62) — the T51–T60 capstone.

`scripts/demo_answering.py` narrates the *answering* track (T24–T33). This demo
narrates the *knowledge-intelligence* track built on top of it as one story a
stakeholder can watch end to end:

  T51 answer galaxy   — the concepts an answer lit, and their one-hop halo
  T52 honest provider — which model answered, named, never faked
  T56 phase timing    — where the answer's wall-clock went (active vs idle)
  T53 curation gate   — a manual-mode document held in review never answers,
                        until a curator accepts it — then it does
  T54 timeline        — the ingestion log, by month
  T57 graph insights  — communities, surprising links and knowledge gaps
  T55 token-meter     — efficiency, cost by phase, provider quota
  T60 MCP             — the same insights through governed agent tools

It runs on the model-free extractive path (KF_MODEL_MODE=extractive, the
deployed floor), so it is deterministic, needs no credits, and the "provider"
line honestly reads *extractive core* rather than pretending a model ran.

Run:  python scripts/demo_intelligence.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "extractive")  # deterministic floor

from knowledge_fabric import curation  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.health import galaxy, graph_insights  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.mcp import server as mcp  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402

TENANT = "qualizeal"

# Two clusters joined by a bridge, each document multi-paragraph so concept
# pairs co-occur across passages and the graph forms real edges: a PLATFORM
# cluster (Fabric platform, QMentisAI, ValidAIte, single sign-on, retrieval)
# and a DELIVERY cluster (release train, regression suite, defect triage), with
# one bridge document connecting the platform's telemetry to the release train.
_PLATFORM = (
    "The Fabric platform provides retrieval and a knowledge graph.\n\n"
    "QMentisAI runs on the Fabric platform and uses single sign-on.\n\n"
    "ValidAIte runs on the Fabric platform and uses single sign-on.\n\n"
    "The Fabric platform enforces single sign-on for every service."
)
_DELIVERY = (
    "The release train ships every fortnight after the regression suite passes.\n\n"
    "Defect triage feeds the release train and blocks a failing regression suite.\n\n"
    "The regression suite runs against staging before the release train departs.\n\n"
    "Defect triage reviews every regression suite failure."
)
_DOCS = [
    ("internal", "internal://q/fabric.md", "Fabric platform", _PLATFORM),
    (
        "internal",
        "internal://q/qmentis.md",
        "QMentisAI",
        "QMentisAI is a test-intelligence platform on the Fabric platform.\n\n"
        "QMentisAI uses the Fabric platform for retrieval and single sign-on.\n\n"
        "QMentisAI and ValidAIte share the Fabric platform knowledge graph.",
    ),
    (
        "internal",
        "internal://q/validaite.md",
        "ValidAIte",
        "ValidAIte is a validation harness on the Fabric platform.\n\n"
        "ValidAIte uses the Fabric platform for retrieval and single sign-on.\n\n"
        "ValidAIte grades model outputs using the Fabric platform.",
    ),
    ("delivery", "delivery://release.md", "Release train", _DELIVERY),
    (
        "delivery",
        "delivery://triage.md",
        "Defect triage",
        "Defect triage tracks every regression suite failure before the release train.\n\n"
        "Defect triage escalates a blocked regression suite to the release train.\n\n"
        "Defect triage and the regression suite gate the release train.",
    ),
    (
        "internal",
        "internal://q/bridge.md",
        "Fabric telemetry",
        "The Fabric platform telemetry feeds the release train dashboard.\n\n"
        "The release train reads Fabric platform telemetry for each regression suite run.\n\n"
        "Fabric platform telemetry links retrieval health to the release train.",
    ),
]


def rule(t: str) -> None:
    print("\n" + "═" * 78 + f"\n▐ {t}\n" + "═" * 78)


def _seed() -> AnswerService:
    from knowledge_fabric.app import Platform

    p = Platform(db_path=":memory:", blob_root="./data/demo-blobs")
    demo.seed(p)
    intake = Intake(p)
    worker = IngestWorker(p, intake)
    for source, uri, title, body in _DOCS:
        intake.submit(
            intake.canonical(
                TENANT, source, uri, title, body.encode(), mime="text/markdown", acl=["public"]
            )
        )
    worker.drain()
    return p, AnswerService(p)


def main() -> int:
    p, svc = _seed()
    who = demo.principal_for(p, TENANT, "asker.public")

    rule("T51 / T52 / T56 — one answer: galaxy, honest provider, phase timing")
    a = svc.ask(who, "What is the Fabric platform?")
    print(f"  answer: {a.answer_text[:90]}…")
    print(f"  cited : {len(a.citations)} source(s); model: {a.model_name or 'extractive core'}")
    prov = mcp.tool_provider_status(p, TENANT)["result"]
    print(f"  provider (honest): {prov['provider']} · available={prov['available']} "
          f"· fallback={prov['fallback']}")
    gx = galaxy.build_payload(p, TENANT, a.trajectory_id)
    print(f"  galaxy: {gx['stats']['nodes']} concepts, {gx['stats']['edges']} links; "
          f"{len(gx['activated_ids'])} lit, {len(gx['halo_ids'])} in the halo")
    if a.timing:
        t = a.timing
        print(f"  timing: active {t['active_ms']}ms · idle {t['idle_ms']}ms · "
              f"phases {', '.join(f'{k} {round(v)}ms' for k, v in t['phase_ms'].items())}")

    rule("T53 — a manual-review document never reaches an asker, until accepted")
    curation.set_mode(p, TENANT, "wiki", "manual")
    intake = Intake(p)
    intake.submit(
        intake.canonical(
            TENANT, "wiki", "wiki://secret.md", "Project Zephyr",
            b"# Project Zephyr\n\nProject Zephyr is an unreleased internal initiative "
            b"on the Fabric platform. Details are confidential.\n",
            mime="text/markdown", acl=["public"],
        )
    )
    res = IngestWorker(p, intake).drain()[-1]
    doc = p.documents.get(TENANT, res["document_id"])
    out = curation.on_ingest(p, TENANT, doc, "wiki")
    held = svc.ask(who, "What is Project Zephyr?")
    print(f"  ingested under manual mode → state '{out['state']}' (review queue)")
    print(f"  asked while in review → kind '{held.kind.value}', "
          f"Zephyr in answer: {'zephyr' in held.answer_text.lower()}, "
          f"cited: {len(held.citations)}")
    curation.accept(p, TENANT, out["review_id"], actor="curator")
    now = svc.ask(who, "What is Project Zephyr?")
    print(f"  curator accepts → asked again → cites the document: "
          f"{doc['id'] in {c.document_id for c in now.citations}}")

    rule("T54 — the ingestion timeline")
    tl = curation.timeline(p, TENANT)
    active = [(m['month'], sum(m[k] for k in ('ingested', 'accepted', 'auto_kept')))
              for m in tl['months']]
    active = [x for x in active if x[1]]
    print(f"  year {tl['year']}: {len(tl['rows'])} events across "
          f"{len(active)} month(s); {active}")

    rule("T57 — knowledge-graph insights")
    ins = graph_insights.insights(p, TENANT)
    comm = [c for c in ins['communities'] if c.get('size', 0) > 1]
    print(f"  communities: {len(ins['communities'])} ({len(comm)} with >1 concept)")
    for c in comm[:3]:
        print(f"    • {(c.get('labels') or ['?'])[0]} — {c['size']} concepts, "
              f"cohesion {c['cohesion']:.2f}{' [thin]' if c.get('flag') else ''}")
    print(f"  surprising cross-domain links: {len(ins['surprising'])}")
    print(f"  knowledge gaps: {len(ins['gaps'])}")
    for g in ins['gaps'][:3]:
        print(f"    • {g['kind']}: {g.get('label', '')} "
              f"(tags: {', '.join(g.get('suggest_tags', []))})")

    rule("T55 / T60 — token-meter + the same insights through governed MCP tools")
    from knowledge_fabric.telemetry import insights as tmeter

    ov = tmeter.overview(7)
    eff = ov["efficiency"].get("efficiency", 0.0)
    print(f"  token-meter: efficiency {round(eff * 100)}% · provider quota "
          f"{ov['provider_quota'].get('provider')} ({ov['provider_quota'].get('limit')})")
    fc = mcp.tool_fabric_communities(p, TENANT)["result"]["communities"]
    kg = mcp.tool_knowledge_gaps(p, TENANT)["result"]
    print(f"  MCP fabric_communities → {len(fc)} communities; "
          f"knowledge_gaps → {len(kg['gaps'])} gaps, {len(kg['surprising'])} surprising")

    rule("DONE — T51–T60: galaxy, honest fallback, curation gate, timeline, "
         "graph insights, token-meter and MCP — deterministic, no credits.")

    # A hard smoke gate: the review item must have been held, then answerable.
    held_cited = doc["id"] in {c.document_id for c in held.citations}
    accepted_ok = doc["id"] in {c.document_id for c in now.citations}
    return 0 if (not held_cited and accepted_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
