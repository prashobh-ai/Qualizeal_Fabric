"""Audience coverage matrix (T83) — the completeness check.

One test and one Admin panel that prove every audience is served across every
data type. The matrix is data type (rows) × persona (columns); each ✓ cell is a
coverage cell with test questions in ``eval/sets/coverage.jsonl``.

``evaluate`` runs each cell's questions through the ONE governed answer path
(``AnswerService.ask``) as that persona, and grades the cell:

* **green**  — every question is answered, grounded and cited;
* **amber**  — some questions answer, some do not (weak);
* **coral**  — a held data type answered none (a real coverage failure);
* **n/a**    — the fabric does not hold that data type, so the cell is not gated.

To grade deterministically the way ``eval/fixture.py`` derives ``facts.jsonl``,
this module ships a small **self-contained coverage corpus**: one public
document per data type, whose content every ``coverage.jsonl`` question grounds
in. ``build_fabric`` ingests it; ``held`` then reports exactly the data types
that corpus put in the fabric, so the gate — *no coral cell for a held data
type* — is exact and self-contained.

Writes ``data/coverage.json`` (the matrix the Admin heatmap renders) and exits
non-zero on any coral held cell.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

COVERAGE_TENANT = "coverage-fabric"

# --------------------------------------------------------------------------
# The matrix. Data types are the rows (ordered as the article's grid); each
# maps to the personas (columns) that should reach it — the ✓ cells.
# --------------------------------------------------------------------------
PERSONAS = ["business", "developer", "tester", "architect", "curator", "hr"]

DATA_TYPES = [
    "website",
    "products",
    "case_studies",
    "code",
    "activity",
    "architecture",
    "dependencies",
    "jira",
    "confluence",
    "tables",
    "documents",
    "images",
    "hr",
]

DATA_TYPE_LABEL = {
    "website": "Website / services",
    "products": "Products",
    "case_studies": "Case studies / recognitions",
    "code": "Code (repos, symbols, comments)",
    "activity": "PRs / commits / contributors",
    "architecture": "Architecture docs / ADRs",
    "dependencies": "Dependencies / licences",
    "jira": "Jira (issues, status, sprint)",
    "confluence": "Confluence pages",
    "tables": "Excel / CSV tables",
    "documents": "Word / PDF / PowerPoint",
    "images": "Images / diagrams",
    "hr": "HR / policy / culture",
}

MATRIX: dict[str, list[str]] = {
    "website": ["business", "curator", "hr"],
    "products": ["business", "developer", "tester", "architect", "curator"],
    "case_studies": ["business", "architect", "curator"],
    "code": ["developer", "tester", "architect", "curator"],
    "activity": ["business", "developer", "tester", "architect", "curator"],
    "architecture": ["developer", "tester", "architect", "curator"],
    "dependencies": ["developer", "architect", "curator"],
    "jira": ["business", "developer", "tester", "architect", "curator"],
    "confluence": ["business", "developer", "tester", "architect", "curator", "hr"],
    "tables": ["business", "developer", "tester", "curator", "hr"],
    "documents": ["business", "developer", "tester", "architect", "curator", "hr"],
    "images": ["developer", "architect", "curator"],
    "hr": ["business", "curator", "hr"],
}

# Persona (column) → an organisational designation to ask as, so the answer is
# conditioned for that audience (T27). The grounding never depends on it.
AUDIENCE_DESIGNATION = {
    "business": "Delivery Manager",
    "developer": "Senior Developer",
    "tester": "QA Engineer",
    "architect": "Software Architect",
    "curator": "Knowledge Curator",
    "hr": "People Operations",
}

# --------------------------------------------------------------------------
# The self-contained coverage corpus: one public document per data type. Every
# coverage.jsonl question grounds in exactly one of these, so the gate is exact
# and needs no live source (the eval/fixture.py pattern, for the answer path).
# --------------------------------------------------------------------------
#
# Each document carries a title token that is DISTINCTIVE across the corpus
# (services, QMentisAI, airline, bm25, runbook, architecture, dependencies,
# REL-42, onboarding, matrix, strategy, diagram, leave). Every coverage question
# names its document's distinctive token, so the subject boost lifts the right
# document and the answer cites it — coverage is graded on "the right data type
# answered", not a brittle substring.
DOCS: list[tuple[str, str, str, str, str]] = [
    (
        "website",
        "web://qualizeal/services",
        "QualiZeal Services",
        "text/markdown",
        "# QualiZeal Services\n\nQualiZeal offers independent quality engineering services: "
        "accessibility testing, performance testing, automation and advisory. The services page "
        "states that accessibility testing audits a product against WCAG and returns a "
        "prioritised remediation plan.\n",
    ),
    (
        "products",
        "file://products/qmentisai",
        "QMentisAI Product",
        "text/markdown",
        "# QMentisAI\n\nQMentisAI is the AI testing platform. QMentisAI evaluates an AI system for "
        "grounding, hallucination and drift, and reports a trust score per release.\n",
    ),
    (
        "case_studies",
        "file://case-studies/airline",
        "Airline Case Study",
        "text/markdown",
        "# Airline Case Study\n\nThe airline case study reports that a global airline engaged "
        "QualiZeal to modernise its regression suite, that automated coverage rose to ninety five "
        "percent, and that release time halved.\n",
    ),
    (
        "code",
        "github://qualizeal/kf-platform/retrieve.py",
        "bm25 Retrieval Module",
        "text/markdown",
        "# bm25 Retrieval Module\n\nThe bm25 retrieval module defines the function bm25_search, "
        "which ranks passages by lexical overlap before dense reranking. bm25_search is called by "
        "the answer service on every query.\n",
    ),
    (
        "activity",
        "github://qualizeal/kf-platform/release-runbook.md",
        "Release Runbook",
        "text/markdown",
        "# Release Runbook\n\nThe release runbook states that a release is cut only after the "
        "regression suite is green and requirement traceability is complete. The runbook requires "
        "sign-off from the QA lead before a pull request is merged to the production branch.\n",
    ),
    (
        "architecture",
        "file://architecture/kf-platform",
        "Platform Architecture",
        "text/markdown",
        "# Platform Architecture\n\nThe platform architecture separates ingestion, retrieval and "
        "an answer service. The architecture has the answer service compose cited answers and "
        "record telemetry, keeping retrieval independent of composition.\n",
    ),
    (
        "dependencies",
        "file://dependencies/kf-platform",
        "Platform Dependencies",
        "text/markdown",
        "# Platform Dependencies\n\nThe platform dependencies are fastapi and pydantic, both under "
        "the permissive MIT licence. No copyleft dependency is present, so the licence posture is "
        "permissive.\n",
    ),
    (
        "jira",
        "jira://REL/REL-42",
        "REL-42 Traceability Gap",
        "text/markdown",
        "# REL-42\n\nJira issue REL-42 is in progress: close the traceability gap on REQ-102 by "
        "adding test case TC-4503 before the release is promoted. The priority of REL-42 is "
        "high.\n",
    ),
    (
        "confluence",
        "confluence://ENG/onboarding",
        "Engineering Onboarding",
        "text/markdown",
        "# Engineering Onboarding\n\nThe engineering onboarding page explains how a new engineer "
        "sets up the platform, runs the test suite, and requests access. New engineers complete "
        "onboarding in the first week.\n",
    ),
    (
        "tables",
        "file://tables/traceability-matrix.csv",
        "Traceability Matrix",
        "text/csv",
        "requirement,test_case,release,status\nREQ-100,TC-4501,R2026.1,covered\n"
        "REQ-101,TC-4502,R2026.1,covered\nREQ-102,TC-4503,R2026.1,gap\n",
    ),
    (
        "documents",
        "file://docs/test-strategy",
        "Test Strategy",
        "text/markdown",
        "# Test Strategy\n\nThe test strategy states that the release regression suite must "
        "achieve full requirement traceability before promotion. The test strategy sets the "
        "acceptance criteria for coverage at ninety five percent automated coverage of priority-1 "
        "requirements.\n",
    ),
    (
        "images",
        "file://images/data-flow-diagram",
        "Data Flow Diagram",
        "text/markdown",
        "# Data Flow Diagram\n\nThe data flow diagram shows ingestion feeding a retriever, and the "
        "retriever feeding an answer service. The data flow diagram is the canonical picture of "
        "how a question moves through the platform.\n",
    ),
    (
        "hr",
        "file://hr/leave-policy",
        "Leave Policy",
        "text/markdown",
        "# Leave Policy\n\nThe leave policy grants every employee twenty four days of annual "
        "leave. The leave policy states that leave is requested through the people portal and "
        "approved by the reporting manager.\n",
    ),
]

_TITLE_TYPE = {title: dt for dt, _uri, title, *_ in DOCS}
#: the document title that a data type's answer should cite — coverage is graded
#: on the right data type answering, not on a substring.
DATA_TYPE_TITLE = {dt: title for dt, _uri, title, *_ in DOCS}


def build_fabric(platform, tenant: str = COVERAGE_TENANT) -> set[str]:
    """Ingest the coverage corpus into ``tenant``; return the data types loaded
    (what the fabric now *holds*, so ``held`` is exact)."""
    from knowledge_fabric.ingestion.intake import IngestWorker, Intake

    platform.policy.set_budget(tenant, 5.0)
    intake = Intake(platform)
    worker = IngestWorker(platform, intake)
    for _dt_slug, uri, title, mime, body in DOCS:
        intake.submit(
            intake.canonical(tenant, "files", uri, title, body.encode(), mime=mime, acl=["public"])
        )
    worker.drain()
    return held_types(platform, tenant)


def held_types(platform, tenant: str) -> set[str]:
    """The data types the fabric holds, by matching each document's title to the
    coverage corpus (titles are stored verbatim). A general fabric (no coverage
    corpus) holds none, so its cells read n/a rather than coral."""
    out: set[str] = set()
    for d in platform.documents.list(tenant):
        dt = _TITLE_TYPE.get(d.get("title") or "")
        if dt:
            out.add(dt)
    return out


def load_questions() -> list[dict]:
    """Rows of ``eval/sets/coverage.jsonl``."""
    p = os.path.join(ROOT, "eval", "sets", "coverage.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _principal(platform, tenant: str, audience: str, designation: str):
    """A public asker conditioned as ``designation`` (T27). Each audience gets a
    distinct subject so the per-subject rate limiter is never the reason a cell
    fails — coverage grades reachability, not throughput."""
    from knowledge_fabric.tenants import demo

    prin = demo.principal_for(platform, tenant, "asker.public")
    prin.subject = f"coverage-{audience}"
    prin.designation = designation
    return prin


def _grade_question(svc, prin, row: dict) -> dict:
    """A cell question passes when the fabric answers it (kind ANSWER) and the
    answer cites the data type's own document — i.e. that data type actually
    served this audience, not a lookalike."""
    from knowledge_fabric.contracts.types import AnswerKind

    a = svc.ask(prin, row["question"])
    want_title = DATA_TYPE_TITLE.get(row["data_type"], "")
    cited_titles = {c.document_title for c in a.citations}
    ok = a.kind == AnswerKind.ANSWER and bool(a.citations) and want_title in cited_titles
    return {
        "id": row.get("id", ""),
        "question": row["question"],
        "ok": bool(ok),
        "kind": a.kind.value,
        "cited": sorted(cited_titles)[:3],
        "level": a.level,
    }


def evaluate(platform, svc, tenant: str, held: set[str], rows: list[dict] | None = None) -> dict:
    """Grade every ✓ cell. Held cells are asked through the governed path as the
    persona; cells whose data type the fabric does not hold are n/a."""
    rows = rows if rows is not None else load_questions()
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r["data_type"], []).append(r)

    prins = {
        aud: _principal(platform, tenant, aud, desig) for aud, desig in AUDIENCE_DESIGNATION.items()
    }
    matrix, tally = [], {"green": 0, "amber": 0, "coral": 0, "na": 0}
    for dt in DATA_TYPES:
        is_held = dt in held
        cells = {}
        for aud in MATRIX[dt]:
            qs = [r for r in by_type.get(dt, []) if aud in (r.get("personas") or [])]
            if not is_held:
                cells[aud] = {"status": "na", "n": len(qs), "passed": 0, "questions": []}
                tally["na"] += 1
                continue
            graded = [_grade_question(svc, prins[aud], r) for r in qs]
            passed = sum(1 for g in graded if g["ok"])
            if not qs:
                status = "na"
            elif passed == len(qs):
                status = "green"
            elif passed == 0:
                status = "coral"
            else:
                status = "amber"
            cells[aud] = {"status": status, "n": len(qs), "passed": passed, "questions": graded}
            tally[status if status != "na" else "na"] += 1
        matrix.append(
            {"data_type": dt, "label": DATA_TYPE_LABEL[dt], "held": is_held, "cells": cells}
        )

    coral_held = [
        f"{m['data_type']}/{aud}"
        for m in matrix
        for aud, c in m["cells"].items()
        if m["held"] and c["status"] == "coral"
    ]
    return {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "tenant": tenant,
        "personas": PERSONAS,
        "data_types": [{"key": k, "label": DATA_TYPE_LABEL[k]} for k in DATA_TYPES],
        "matrix": matrix,
        "summary": tally,
        "coral_held": coral_held,
        "passed": not coral_held,
    }


def run(model_mode: str = "extractive") -> dict:
    """Build a fresh in-memory coverage fabric, ingest the corpus, grade, return
    the report. Self-contained — no live source, no persisted state."""
    os.environ.setdefault("KF_MODEL_MODE", model_mode)
    from knowledge_fabric.answer.service import AnswerService
    from knowledge_fabric.app import Platform

    p = Platform(
        db_path=":memory:", blob_root=os.path.join(tempfile.mkdtemp(prefix="kf-coverage-"), "blobs")
    )
    from knowledge_fabric.tenants import demo

    demo.seed(p)
    held = build_fabric(p, COVERAGE_TENANT)
    return evaluate(p, AnswerService(p), COVERAGE_TENANT, held)


def format_report(rep: dict) -> str:
    s = rep["summary"]
    lines = [
        f"Coverage matrix: {'PASS' if rep['passed'] else 'FAIL'} — "
        f"{s['green']} green · {s['amber']} amber · {s['coral']} coral · {s['na']} n/a"
    ]
    for m in rep["matrix"]:
        cells = " ".join(f"{aud}:{c['status'][0].upper()}" for aud, c in m["cells"].items())
        held = "" if m["held"] else " (not held)"
        lines.append(f"  {m['label']:34s} {cells}{held}")
    if rep["coral_held"]:
        lines.append("  coral (held) cells: " + ", ".join(rep["coral_held"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="coverage")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--model", choices=["real", "extractive"], default="extractive")
    args = ap.parse_args(argv)
    rep = run("real" if args.model == "real" else "extractive")

    from knowledge_fabric import fabric_data as fd
    from knowledge_fabric.telemetry import api_ledger

    fd.write_json(fd.data_path("coverage.json", mkdir=True), rep)
    print(json.dumps(rep, indent=2) if args.json else format_report(rep))
    api_ledger.append_step_summary(format_report(rep).splitlines()[0])
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
