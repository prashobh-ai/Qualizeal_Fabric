"""Answer one queued question (T45) — the ``ask`` workflow's worker.

    python scripts/answer_issue.py --issue 42            # an `ask` issue
    python scripts/answer_issue.py --question "what is QMentisAI"   # workflow_dispatch

Reads the issue (``GET /repos/{o}/{r}/issues/{n}``), parses the question (the
issue-form ``### Question`` section, or a JSON body ``{question, subject,
context}`` the Workspace pre-filled), runs the agent under
``KF_LEDGER_PURPOSE=ask_queue`` (the governed ``AnswerService`` until T43's
agent lands), writes ``answers/<hash>.json`` with ``source: "queue"`` under
``KF_FABRIC_ROOT``, comments the answer with its citations on the issue,
labels it ``answered`` and closes it.

Loud on failure: the model MUST be available (``KF_MODEL_MODE=anthropic`` and
a key) — otherwise, or on any error, the failure is commented on the issue and
the process exits non-zero. Never a faked answer.

Every GitHub call goes through one ``transport`` (``urllib.request.urlopen``
by default) so tests inject a fake and no network is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("KF_LEDGER_PURPOSE", "ask_queue")
os.environ.setdefault("KF_MODEL_MODE", "anthropic")

from knowledge_fabric import baking  # noqa: E402
from knowledge_fabric import fabric_data as fd  # noqa: E402
from knowledge_fabric.contracts.types import Principal  # noqa: E402
from knowledge_fabric.telemetry import api_ledger  # noqa: E402

DEFAULT_REPO = "prashobh-ai/QualiZeal_Fabric"
TENANT = "qualizeal"
API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    """The four calls the queue needs, over urllib. ``transport`` is
    ``urlopen``-shaped: ``transport(request, timeout=…) -> response``."""

    def __init__(self, repo: str, token: str = "", transport=None, base: str = API):
        self.repo, self.token, self.base = repo, token, base.rstrip("/")
        self.transport = transport or urllib.request.urlopen
        self.calls: list[tuple[str, str, dict | None]] = []

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        self.calls.append((method, path, body))
        try:
            with self.transport(req, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise GitHubError(
                f"{method} {path} → HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}"
            ) from e
        except urllib.error.URLError as e:
            raise GitHubError(f"{method} {path} unreachable: {e.reason}") from e
        return json.loads(raw) if raw else {}

    def issue(self, number: int) -> dict:
        return self._call("GET", f"/repos/{self.repo}/issues/{number}")

    def comment(self, number: int, body: str) -> dict:
        return self._call("POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body})

    def add_labels(self, number: int, labels: list[str]) -> dict:
        return self._call("POST", f"/repos/{self.repo}/issues/{number}/labels", {"labels": labels})

    def close(self, number: int) -> dict:
        return self._call(
            "PATCH",
            f"/repos/{self.repo}/issues/{number}",
            {"state": "closed", "state_reason": "completed"},
        )


# --------------------------------------------------------------------------
# question parsing
# --------------------------------------------------------------------------
_SECTION = re.compile(r"###\s*Question\s*\n+(.*?)(?:\n###|\Z)", re.S | re.I)
_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def parse_issue(issue: dict) -> dict:
    """``{question, subject, context}`` from an issue. Order: a JSON object in
    the body (the Workspace pre-fill) → the form's ``### Question`` section →
    the whole body → the title (with a leading ``[ask]`` dropped)."""
    title = str(issue.get("title") or "").strip()
    body = str(issue.get("body") or "").strip()
    out = {"question": "", "subject": "", "context": None}
    m = _JSON_OBJ.search(body)
    if m:
        try:
            data = json.loads(m.group(0))
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("question"):
            out["question"] = str(data["question"]).strip()
            out["subject"] = str(data.get("subject") or "").strip()
            ctx = data.get("context")
            out["context"] = ctx if isinstance(ctx, dict) else None
    if not out["question"]:
        s = _SECTION.search(body)
        text = (s.group(1) if s else body).strip()
        text = re.sub(r"^_No response_$", "", text, flags=re.M).strip()
        if text and not text.startswith("{"):
            out["question"] = text.splitlines()[0].strip() if "\n" in text else text
    if not out["question"]:
        out["question"] = re.sub(r"^\s*\[ask\]\s*", "", title, flags=re.I).strip()
    if not out["subject"]:
        out["subject"] = str((issue.get("user") or {}).get("login") or "").strip()
    return out


# --------------------------------------------------------------------------
# answering
# --------------------------------------------------------------------------
def queue_principal(platform, subject: str = "") -> Principal:
    token = platform.idp.mint(
        Principal(subject=subject or "ask-queue", tenant=TENANT, roles=["asker"], scopes=["public"])
    )
    return platform.idp.authenticate({"token": token})


def render_comment(question: str, answer: dict, path_rel: str) -> str:
    kind = answer.get("kind")
    lines = [f"**Question:** {question}", ""]
    if kind == "answer":
        text = re.sub(r"\[(\d+)\]", r"[\1]", answer.get("answer_text") or "")
        lines += [text, "", "**Sources**"]
        for i, c in enumerate(answer.get("citations") or [], 1):
            url = ((c.get("coordinate") or {}).get("locator") or {}).get("url") or ""
            where = c.get("coordinate_render") or ""
            title = c.get("document_title") or c.get("document_id") or "document"
            lines.append(f"{i}. {f'[{title}]({url})' if url else title} · {where}")
    elif kind == "clarify":
        lines += [
            "The fabric needs a clearer question: " + (answer.get("clarify_back") or ""),
            "",
        ] + [f"- {s}" for s in answer.get("suggestions") or []]
    else:
        lines += ["No supporting evidence exists in the fabric for that yet (declined)."]
    lines += [
        "",
        f"_Model {answer.get('model') or 'none'} · cost ${float(answer.get('cost_usd') or 0):.4f} "
        f"· level {answer.get('level', 0)} · baked to `{path_rel}` · "
        f"trajectory `{answer.get('trajectory_id', '')}`_",
    ]
    return "\n".join(lines)


def answer_question(platform, question: str, subject: str = "", context=None) -> tuple[dict, str]:
    """Run the agent (or the governed service) and bake ``answers/<hash>.json``
    with ``source: "queue"``. Returns ``(file body, file path)``."""
    if not platform.model.available():
        raise RuntimeError(
            "the ask queue needs the model: set KF_MODEL_MODE=anthropic and ANTHROPIC_API_KEY "
            "(no answer was produced — the queue never fakes one)"
        )
    answer_fn, name = baking.answerer(platform)
    prin = queue_principal(platform, subject)
    ans, steps = answer_fn(prin, question, context)
    path = baking.write_answer(
        question,
        ans,
        source="queue",
        model=ans.model_name,
        cost_usd=ans.cost,
        steps=steps,
        trajectory_id=ans.trajectory_id,
        subject=subject,
        extra={"answerer": name, "tenant": TENANT},
    )
    return fd.read_json(path) or {}, path


def _platform():
    from knowledge_fabric.app import Platform
    from knowledge_fabric.tenants import demo

    db = os.environ.get("KF_DB") or ":memory:"
    p = Platform(db_path=db, blob_root=os.path.join(fd.fabric_root(), "blobs"))
    demo.seed(p, [TENANT])
    if not p.documents.list(TENANT):
        from scripts import ingest as _ingest

        _ingest.load_corpus(p, TENANT, out=lambda *_: None)
    return p


def run(
    *,
    issue: int | None = None,
    question: str | None = None,
    repo: str | None = None,
    platform=None,
    transport=None,
    out=print,
) -> int:
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    gh = GitHub(repo, os.environ.get("GITHUB_TOKEN", ""), transport=transport)
    subject, context = "", None
    if issue is not None:
        parsed = parse_issue(gh.issue(issue))
        question, subject, context = parsed["question"], parsed["subject"], parsed["context"]
    question = (question or "").strip()
    if not question:
        msg = "ask queue: no question found (empty issue body and title)"
        out(msg)
        if issue is not None:
            gh.comment(issue, f"⚠️ {msg}")
        return 2
    try:
        p = platform or _platform()
        body, path = answer_question(p, question, subject, context)
    except Exception as e:  # noqa: BLE001 — every failure lands on the issue, loudly
        err = f"{type(e).__name__}: {e}"
        out(f"ask queue FAILED for {question!r}: {err}")
        out(traceback.format_exc())
        if issue is not None:
            gh.comment(
                issue,
                f"⚠️ The queue could not answer **{question}**.\n\n```\n{err[:1500]}\n```\n"
                "No answer was produced; the run is marked failed.",
            )
        return 1
    rel = os.path.relpath(path, fd.fabric_root())
    line = (
        f"ask queue: {body.get('kind')} · {question!r} → {rel} · model {body.get('model') or '—'} "
        f"· ${float(body.get('cost_usd') or 0):.4f}"
    )
    out(line)
    api_ledger.append_step_summary(line)
    api_ledger.append_step_summary(api_ledger.step_summary_line())
    if issue is not None:
        gh.comment(issue, render_comment(question, body, rel))
        gh.add_labels(issue, ["answered"])
        gh.close(issue)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="answer_issue")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--issue", type=int, help="the `ask` issue number")
    g.add_argument("--question", help="answer this question directly (workflow_dispatch)")
    ap.add_argument("--repo", default=None, help="owner/name (default GITHUB_REPOSITORY)")
    args = ap.parse_args(argv)
    return run(issue=args.issue, question=args.question, repo=args.repo)


if __name__ == "__main__":
    sys.exit(main())
