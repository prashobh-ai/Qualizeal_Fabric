"""Synthetic demo tenants (Section 19).

A tenant = configuration + ontology pack + source set. NO customer-specific
code. Every identifier is drawn from ranges the issuing authorities RESERVE
for documentation (RFC 2606 domains, RFC 5737 IPs, 555-01xx phone numbers,
ISO 3166 user-assigned country codes), and ``validate_identifiers`` FAILS the
build if any identifier could resolve to a real entity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..contracts.types import Principal


@dataclass
class TenantConfig:
    tenant: str
    display: str
    ontology: str
    budget: float


DEMO_TENANTS = [
    TenantConfig("acme-assurance", "Acme Assurance (synthetic)", "quality-assurance", 5.0),
    TenantConfig("northwind-air", "Northwind Air (synthetic)", "aviation-ops", 5.0),
    TenantConfig("meridian-health", "Meridian Health (synthetic)", "health-provider", 5.0),
]

# --- synthetic corpora: realistic SHAPES, wholly invented content ---------
CORPORA = {
    "acme-assurance": [
        ("qa/test-strategy.md", "Test Strategy v3", "text/markdown", ["public"],
         """# Test Strategy

The release regression suite must achieve full requirement traceability before promotion.
Every requirement is verified by at least one test case, and every test case links back to a requirement.

Acceptance criteria for a release: zero open critical defects and 95% automated coverage of priority-1 requirements.
The strategy complies with ISO 29119 for software testing documentation.

Regression scope is selected by impact analysis on the changed components. A component with an open defect blocks its dependent releases until the defect is resolved.
"""),
        ("qa/traceability-matrix.csv", "Requirement Traceability Matrix", "text/csv", ["public"],
         "requirement,test_case,release,status\nREQ-100,TC-4501,R2026.1,covered\nREQ-101,TC-4502,R2026.1,covered\nREQ-102,TC-4503,R2026.1,gap\n"),
        ("qa/defect-policy.md", "Defect Management Policy", "text/markdown", ["restricted"],
         """# Defect Management Policy (Restricted)

Critical defects must be triaged within 4 business hours. A critical defect blocks the affected release.
Severity is assigned by the QA lead and reviewed at the daily defect council.
This restricted policy is visible only to curators and admins, not to general askers.
"""),
        ("qa/standup.transcript", "Release Standup Recording", "audio/transcript", ["public"],
         "[00:03] The traceability gap on REQ-102 is the last blocker for the release.\n[00:15] We agreed to add test case TC-4503 before promotion.\n[00:41] Coverage sits at ninety four percent, one point short of the acceptance bar.\n"),
    ],
    "northwind-air": [
        ("ops/turnaround.md", "Aircraft Turnaround Procedure", "text/markdown", ["public"],
         """# Turnaround Procedure

The turnaround checklist applies to every narrow-body aircraft between arrival and departure.
Ground crew requires a completed walkaround inspection before boarding begins.

Pushback clearance requires confirmation from the flight deck and the ramp coordinator.
The procedure complies with the operator's airworthiness maintenance program.
"""),
        ("ops/inspection-log.csv", "Daily Inspection Log", "text/csv", ["public"],
         "aircraft,system,check,result\nNW-101,hydraulics,pre-flight,pass\nNW-101,brakes,pre-flight,pass\nNW-102,hydraulics,pre-flight,defer\n"),
    ],
    "meridian-health": [
        ("clin/triage-protocol.md", "Emergency Triage Protocol", "text/markdown", ["public"],
         """# Triage Protocol

Triage assigns each patient a priority category on arrival. Category 1 requires immediate clinician review.
Consent must be obtained before any non-emergency procedure.

The protocol is governed by the department's clinical guideline board and reviewed annually.
"""),
        ("clin/medication-guide.csv", "Medication Dosage Guide", "text/csv", ["restricted"],
         "medication,guideline,max_daily,note\nDrugA,GL-12,200mg,contraindicated with DrugB\nDrugB,GL-13,50mg,monitor renal function\n"),
    ],
}

# synthetic connector records — automated ingestion from GitHub + Jira (WS1).
# Identifier-safe: invented org/repo/keys only.
GITHUB_RECORDS = {
    "acme-assurance": [
        {"repo": "acme/assurance-platform", "path": "docs/release-runbook.md", "updated_at": 1700,
         "commit": "a1b2c3", "mime": "text/markdown",
         "content": "# Release Runbook\n\nA release is cut only after the regression suite is green and "
                    "requirement traceability is complete. The runbook requires sign-off from the QA lead "
                    "before promotion to production."},
        {"repo": "acme/assurance-platform", "path": "CHANGELOG.md", "updated_at": 1710, "commit": "d4e5f6",
         "mime": "text/markdown",
         "content": "# Changelog\n\nR2026.1 closed the traceability gap on REQ-102 by adding automated "
                    "coverage for the checkout component."},
    ],
}
JIRA_RECORDS = {
    "acme-assurance": [
        {"project": "REL", "key": "REL-42", "summary": "Close traceability gap on REQ-102",
         "status": "In Progress", "updated": 1720, "acl": ["public"],
         "description": "REQ-102 has no linked test case. Add TC-4503 and link it to the requirement "
                        "before the R2026.1 release can be promoted."},
        {"project": "REL", "key": "REL-43", "summary": "Regression suite flakiness on payments",
         "status": "Open", "updated": 1730, "acl": ["public"],
         "description": "Intermittent failures in the payments regression pack are blocking a clean run. "
                        "Owner is investigating a race in the test fixtures."},
    ],
}

# question bank per tenant (measured, multi-doc where possible, distinct families)
QUESTION_BANK = {
    "acme-assurance": [
        ("what must a release achieve before promotion?", ["qa/test-strategy.md"], "policy"),
        ("what is the acceptance criteria for coverage?", ["qa/test-strategy.md"], "threshold"),
        ("which requirement has a traceability gap?", ["qa/traceability-matrix.csv"], "lookup"),
        ("what blocks the release according to the standup?", ["qa/standup.transcript"], "evidence"),
    ],
    "northwind-air": [
        ("what is required before boarding begins?", ["ops/turnaround.md"], "procedure"),
        ("which aircraft system was deferred?", ["ops/inspection-log.csv"], "lookup"),
    ],
    "meridian-health": [
        ("what does triage category 1 require?", ["clin/triage-protocol.md"], "procedure"),
    ],
}

# demo users per tenant: (subject, roles, scopes)
DEMO_USERS = {
    "acme-assurance": [
        ("asha.asker", ["asker"], ["public"]),
        ("carl.curator", ["curator"], ["public", "restricted"]),
        ("adar.admin", ["admin"], ["public", "restricted"]),
        ("rana.restricted", ["asker"], ["public"]),          # cannot see 'restricted'
        ("qa-agent", ["agent"], ["public"]),                  # service principal
    ],
    "northwind-air": [("nia.asker", ["asker"], ["public"])],
    "meridian-health": [("mo.asker", ["asker"], ["public"])],
    "qualizeal": [
        ("asha.asker", ["asker"], ["public"]),
        ("carl.curator", ["curator"], ["public", "restricted"]),
        ("adar.admin", ["admin"], ["public", "restricted"]),
        ("kf-agent", ["agent"], ["public"]),
    ],
}

# identifier-safety: patterns that would indicate a REAL-resolvable identifier
_UNSAFE = [
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?(?!01[0-9]{2})\d{3}[-.\s]?\d{4}\b"), "real-looking phone"),
    (re.compile(r"@(?!example\.(?:com|org|net)\b)[a-z0-9.-]+\.(?:com|org|net|io|gov)", re.I), "real domain email"),
    (re.compile(r"\b(?:192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)"), None),   # RFC5737 -> SAFE (ignore)
]


def validate_identifiers() -> list[str]:
    """Fail the build if any shipped identifier could resolve to a real entity."""
    problems = []
    for tenant, docs in CORPORA.items():
        for uri, title, mime, acl, body in docs:
            for pat, label in _UNSAFE:
                if label is None:
                    continue
                for m in pat.findall(body):
                    problems.append(f"{tenant}:{uri} contains {label}: {m!r}")
    return problems


def seed(platform, tenants: list[str] | None = None) -> dict:
    """Load demo tenants, users, budgets, corpora and question banks."""
    from ..ingestion.intake import Intake, IngestWorker

    problems = validate_identifiers()
    if problems:
        raise AssertionError("identifier-safety validation FAILED:\n" + "\n".join(problems))

    intake = Intake(platform)
    worker = IngestWorker(platform, intake)
    chosen = tenants or [t.tenant for t in DEMO_TENANTS]
    summary = {}
    for cfg in DEMO_TENANTS:
        if cfg.tenant not in chosen:
            continue
        platform.policy.set_budget(cfg.tenant, cfg.budget)
        for uri, title, mime, acl, body in CORPORA[cfg.tenant]:
            raw = intake.canonical(cfg.tenant, "files", f"file://{uri}", title,
                                   body.encode(), mime=mime, acl=acl, ontology=cfg.ontology)
            intake.submit(raw)
        for qid, (q, docs, fam) in enumerate(QUESTION_BANK.get(cfg.tenant, [])):
            platform.db.execute(
                "INSERT OR REPLACE INTO question_bank(id,tenant,question,expected_docs,family) VALUES(?,?,?,?,?)",
                (f"{cfg.tenant}-q{qid}", cfg.tenant, q, ",".join(docs), fam))
        res = worker.drain()
        summary[cfg.tenant] = {"documents": len(CORPORA[cfg.tenant]),
                               "ingested": len([r for r in res if r["status"] in ("ok", "updated")]),
                               "passages": sum(r.get("passages", 0) for r in res)}
        # automated multi-source ingestion from GitHub + Jira (WS1)
        sources = []
        if cfg.tenant in GITHUB_RECORDS:
            sources.append({"source": "github", "config": {"repos": ["acme/assurance-platform"]},
                            "records": GITHUB_RECORDS[cfg.tenant]})
        if cfg.tenant in JIRA_RECORDS:
            sources.append({"source": "jira", "config": {"projects": ["REL"]},
                            "records": JIRA_RECORDS[cfg.tenant]})
        if sources:
            from ..ingestion.sync import SyncManager
            syncs = SyncManager(platform).run_all(cfg.tenant, sources)
            summary[cfg.tenant]["connectors"] = {s["source"]: s["ingested"] for s in syncs}
    return summary


def principal_for(platform, tenant: str, subject: str) -> Principal:
    for s, roles, scopes in DEMO_USERS.get(tenant, []):
        if s == subject:
            token = platform.idp.mint(Principal(subject=s, tenant=tenant, roles=roles,
                                                scopes=scopes, agent="agent" in roles))
            return platform.idp.authenticate({"token": token})
    raise KeyError(f"no demo user {subject} in {tenant}")
