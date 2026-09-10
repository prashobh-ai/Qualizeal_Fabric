"""Static-parity check (T32) — the showcase answers like the server.

The static showcase re-implements the answer path in ``engine.js`` (coreference,
persona conditioning, retrieval, discovery, the answer shape) so it can run on
GitHub Pages with no server. This check proves that mirror stays faithful: it
seeds a small fabric, drives a curated set of questions through the REAL Python
``AnswerService``, bakes a production-shaped snapshot, then runs the ACTUAL
shipped ``engine.js`` over that snapshot (under a minimal browser shim in Node)
and asserts the two agree on every parity-relevant field.

What must match, per question:
  * ``kind`` (answer / clarify / gap),
  * ``understood_as`` — the coreference rewrite (T26), which the engine
    re-computes client-side,
  * the persona lens (T27) — ``role_view`` lens / persona / depth / emphasis,
    which the engine also re-computes client-side,
  * whether the answer is cited, a code answer, or a discovery list.

Answer-derived numbers that legitimately differ between the model-free static
composer and the server (exact prose, grounding score, per-source counts) are
NOT compared — only the classification the reader sees.

Run:  python scripts/parity_check.py            # human report, exit 0 = parity
      python scripts/parity_check.py --json     # machine-readable result
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_MODEL_MODE", "off")  # the deployed, model-free path

from knowledge_fabric.answer.service import AnswerService  # noqa: E402
from knowledge_fabric.ingestion.intake import IngestWorker, Intake  # noqa: E402
from knowledge_fabric.tenants import demo  # noqa: E402

TENANT = "qualizeal"
ENGINE_JS = os.path.join(ROOT, "scripts", "showcase", "engine.js")
RUNNER_JS = os.path.join(ROOT, "scripts", "showcase", "parity_runner.js")

# --------------------------------------------------------------------------
# A small parity corpus — products (distinctive camelCase subjects), code + its
# test, an auth helper and an SSO policy — enough to exercise every path.
# --------------------------------------------------------------------------
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
        "runs evaluation suites for testers and grades model outputs.\n",
    ),
    (
        "internal",
        "internal://q/nexaai.md",
        "NexaAI",
        "text/markdown",
        "# NexaAI\n\nNexaAI is a model-serving platform. NexaAI pricing is tiered.\n",
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

# Each case: an id, the SERVER subject (demo directory) and the ENGINE subject
# (a sign-in the browser accepts — an SSO email whose local-part maps to the
# same designation, or a seeded account with its password), the question and any
# two-turn context. Server and engine resolve to the SAME persona.
CASES = [
    {
        "id": "self.developer",
        "server": "developer",
        "engine": "developer@qualizeal.com",
        "question": "what is QMentisAI",
    },
    {
        "id": "self.cto",
        "server": "cto",
        "engine": "cto@qualizeal.com",
        "question": "what is QMentisAI",
    },
    {
        "id": "self.tester",
        "server": "tester",
        "engine": "tester@qualizeal.com",
        "question": "what is QMentisAI",
    },
    {
        "id": "self.general",
        "server": "asker.public",
        "engine": "someone@qualizeal.com",
        "question": "what is QMentisAI",
    },
    {
        "id": "self.curator",
        "server": "curator",
        "engine": "curator@qualizeal.com",
        "password": "kf@qz2026",
        "question": "what is QMentisAI",
    },
    {
        "id": "coref.pronoun",
        "server": "developer",
        "engine": "developer@qualizeal.com",
        "question": "what about its pricing",
        "context": {"turns": [{"question": "what is QMentisAI"}]},
    },
    {
        "id": "coref.reach",
        "server": "asker.public",
        "engine": "someone@qualizeal.com",
        "question": "and for testers?",
        "context": {
            "turns": [
                {"question": "what is QMentisAI"},
                {"question": "what about QMentisAI pricing"},
            ]
        },
    },
    {
        "id": "code",
        "server": "developer",
        "engine": "developer@qualizeal.com",
        "question": "how does retry_call work",
    },
    {
        "id": "discovery",
        "server": "asker.public",
        "engine": "someone@qualizeal.com",
        "question": "has anyone made sso and auth code which I can reuse",
    },
    {
        "id": "decline",
        "server": "asker.public",
        "engine": "someone@qualizeal.com",
        "question": "what is the capital of France",
    },
    {
        "id": "clarify.empty",
        "server": "asker.public",
        "engine": "someone@qualizeal.com",
        "question": "when was it made",
        "context": {"turns": []},
    },
]


def _norm(q: str) -> str:
    """The engine's snapshot key normaliser, mirrored so a baked answer is found
    under the same key the browser looks it up by."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", (q or "").lower())).strip()


def _build_fabric():
    from knowledge_fabric.app import Platform

    p = Platform(db_path=":memory:", blob_root="./data/parity-blobs")
    demo.seed(p)
    intake, worker = Intake(p), IngestWorker(p, None)
    worker.intake = intake
    for source, uri, title, mime, body in _DOCS:
        intake.submit(
            intake.canonical(TENANT, source, uri, title, body.encode(), mime=mime, acl=["public"])
        )
    worker.drain()
    return p


def _server_answers(p):
    """Ask each case through the real service; return {id: answer_dict}."""
    svc = AnswerService(p)
    out = {}
    for c in CASES:
        prin = demo.principal_for(p, TENANT, c["server"])
        a = svc.ask(prin, c["question"], context=c.get("context"))
        out[c["id"]] = a.to_dict()
    return out


def _snapshot(p, server):
    """A production-shaped snapshot: the exported retrieval index, the subject
    vocabulary, and the server's answers baked under the engine's lookup key, so
    the engine serves them and re-computes coreference + persona on top."""
    import scripts.build_showcase as build

    build.TENANT = TENANT  # the exporter keys off the module TENANT
    subjects = {}
    for _s, _u, title, _m, _b in _DOCS:
        if re.search(r"[a-z][A-Z]", title):  # camelCase products only (T26/T27 rule)
            subjects[title.lower()] = title
    answers = {}
    for c in CASES:
        a = server[c["id"]]
        key = _norm(a.get("understood_as") or c["question"])
        answers[key] = a
    return {
        "login": {},
        "get": {},
        "usage": {},
        "answers": answers,
        "subjects": subjects,
        "related": {k: [] for k in subjects},
        "index": build._export_index(p),
        "corpus": {},
    }


def _engine_answers(snapshot):
    """Run the real engine.js over the snapshot in Node and return {id: answer}."""
    with tempfile.TemporaryDirectory() as d:
        snap_path = os.path.join(d, "snapshot.json")
        cases_path = os.path.join(d, "cases.json")
        json.dump(snapshot, open(snap_path, "w"))
        json.dump(
            [
                {
                    "id": c["id"],
                    "subject": c["engine"],
                    "password": c.get("password", ""),
                    "question": c["question"],
                    "context": c.get("context"),
                }
                for c in CASES
            ],
            open(cases_path, "w"),
        )
        proc = subprocess.run(
            ["node", RUNNER_JS, ENGINE_JS, snap_path, cases_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"parity_runner failed: {proc.stderr.strip()}")
    return {r["id"]: r for r in json.loads(proc.stdout)}


# --------------------------------------------------------------------------
# Comparison — classification only, never answer-derived numbers.
# --------------------------------------------------------------------------
def _cmp(case_id, srv, eng) -> list[str]:
    diffs = []
    if srv["kind"] != eng["kind"]:
        diffs.append(f"kind: server={srv['kind']} engine={eng['kind']}")
    su, eu = srv.get("understood_as") or None, eng.get("understood_as") or None
    if su != eu:
        diffs.append(f"understood_as: server={su!r} engine={eu!r}")
    srv_rv, eng_rv = srv.get("role_view") or {}, eng.get("role_view") or {}
    for key in ("lens", "persona", "depth", "emphasis"):
        if srv_rv.get(key) != eng_rv.get(key):
            diffs.append(f"role_view.{key}: server={srv_rv.get(key)!r} engine={eng_rv.get(key)!r}")
    if bool(srv.get("citations")) != bool(eng.get("citations")):
        diffs.append(
            f"cited: server={bool(srv.get('citations'))} engine={bool(eng.get('citations'))}"
        )
    # discovery + code shape (from the baked answer the engine serves)
    srv_level = (srv.get("why") or {}).get("level_name")
    if (srv_level == "discovery") != (eng.get("level_name") == "discovery"):
        diffs.append(f"discovery: server={srv_level} engine={eng.get('level_name')}")
    srv_code = "```" in (srv.get("answer_text") or "")
    if srv_code != bool(eng.get("has_code")):
        diffs.append(f"code: server={srv_code} engine={eng.get('has_code')}")
    return diffs


def run_parity() -> dict:
    p = _build_fabric()
    server = _server_answers(p)
    snapshot = _snapshot(p, server)
    engine = _engine_answers(snapshot)
    rows, failures = [], []
    for c in CASES:
        cid = c["id"]
        srv, eng = server[cid], engine.get(cid)
        if eng is None:
            diffs = ["engine produced no answer"]
        else:
            diffs = _cmp(cid, srv, eng)
        rows.append({"id": cid, "ok": not diffs, "diffs": diffs})
        if diffs:
            failures.append({"id": cid, "diffs": diffs})
    passing = sum(1 for r in rows if r["ok"])
    return {
        "passed": passing == len(rows),
        "total": len(rows),
        "passing": passing,
        "rows": rows,
        "failures": failures,
    }


def format_report(result: dict) -> str:
    lines = [
        f"Static parity: {result['passing']}/{result['total']} cases agree "
        f"— {'PASS' if result['passed'] else 'FAIL'}",
        "",
    ]
    for r in result["rows"]:
        mark = "✓" if r["ok"] else "✗"
        lines.append(f"  {mark} {r['id']}" + ("" if r["ok"] else "  →  " + "; ".join(r["diffs"])))
    return "\n".join(lines)


def main(argv) -> int:
    result = run_parity()
    if "--json" in argv:
        print(json.dumps(result, indent=2))
    else:
        print(format_report(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
