"""Answer-quality golden suite + CI gate (T28).

The promotion gate in ``gate.py`` protects a *candidate index version* against
the question bank. This is the complementary guard: a curated set of **golden
cases** that pin the answering *behaviours* the product promises — a grounded
answer with resolvable citations, an honest decline out of corpus, a code answer
that quotes the function and links its lines, asset discovery, two-turn
follow-up resolution, and role/designation conditioning (T24–T27). Each case is a
hard must-hold assertion over the REAL governed answer path with the model
disabled, so a regression in any of those behaviours fails the gate — locally
(``make quality``) and in CI.

Deterministic and self-contained: the suite ingests its own tiny corpus through
the live pipeline, so it needs no external corpus and runs in the SQLite CI job.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..answer.service import AnswerService
from ..contracts.types import AnswerKind, Principal
from ..ingestion.intake import IngestWorker, Intake

EVAL_TENANT = "quality-eval"

# --------------------------------------------------------------------------
# The golden corpus — a handful of documents, ingested through the real
# pipeline, chosen so every answering dimension has something to exercise.
# --------------------------------------------------------------------------
_QMENTIS = (
    "# QMentisAI\n\n"
    "QMentisAI is an AI test-intelligence platform for quality engineering. "
    "QMentisAI pricing is usage-based, billed per test run. "
    "QMentisAI serves QA engineers and testers who want failure triage without "
    "writing scripts. It integrates with the existing CI pipeline.\n"
)
_RETRY = '''"""Retry helpers."""


def retry_call(fn, attempts=3):
    """Retry a flaky call with backoff until it succeeds."""
    for _ in range(attempts):
        try:
            return fn()
        except Exception:
            continue
'''
_RETRY_TEST = '''"""Tests for retry_call."""


def test_retry_call_succeeds():
    """Verify retry_call retries a flaky call and eventually succeeds."""
    assert retry_call(lambda: 1) == 1
'''
_AUTH = '''"""Auth helpers for single sign-on."""


def mint_session_token(subject, roles, scopes):
    """Mint a short-lived signed session token for single sign-on."""
    return {"subject": subject, "roles": roles, "scopes": scopes}
'''
_SSO_POLICY = (
    "# Single Sign-On Standard\n\n"
    "Single sign-on lets an employee sign in once and reach every permitted "
    "application. Reuse the shared authentication building blocks rather than "
    "writing your own login flow.\n"
)

_DOCS = [
    ("internal", "internal://q/qmentis.md", "QMentisAI", _QMENTIS, "text/markdown"),
    ("github", "github://acme/app/retry.py", "retry.py", _RETRY, "text/x-python;code"),
    (
        "github",
        "github://acme/app/test_retry.py",
        "test_retry.py",
        _RETRY_TEST,
        "text/x-python;code",
    ),
    ("github", "github://acme/app/auth.py", "auth.py", _AUTH, "text/x-python;code"),
    ("internal", "internal://q/sso.md", "Single Sign-On Standard", _SSO_POLICY, "text/markdown"),
]


def build_eval_fabric(platform, tenant: str = EVAL_TENANT) -> None:
    """Ingest the golden corpus into ``tenant`` through the live pipeline."""
    platform.policy.set_budget(tenant, 20.0)
    intake, worker = Intake(platform), IngestWorker(platform, None)
    worker.intake = intake
    for source, uri, title, body, mime in _DOCS:
        intake.submit(
            intake.canonical(tenant, source, uri, title, body.encode(), mime=mime, acl=["public"])
        )
    worker.drain()


def _principal(platform, tenant: str, designation: str = "") -> Principal:
    token = platform.idp.mint(
        Principal(
            subject="eval-bot",
            tenant=tenant,
            roles=["asker"],
            scopes=["public"],
            designation=designation,
        )
    )
    return platform.idp.authenticate({"token": token})


# --------------------------------------------------------------------------
# A tiny harness: each case gets an ``ask`` bound to the eval fabric, and
# returns (ok, detail). Cases are hard must-holds — curated, not statistical.
# --------------------------------------------------------------------------
class _Ctx:
    def __init__(self, platform, tenant):
        self.svc = AnswerService(platform)
        self.platform, self.tenant = platform, tenant

    def ask(self, question, designation="", context=None):
        return self.svc.ask(
            _principal(self.platform, self.tenant, designation), question, context=context
        )


@dataclass
class Case:
    dimension: str
    name: str
    check: Callable[[_Ctx], tuple[bool, str]]


def _grounding_floor(platform) -> float:
    return getattr(platform, "grounding_threshold", 0.5)


# ---- the golden cases ----------------------------------------------------
def _c_answers(ctx):
    a = ctx.ask("what is QMentisAI")
    ok = a.kind == AnswerKind.ANSWER and bool(a.citations)
    return ok, f"kind={a.kind.value} cites={len(a.citations)}"


def _c_grounded(ctx):
    a = ctx.ask("what is QMentisAI")
    floor = _grounding_floor(ctx.platform)
    return a.grounding_score >= floor, f"grounding={a.grounding_score:.3f} floor={floor}"


def _c_citations_resolvable(ctx):
    a = ctx.ask("what is QMentisAI")
    ok = bool(a.citations) and all(c.coordinate.locator for c in a.citations)
    return ok, f"cites={len(a.citations)} all_resolvable={ok}"


def _c_cites_subject(ctx):
    a = ctx.ask("what is QMentisAI")
    titles = [c.document_title for c in a.citations]
    return "QMentisAI" in titles, f"titles={titles}"


def _c_declines_out_of_corpus(ctx):
    a = ctx.ask("what is the capital of France")
    return a.kind in (AnswerKind.GAP, AnswerKind.CLARIFY), f"kind={a.kind.value}"


def _c_code_answer(ctx):
    a = ctx.ask("how does retry_call work", designation="Developer")
    fenced = "```" in a.answer_text
    anchored = bool(a.citations) and "#L" in (a.citations[0].coordinate.locator or {}).get(
        "url", ""
    )
    return (
        a.kind == AnswerKind.ANSWER and fenced and anchored,
        f"fenced={fenced} anchored={anchored}",
    )


def _c_discovery(ctx):
    a = ctx.ask("has anyone made sso and auth code which I can reuse")
    lvl = (a.why or {}).get("level_name")
    return a.kind == AnswerKind.ANSWER and lvl == "discovery" and bool(
        a.citations
    ), f"level={lvl} cites={len(a.citations)}"


def _c_followup(ctx):
    ctx0 = {"turns": [{"question": "what is QMentisAI"}]}
    a = ctx.ask("what about its pricing", context=ctx0)
    ok = bool(a.understood_as) and "QMentisAI" in (a.understood_as or "")
    return ok, f"understood_as={a.understood_as!r}"


def _c_empty_pronoun_clarifies(ctx):
    a = ctx.ask("when was it made", context={"turns": []})
    return a.kind == AnswerKind.CLARIFY and bool(
        a.suggestions
    ), f"kind={a.kind.value} chips={a.suggestions}"


def _c_persona_depth(ctx):
    cxo = ctx.ask("what is QMentisAI", designation="CTO")
    dev = ctx.ask("what is QMentisAI", designation="Developer")
    cxo_n, dev_n = cxo.answer_text.count("["), dev.answer_text.count("[")
    ok = cxo.role_view.get("depth") == "headline" and cxo_n <= dev_n and cxo_n == 1
    return ok, f"cxo_sents={cxo_n} dev_sents={dev_n} cxo_depth={cxo.role_view.get('depth')}"


def _c_persona_emphasis(ctx):
    dev = ctx.ask("how does retry_call work", designation="Developer")
    qa = ctx.ask("how does retry_call work", designation="QA Engineer")
    dev_lead = dev.citations[0].document_title if dev.citations else ""
    qa_lead = qa.citations[0].document_title if qa.citations else ""
    ok = dev_lead == "retry.py" and qa_lead == "test_retry.py"
    return ok, f"dev_lead={dev_lead} qa_lead={qa_lead}"


def _c_persona_lens(ctx):
    dev = ctx.ask("what is QMentisAI", designation="Developer")
    cur = ctx.ask("what is QMentisAI", designation="Knowledge Curator")
    ok = dev.role_view.get("lens") == "builder" and cur.role_view.get("lens") == "curation"
    return ok, f"dev_lens={dev.role_view.get('lens')} cur_lens={cur.role_view.get('lens')}"


GOLDEN: list[Case] = [
    Case("answering", "grounded answer with citations", _c_answers),
    Case("grounding", "grounding above the floor", _c_grounded),
    Case("citations", "every citation resolves to a place", _c_citations_resolvable),
    Case("citations", "cites the subject document", _c_cites_subject),
    Case("decline", "honest decline out of corpus", _c_declines_out_of_corpus),
    Case("code", "code answer quotes the function and links its lines", _c_code_answer),
    Case("discovery", "asset discovery returns a ranked list", _c_discovery),
    Case("context", "two-turn follow-up resolves the subject", _c_followup),
    Case("context", "bare pronoun with no session asks back", _c_empty_pronoun_clarifies),
    Case("persona", "designation depth shapes the answer", _c_persona_depth),
    Case("persona", "designation emphasis leads the evidence", _c_persona_emphasis),
    Case("persona", "designation stamps the right lens", _c_persona_lens),
]


@dataclass
class SuiteResult:
    passed: bool
    total: int
    passing: int
    score: float
    dimensions: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)
    rows: list = field(default_factory=list)


def run_quality_suite(platform=None, tenant: str = EVAL_TENANT, build: bool = True) -> SuiteResult:
    """Run every golden case against the eval fabric. Returns a SuiteResult;
    ``passed`` is True only when EVERY curated case holds (a strict regression
    gate). ``platform`` may be supplied (already seeded) for tests; otherwise a
    fresh in-memory fabric is built. Pass ``build=False`` to run against a fabric
    the caller has already populated (and possibly perturbed, to prove the gate
    blocks)."""
    if platform is None:
        from ..app import Platform

        platform = Platform(db_path=":memory:", blob_root="./data/quality-blobs")
    if build:
        build_eval_fabric(platform, tenant)
    ctx = _Ctx(platform, tenant)

    rows, failures = [], []
    dims: dict = {}
    for case in GOLDEN:
        try:
            ok, detail = case.check(ctx)
        except Exception as e:  # a crash is a failure, never a pass
            ok, detail = False, f"error: {type(e).__name__}: {e}"
        rows.append({"dimension": case.dimension, "name": case.name, "ok": ok, "detail": detail})
        d = dims.setdefault(case.dimension, {"pass": 0, "total": 0})
        d["total"] += 1
        d["pass"] += 1 if ok else 0
        if not ok:
            failures.append(f"[{case.dimension}] {case.name} — {detail}")

    passing = sum(1 for r in rows if r["ok"])
    total = len(rows)
    return SuiteResult(
        passed=(passing == total and total > 0),
        total=total,
        passing=passing,
        score=round(passing / total, 4) if total else 0.0,
        dimensions={k: round(v["pass"] / v["total"], 4) for k, v in dims.items()},
        failures=failures,
        rows=rows,
    )


def format_report(result: SuiteResult) -> str:
    lines = [
        f"Quality suite: {result.passing}/{result.total} golden cases passed "
        f"(score {result.score:.2f}) — {'PASS' if result.passed else 'FAIL'}",
        "",
    ]
    for r in result.rows:
        mark = "✓" if r["ok"] else "✗"
        lines.append(f"  {mark} [{r['dimension']}] {r['name']}  ({r['detail']})")
    if result.failures:
        lines += ["", "Failures:"] + [f"  - {f}" for f in result.failures]
    return "\n".join(lines)
