"""Synthetic corpus for tests only (L0.2).

These fixture documents used to be seeded into the `qualizeal` fabric by
`tenants/demo.py::seed`. They are moved here so nothing synthetic ever
reaches the product fabric — documents enter `qualizeal` exclusively via
`make load-corpus`. Tests that need a populated fabric call
`load_into(platform, tenant)` with a **test-only** fabric id
(`TEST_FABRIC` = "test-fabric" by default).

Every identifier is drawn from ranges the issuing authorities reserve for
documentation; `validate_identifiers()` fails if any could resolve to a
real entity.
"""
from __future__ import annotations

import re

TEST_FABRIC = "test-fabric"

# One corpus, three ontology packs represented through the document mix.
CORPORA = [
    ("qa/test-strategy.md", "Test Strategy v3", "text/markdown", ["public"], "quality-assurance",
     """# Test Strategy

The release regression suite must achieve full requirement traceability before promotion.
Every requirement is verified by at least one test case, and every test case links back to a requirement.

Acceptance criteria for a release: zero open critical defects and 95% automated coverage of priority-1 requirements.
The strategy complies with ISO 29119 for software testing documentation.

Regression scope is selected by impact analysis on the changed components. A component with an open defect blocks its dependent releases until the defect is resolved.
"""),
    ("qa/traceability-matrix.csv", "Requirement Traceability Matrix", "text/csv", ["public"], "quality-assurance",
     "requirement,test_case,release,status\nREQ-100,TC-4501,R2026.1,covered\nREQ-101,TC-4502,R2026.1,covered\nREQ-102,TC-4503,R2026.1,gap\n"),
    ("qa/defect-policy.md", "Defect Management Policy", "text/markdown", ["restricted"], "quality-assurance",
     """# Defect Management Policy (Restricted)

Critical defects must be triaged within 4 business hours. A critical defect blocks the affected release.
Severity is assigned by the QA lead and reviewed at the daily defect council.
This restricted policy is visible only to curators and admins, not to general askers.
"""),
    ("qa/standup.transcript", "Release Standup Recording", "audio/transcript", ["public"], "quality-assurance",
     "[00:03] The traceability gap on REQ-102 is the last blocker for the release.\n[00:15] We agreed to add test case TC-4503 before promotion.\n[00:41] Coverage sits at ninety four percent, one point short of the acceptance bar.\n"),
    ("ops/turnaround.md", "Aircraft Turnaround Procedure", "text/markdown", ["public"], "aviation-ops",
     """# Turnaround Procedure

The turnaround checklist applies to every narrow-body aircraft between arrival and departure.
Ground crew requires a completed walkaround inspection before boarding begins.

Pushback clearance requires confirmation from the flight deck and the ramp coordinator.
The procedure complies with the operator's airworthiness maintenance program.
"""),
    ("ops/inspection-log.csv", "Daily Inspection Log", "text/csv", ["public"], "aviation-ops",
     "aircraft,system,check,result\nNW-101,hydraulics,pre-flight,pass\nNW-101,brakes,pre-flight,pass\nNW-102,hydraulics,pre-flight,defer\n"),
    ("clin/triage-protocol.md", "Emergency Triage Protocol", "text/markdown", ["public"], "health",
     """# Triage Protocol

Triage assigns each patient a priority category on arrival. Category 1 requires immediate clinician review.
Consent must be obtained before any non-emergency procedure.

The protocol is governed by the department's clinical guideline board and reviewed annually.
"""),
    ("clin/medication-guide.csv", "Medication Dosage Guide", "text/csv", ["restricted"], "health",
     "medication,guideline,max_daily,note\nDrugA,GL-12,200mg,contraindicated with DrugB\nDrugB,GL-13,50mg,monitor renal function\n"),
]

GITHUB_RECORDS = [
    {"repo": "qualizeal/kf-platform", "path": "docs/release-runbook.md", "updated_at": 1700,
     "commit": "a1b2c3", "mime": "text/markdown",
     "content": "# Release Runbook\n\nA release is cut only after the regression suite is green and "
                "requirement traceability is complete. The runbook requires sign-off from the QA lead "
                "before promotion to production."},
    {"repo": "qualizeal/kf-platform", "path": "CHANGELOG.md", "updated_at": 1710, "commit": "d4e5f6",
     "mime": "text/markdown",
     "content": "# Changelog\n\nR2026.1 closed the traceability gap on REQ-102 by adding automated "
                "coverage for the checkout component."},
]

JIRA_RECORDS = [
    {"project": "REL", "key": "REL-42", "summary": "Close traceability gap on REQ-102",
     "status": "In Progress", "updated": 1720, "acl": ["public"],
     "description": "REQ-102 has no linked test case. Add TC-4503 and link it to the requirement "
                    "before the R2026.1 release can be promoted."},
    {"project": "REL", "key": "REL-43", "summary": "Regression suite flakiness on payments",
     "status": "Open", "updated": 1730, "acl": ["public"],
     "description": "Intermittent failures in the payments regression pack are blocking a clean run. "
                    "Owner is investigating a race in the test fixtures."},
]

QUESTION_BANK = [
    ("what must a release achieve before promotion?", ["qa/test-strategy.md"], "policy"),
    ("what is the acceptance criteria for coverage?", ["qa/test-strategy.md"], "threshold"),
    ("which requirement has a traceability gap?", ["qa/traceability-matrix.csv"], "lookup"),
    ("what blocks the release according to the standup?", ["qa/standup.transcript"], "evidence"),
    ("how fast must critical defects be triaged?", ["qa/defect-policy.md"], "policy"),
    ("what is required before boarding begins?", ["ops/turnaround.md"], "procedure"),
    ("which aircraft system was deferred?", ["ops/inspection-log.csv"], "lookup"),
    ("what does triage category 1 require?", ["clin/triage-protocol.md"], "procedure"),
]

_UNSAFE = [
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?(?!01[0-9]{2})\d{3}[-.\s]?\d{4}\b"), "real-looking phone"),
    (re.compile(r"@(?!example\.(?:com|org|net)\b)[a-z0-9.-]+\.(?:com|org|net|io|gov)", re.I), "real domain email"),
    (re.compile(r"\b(?:192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)"), None),   # RFC5737 -> SAFE
]


def validate_identifiers() -> list[str]:
    """Fail if any fixture identifier could resolve to a real entity."""
    problems = []
    for uri, title, mime, acl, ontology, body in CORPORA:
        for pat, label in _UNSAFE:
            if label is None:
                continue
            for m in pat.findall(body):
                problems.append(f"{uri} contains {label}: {m!r}")
    return problems


def _register_vertical_packs() -> None:
    """The fixture seeds documents in the aviation and health domains, so it
    registers those ontology packs (the shipped package carries only the
    quality-assurance pack — L0.2)."""
    from knowledge_fabric.ontology.packs import OntologyPack, register
    register(OntologyPack(
        name="aviation-ops", version=1,
        entity_types=["Aircraft", "Procedure", "Checklist", "System", "Regulation", "Airport"],
        relation_types=["applies_to", "requires", "precedes", "governed_by", "located_at"],
        typed_facts={"procedure_step": ["Procedure", "System", "Aircraft"]},
        salient_vocab={
            "procedure": 1.0, "checklist": 0.9, "airworthiness": 1.0, "inspection": 0.9,
            "maintenance": 0.9, "regulation": 0.9, "clearance": 0.7, "turnaround": 0.8,
            "boarding": 0.6, "taxi": 0.6, "runway": 0.7, "compliance": 0.9,
        },
        entity_lexicon={
            "aircraft": "Aircraft", "procedure": "Procedure", "checklist": "Checklist",
            "system": "System", "regulation": "Regulation", "airport": "Airport",
        },
    ))
    register(OntologyPack(
        name="health", version=1,
        entity_types=["Policy", "Procedure", "Patient", "Medication", "Guideline", "Department"],
        relation_types=["indicated_for", "contraindicated_with", "governed_by", "administered_by"],
        typed_facts={"dosage": ["Medication", "Patient", "Guideline"]},
        salient_vocab={
            "protocol": 1.0, "dosage": 1.0, "contraindication": 1.0, "guideline": 0.9,
            "consent": 0.9, "triage": 0.8, "discharge": 0.7, "medication": 0.9,
            "policy": 0.7, "procedure": 0.7, "compliance": 0.8,
        },
        entity_lexicon={
            "policy": "Policy", "procedure": "Procedure", "medication": "Medication",
            "guideline": "Guideline", "department": "Department",
        },
    ))


def load_isolation(platform, tenant: str) -> None:
    """Seed a minimal two-document corpus (no bank, no connectors) into a
    second tenant, used only to exercise cross-tenant isolation (I5)."""
    from knowledge_fabric.ingestion.intake import Intake, IngestWorker
    _register_vertical_packs()
    platform.policy.set_budget(tenant, 5.0)
    intake = Intake(platform)
    worker = IngestWorker(platform, intake)
    for uri, title, mime, acl, ontology, body in CORPORA:
        if not uri.startswith("ops/"):
            continue
        intake.submit(intake.canonical(tenant, "files", f"file://{uri}", title,
                                       body.encode(), mime=mime, acl=acl, ontology=ontology))
    worker.drain()


def load_into(platform, tenant: str = TEST_FABRIC, *, with_connectors: bool = True) -> dict:
    """Seed the synthetic corpus, question bank and (optionally) connectors
    into a test-only fabric. Returns a summary dict."""
    from knowledge_fabric.ingestion.intake import Intake, IngestWorker

    problems = validate_identifiers()
    if problems:
        raise AssertionError("identifier-safety validation FAILED:\n" + "\n".join(problems))
    _register_vertical_packs()

    platform.policy.set_budget(tenant, 5.0)
    intake = Intake(platform)
    worker = IngestWorker(platform, intake)
    for uri, title, mime, acl, ontology, body in CORPORA:
        raw = intake.canonical(tenant, "files", f"file://{uri}", title,
                               body.encode(), mime=mime, acl=acl, ontology=ontology)
        intake.submit(raw)
    for qid, (q, docs, fam) in enumerate(QUESTION_BANK):
        platform.db.execute(
            "INSERT OR REPLACE INTO question_bank(id,tenant,question,expected_docs,family) VALUES(?,?,?,?,?)",
            (f"{tenant}-q{qid}", tenant, q, ",".join(docs), fam))
    res = worker.drain()
    summary = {"documents": len(CORPORA),
               "ingested": len([r for r in res if r["status"] in ("ok", "updated")]),
               "passages": sum(r.get("passages", 0) for r in res)}
    if with_connectors:
        from knowledge_fabric.ingestion.sync import SyncManager
        sources = [
            {"source": "github", "config": {"repos": ["qualizeal/kf-platform"]},
             "records": GITHUB_RECORDS},
            {"source": "jira", "config": {"projects": ["REL"]}, "records": JIRA_RECORDS},
        ]
        syncs = SyncManager(platform).run_all(tenant, sources)
        summary["connectors"] = {s["source"]: s["ingested"] for s in syncs}
    return summary
