"""Knowledge-Fabric v2 capstone demo (T102) — the KF-Instructions narrative.

One coherent, keyless story a stakeholder watches end to end — every step on the
governed answer path, no credits, nothing leaving the app:

  T91/T92  keyless answer     — "what is QMentisAI" → a fluent, cited answer
                                labelled Open-source LLM, with tokens
  T93      citation expand    — the cited passage expands to its paragraph
  T94      code snippet       — "show the login implementation" → prose + a
                                fenced block with a line-anchored link
  T97      Jira board         — "how many tasks are in progress on the V1 board"
                                → the exact count from the board's columns, fresh
  T99      cross-source       — "V1-42 shows complete, check the repo and verify"
                                → a two-source answer citing Jira and the repo
  T96      ROI + OTel         — hours saved / cost avoided / ROI ratio, a trace
                                waterfall, and the input/thinking/output split
  T100     galaxy             — the answer's concepts, ready to flash and fit

Runs on the model-free extractive floor (deterministic, no provider), so it
shows exactly what ships. Returns a step→result summary for the smoke test.

Run:  python scripts/demo_v2.py
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "extractive")
os.environ.setdefault("KF_DATA_ROOT", tempfile.mkdtemp(prefix="kf-demo-v2-"))

from knowledge_fabric import fabric_data as fd  # noqa: E402
from knowledge_fabric import relationships as relmod  # noqa: E402
from knowledge_fabric.answer import aggregate  # noqa: E402
from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.contracts.types import RawItem  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.telemetry import insights, roi  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402

TENANT = "qualizeal"

_DOCS = [
    RawItem(
        tenant=TENANT,
        source="internal",
        source_version="1",
        uri="internal://q/qmentis.md",
        mime="text/markdown",
        title="QMentisAI",
        bytes_=(
            b"# QMentisAI\n\nQMentisAI is an AI test-intelligence platform for quality "
            b"engineering. It grades model outputs and finds gaps.\n\nQMentisAI pricing is "
            b"usage-based, billed per test run.\n\nQMentisAI serves QA engineers and testers.\n"
        ),
        meta={"acl": ["public"], "as_of": "2026-09-12T08:00:00Z"},
    ),
    RawItem(
        tenant=TENANT,
        source="github_live",
        source_version="1",
        uri="github://acme/app/blob/login.py",
        mime="text/x-python",
        title="login.py",
        bytes_=(
            b"def login(user, password):\n"
            b'    """Authenticate a user and return a session token."""\n'
            b"    token = verify_password(user, password)\n"
            b"    return issue_token(token)\n"
        ),
        meta={
            "acl": ["public"],
            "citation_url": "https://github.com/acme/app/blob/HEAD/login.py",
            "as_of": "2026-09-12T08:00:00Z",
        },
    ),
    RawItem(
        tenant=TENANT,
        source="jira_live",
        source_version="1",
        uri="jira://V1/V1-42",
        mime="text/markdown",
        title="V1-42 · retry logic for the ingest worker",
        bytes_=b"# V1-42\n\nAdd exponential backoff to the ingest worker retry path.",
        meta={
            "acl": ["public"],
            "source_kind": "jira",
            "citation_url": "https://qualizeal-team-aicoe.atlassian.net/browse/V1-42",
            "as_of": "2026-09-12T08:00:00Z",
            "jira": {"status": "Done", "type": "Task", "assignee": "Alice"},
        },
    ),
    RawItem(
        tenant=TENANT,
        source="github_live",
        source_version="1",
        uri="github://acme/app/commits/abc123def456",
        mime="text/markdown",
        title="Fix retry logic",
        bytes_=b"Fix retry logic\n\nImplements the exponential backoff from V1-42.",
        meta={
            "acl": ["public"],
            "source_kind": "github",
            "citation_url": "https://github.com/acme/app/commit/abc123def456",
            "as_of": "2026-09-12T08:00:00Z",
        },
    ),
]

# The V1 Platform board facts (as T97's sync would write), so the in-progress
# count answers from the board's own column definition.
_JIRA_FACTS = {
    "jira_projects": {
        "V1": {
            "issues": {
                "total": 10,
                "by_status": {"To Do": 2, "In Progress": 3, "In Review": 1, "Done": 4},
                "by_type": {"Task": 8, "Bug": 2},
                "by_priority": {"High": 4, "Medium": 6},
                "by_assignee": {"Alice": 6, "Bob": 4},
            },
            "board": {
                "id": 34,
                "name": "V1 Platform",
                "columns": [
                    {"name": "To Do", "statuses": ["To Do"]},
                    {"name": "In Progress", "statuses": ["In Progress", "In Review"]},
                    {"name": "Done", "statuses": ["Done"]},
                ],
            },
            "sprint": {"name": "V1 Sprint 7", "state": "active", "start": "", "end": ""},
            "as_of": "2026-09-12T08:00:00Z",
        }
    }
}


def _p(out, label, text):
    out(f"\n\033[1m{label}\033[0m\n{text}")


def build(platform):
    intake = Intake(platform)
    worker = IngestWorker(platform, intake)
    for it in _DOCS:
        it.meta.setdefault("ontology", "quality-assurance")
        intake.submit(it)
    worker.drain()
    # merge the board facts into data/facts.json
    p = fd.data_path("facts.json", mkdir=True)
    facts = fd.read_json(p, {}) or {}
    facts.setdefault("jira_projects", {}).update(_JIRA_FACTS["jira_projects"])
    fd.write_json(p, facts)
    relmod.scan(platform, TENANT)


def run(out=print) -> dict:
    from knowledge_fabric.app import Platform

    platform = Platform(db_path=":memory:", blob_root=os.path.join(fd.fabric_root(), "blobs"))
    demo.seed(platform, [TENANT])
    build(platform)
    svc = AnswerService(platform)
    who = demo.principal_for(platform, TENANT, "developer")
    summary: dict = {}

    a1 = svc.ask(who, "what is QMentisAI")
    summary["keyless_answer"] = {"cited": bool(a1.citations), "model": a1.model_name}
    _p(
        out,
        "T91/T92 · keyless answer (Open-source LLM)",
        f"{a1.answer_text}\n  model: {a1.model_name}",
    )

    ctx = (
        platform.passages.context(TENANT, a1.citations[0].passage_id)
        if a1.citations and a1.citations[0].passage_id
        else None
    )
    summary["citation_expand"] = bool(ctx and (ctx["before"] or ctx["after"]))
    _p(out, "T93 · citation expands to its paragraph", f"neighbours: {summary['citation_expand']}")

    a2 = svc.ask(who, "show the login implementation in acme/app")
    summary["code_snippet"] = "```" in a2.answer_text
    _p(out, "T94 · code snippet with a line link", a2.answer_text[:400])

    # Level-0 facts (T42/T97): the governed keyless facts path — exact, as-of.
    a3 = aggregate.try_answer(platform, who, "how many tasks are in progress on the V1 board")
    summary["jira_in_progress"] = a3.answer_text if a3 else ""
    _p(out, "T97 · Jira board in-progress (exact, fresh)", summary["jira_in_progress"])

    a4 = svc.ask(who, "the Jira task V1-42 shows complete, check the repo and verify")
    summary["cross_source_verdict"] = (a4.why or {}).get("verdict")
    _p(out, "T99 · cross-source verification (two sources)", a4.answer_text)

    ov = roi.overview(platform, TENANT)
    summary["roi"] = {
        "hours_saved": ov["value"]["hours_saved"],
        "ratio": ov["roi"]["ratio"],
        "cost_avoided": ov["cost"]["cost_avoided_usd"],
    }
    tc = insights.token_classes(1)
    obs = roi.observability(platform, TENANT)
    summary["observability_ok"] = obs["reconciliation"]["ok"]
    classes = [k for k in ("input", "thinking", "output", "cache_read", "cache_write") if k in tc]
    _p(
        out,
        "T96 · ROI + OTel",
        f"hours saved {ov['value']['hours_saved']}, ROI ratio {ov['roi']['ratio']}, "
        f"token classes {classes}, reconciliation ok={obs['reconciliation']['ok']}",
    )

    from knowledge_fabric.surfaces.http_api import _galaxy_for_trace

    g = _galaxy_for_trace(platform, TENANT, a1.trajectory_id)
    lit = len((g or {}).get("activated_ids", []))
    summary["galaxy_activated"] = lit
    _p(out, "T100 · galaxy activations", f"concepts lit for the first answer: {lit}")

    out("\n\033[1mDemo complete — every step keyless, cited, in-app.\033[0m")
    return summary


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
