"""Coverage baking (T44) — pre-answer the questions a document invites.

At ingest, for every new or changed document (a brief, a repository card, a
PR set, a sheet, an image, a code file) the bake:

1. generates candidate questions — templates by document kind (``what is
   <title>``, ``how many rows in <sheet>``, ``who worked on <repo>``, ``when
   was <doc> updated``, ``which capabilities …``, ``where is <symbol>
   defined``) plus ONE ``question_gen`` model call per document (the small
   model, up to 12 questions, JSON-validated) when the model is available;
2. answers them with the agent (``knowledge_fabric.answer.agent``, T43) or,
   until it lands, the governed ``AnswerService.ask`` path;
3. keeps the answers that carry citations and bakes each into
   ``answers/<hash>.json`` under the fabric root, where ``hash`` is the first
   16 hex digits of ``sha256(norm(question))`` — the SAME ``norm`` the
   showcase builder and the Pages engine use, so a browser can address the
   baked file for a question without a server;
4. records the questions that FAILED (gap / clarify / no citations) per
   document in ``data/quality/bake_failures.json`` — the Curator's
   Recommendations card lists the documents whose generated questions the
   fabric cannot answer.

Idempotent: ``data/quality/bake_state.json`` remembers the content hash each
document was last baked at; unchanged documents are skipped.

The Pages engine serves, in order: a baked answer (``answers/<hash>.json``
fetched over the network) → in-browser retrieval and facts → the ask queue.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re

from . import fabric_data as fd
from .contracts.types import AnswerKind, Principal

MAX_MODEL_QUESTIONS = 12
STATE_FILE = "quality/bake_state.json"
FAILURES_FILE = "quality/bake_failures.json"


# --------------------------------------------------------------------------
# the question key — byte-identical to scripts/build_showcase.py's ``norm`` and
# to engine.js's ``norm``. Never change one without the others.
# --------------------------------------------------------------------------
def norm(q):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", q.lower())).strip()


def question_hash(question: str) -> str:
    """``sha256(norm(question))[:16]`` — the name of the baked answer file."""
    return hashlib.sha256(norm(question).encode("utf-8")).hexdigest()[:16]


def answer_path(question: str, mkdir: bool = False) -> str:
    return fd.path("answers", f"{question_hash(question)}.json", mkdir=mkdir)


def read_answer(question: str) -> dict | None:
    return fd.read_json(answer_path(question))


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_answer(
    question: str,
    answer,
    *,
    source: str,
    model: str = "",
    cost_usd: float = 0.0,
    steps: list | None = None,
    trajectory_id: str = "",
    subject: str = "",
    extra: dict | None = None,
) -> str:
    """Bake one answer to ``answers/<hash>.json``: ``Answer.to_dict()`` plus
    ``question, asked_at, model, cost_usd, source, steps, trajectory_id``."""
    body = answer.to_dict() if hasattr(answer, "to_dict") else dict(answer)
    body.update(
        {
            "question": question,
            "question_hash": question_hash(question),
            "asked_at": _now(),
            "model": model or body.get("model_name") or "",
            "cost_usd": round(float(cost_usd or body.get("cost") or 0.0), 6),
            "source": source,
            "steps": list(steps or []),
            "trajectory_id": trajectory_id or body.get("trajectory_id") or "",
        }
    )
    if subject:
        body["subject"] = subject
    if extra:
        body.update(extra)
    return fd.write_json(answer_path(question, mkdir=True), body)


# --------------------------------------------------------------------------
# document kinds + template questions
# --------------------------------------------------------------------------
def doc_kind(doc: dict) -> str:
    """``repo | pr | code | sheet | image | doc`` from the document row."""
    mime = str(doc.get("type") or "").lower()
    uri = str(doc.get("uri") or "").lower()
    title = str(doc.get("title") or "").lower()
    if mime.startswith("image/") or uri.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "image"
    if "/pull/" in uri or "/pulls/" in uri or title.startswith("pr "):
        return "pr"
    if (
        mime in ("text/csv", "application/vnd.ms-excel")
        or "spreadsheet" in mime
        or uri.endswith((".csv", ".xlsx", ".xls", ".sqlite", ".tsv"))
    ):
        return "sheet"
    if ";code" in mime or uri.endswith((".py", ".js", ".ts", ".go", ".java", ".rs")):
        return "code"
    if "repository overview" in title or (
        uri.startswith("github://") and uri.endswith("readme.md")
    ):
        return "repo"
    return "doc"


def _subject(title: str) -> str:
    """The subject of a title — a leading numeric code and the extension dropped."""
    t = re.sub(r"^\s*(?:\d+[\s._-]*)+", "", title or "").strip()
    return re.sub(r"\s+", " ", t).strip() or (title or "").strip()


def _repo_of(uri: str) -> str:
    m = re.match(r"github://([^/]+/[^/]+)", uri or "")
    return m.group(1) if m else ""


def template_questions(doc: dict, platform=None, capabilities: list[str] | None = None) -> list:
    """Candidate questions by document kind. Deterministic, no model."""
    kind = doc_kind(doc)
    title = _subject(doc.get("title") or "")
    uri = doc.get("uri") or ""
    out: list[str] = []
    if not title:
        return out
    if kind == "repo":
        repo = _repo_of(uri) or title
        out += [
            f"what does {repo} do",
            f"who worked on {repo}",
            f"which capabilities does {repo} provide",
            f"how is {repo} organised",
        ]
    elif kind == "pr":
        out += [f"what changed in {title}", f"who worked on {title}", f"why was {title} made"]
    elif kind == "sheet":
        sheet = re.sub(r"\.(csv|xlsx|xls|tsv|sqlite)$", "", title, flags=re.I)
        out += [
            f"how many rows in {sheet}",
            f"what columns are in {sheet}",
            f"what is {sheet} about",
        ]
    elif kind == "image":
        out += [f"what does {title} show", f"what is {title}"]
    elif kind == "code":
        path = re.sub(r"^github://[^/]+/[^/]+/", "", uri)
        out += [f"what does {title} do", f"where is {path or title}"]
        # one "where is <symbol> defined" per symbol the pipeline extracted
        if platform is not None:
            seen = set()
            for pas in _passages_of(platform, doc):
                loc = getattr(pas.coordinate, "locator", {}) or {}
                sym = str(loc.get("symbol") or "").strip()
                if sym and sym not in seen and re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", sym):
                    seen.add(sym)
                    out.append(f"where is {sym} defined")
                if len(seen) >= 6:
                    break
    else:
        out += [
            f"what is {title}",
            f"what does {title} cover",
            f"when was {title} updated",
        ]
    for cap in capabilities or []:
        out.append(f"which {cap} capabilities does {title} provide")
    # unique by key, order kept
    seen_keys, uniq = set(), []
    for q in out:
        k = norm(q)
        if k and k not in seen_keys:
            seen_keys.add(k)
            uniq.append(q)
    return uniq


def _passages_of(platform, doc: dict) -> list:
    tenant, did = doc.get("tenant"), doc.get("id")
    try:
        return [p for p in platform.passages.for_tenant(tenant) if p.document_id == did]
    except Exception:  # noqa: BLE001 — a store without for_tenant yields no symbols
        return []


def _doc_text(platform, doc: dict, limit: int = 6000) -> str:
    parts, n = [], 0
    for pas in _passages_of(platform, doc):
        t = (pas.text or "").strip()
        if not t:
            continue
        parts.append(t)
        n += len(t)
        if n >= limit:
            break
    return "\n\n".join(parts)[:limit]


def capabilities_for(doc: dict) -> list[str]:
    """Capability names from ``data/capabilities.json`` that list this document
    (or its repository). Missing file → none."""
    data = fd.read_json(fd.data_path("capabilities.json"), default=None)
    if not isinstance(data, (list, dict)):
        return []
    items = data.get("capabilities", data) if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = [dict(v, name=k) if isinstance(v, dict) else {"name": k} for k, v in items.items()]
    uri, did = str(doc.get("uri") or ""), str(doc.get("id") or "")
    repo = _repo_of(uri)
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or it.get("capability") or "").strip()
        refs = {str(x) for x in (it.get("documents") or it.get("docs") or [])}
        repos = {str(x) for x in (it.get("repos") or it.get("repositories") or [])}
        if name and (did in refs or uri in refs or (repo and repo in repos)):
            out.append(name)
    return out[:4]


# --------------------------------------------------------------------------
# the one question_gen call per document
# --------------------------------------------------------------------------
_QGEN_SYSTEM = (
    "You write the questions a QualiZeal employee would ask about ONE document in the "
    "company knowledge fabric. Reply with a JSON array of at most {n} short, self-contained "
    "questions (strings). Each question must be answerable from the document text alone and "
    "must name its subject (no pronouns). No prose, no markdown, no code fences — only the "
    "JSON array."
)


def _extract_json_array(text: str) -> list | None:
    """The first JSON array in ``text`` (models sometimes wrap it), else None."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return None
    return data if isinstance(data, list) else None


def validate_questions(data, max_n: int = MAX_MODEL_QUESTIONS) -> list[str]:
    """Only non-empty strings (≤ 200 chars), de-duplicated by key, ≤ ``max_n``."""
    out, seen = [], set()
    for item in data or []:
        if isinstance(item, dict):
            item = item.get("question") or item.get("q")
        if not isinstance(item, str):
            continue
        q = re.sub(r"\s+", " ", item).strip()
        if not q or len(q) > 200:
            continue
        k = norm(q)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(q)
        if len(out) >= max_n:
            break
    return out


def model_questions(platform, doc: dict, text: str, max_n: int = MAX_MODEL_QUESTIONS) -> list:
    """One ``question_gen`` call (small model) for ``doc``; ``[]`` when the
    model is off or the reply is not a JSON array. A provider error is loud
    (it propagates — a bake never fakes questions)."""
    model = getattr(platform, "model", None)
    if model is None or not model.available() or not hasattr(model, "messages"):
        return []
    from .adapters.model import resolve_models

    small, _large = resolve_models()
    body = {
        "model": small,
        "max_tokens": 1024,
        "system": [
            {
                "type": "text",
                "text": _QGEN_SYSTEM.format(n=max_n),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Document title: {doc.get('title', '')}\n"
                    f"Kind: {doc_kind(doc)}\nSource: {doc.get('uri', '')}\n\n"
                    f"Document text:\n{text[:6000]}"
                ),
            }
        ],
    }
    data = model.messages(
        body, purpose="question_gen", doc_id=str(doc.get("id") or ""), repo=_repo_of(doc.get("uri"))
    )
    reply = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return validate_questions(_extract_json_array(reply), max_n)


# --------------------------------------------------------------------------
# answering — the agent when present, the governed service until then
# --------------------------------------------------------------------------
class AgentUnavailableError(RuntimeError):
    """Mirror of the agent's exception name, for callers that pre-date T43."""


def answerer(platform):
    """``(fn, name)`` — ``fn(principal, question, context) -> (Answer, steps)``.
    The agent (T43) when importable, else ``AnswerService.ask``."""
    try:
        from .answer import agent as _agent  # noqa: F401 — lands with T43
    except ImportError:
        _agent = None
    if _agent is not None and hasattr(_agent, "run"):
        agent_unavailable = getattr(_agent, "AgentUnavailableError", AgentUnavailableError)

        def via_agent(principal, question, context=None):
            steps: list = []
            try:
                ans = _agent.run(
                    platform, principal, question, context, on_step=lambda s: steps.append(s)
                )
            except agent_unavailable:
                # extractive mode: the agent declines to run; the governed
                # service still answers with citations.
                from .answer.service import AnswerService

                return AnswerService(platform).ask(principal, question, context=context), []
            return ans, [_step_dict(s) for s in steps]

        return via_agent, "agent"

    from .answer.service import AnswerService

    svc = AnswerService(platform)

    def via_service(principal, question, context=None):
        return svc.ask(principal, question, context=context), []

    return via_service, "service"


def _step_dict(s):
    if isinstance(s, dict):
        return s
    if hasattr(s, "to_dict"):
        return s.to_dict()
    if hasattr(s, "__dict__"):
        return {k: v for k, v in vars(s).items() if not k.startswith("_")}
    return {"step": str(s)}


def bake_principal(platform, tenant: str) -> Principal:
    """The public reader: baked answers land on Pages, so only ``public``
    material may ever be cited."""
    token = platform.idp.mint(
        Principal(subject="bake-bot", tenant=tenant, roles=["asker"], scopes=["public"])
    )
    return platform.idp.authenticate({"token": token})


def _failure_reason(ans) -> str | None:
    kind = ans.kind.value if isinstance(ans.kind, AnswerKind) else str(ans.kind)
    if kind == "gap":
        return "gap: no supporting evidence"
    if kind == "clarify":
        return "clarify: " + (ans.clarify_back or "ambiguous question")[:120]
    if not ans.citations:
        return "no citations"
    return None


# --------------------------------------------------------------------------
# state + failures
# --------------------------------------------------------------------------
def load_state() -> dict:
    return fd.read_json(fd.data_path(STATE_FILE), default={}) or {}


def save_state(state: dict) -> str:
    return fd.write_json(fd.data_path(STATE_FILE), state)


def load_failures() -> list[dict]:
    data = fd.read_json(fd.data_path(FAILURES_FILE), default=[])
    return list(data) if isinstance(data, list) else []


def save_failures(rows: list[dict]) -> str:
    return fd.write_json(fd.data_path(FAILURES_FILE), rows)


def recommendations(limit: int = 50) -> list[dict]:
    """Documents whose generated questions failed, most failures first — the
    Curator's Recommendations card."""
    by_doc: dict[str, dict] = {}
    for r in load_failures():
        did = str(r.get("doc_id") or "")
        box = by_doc.setdefault(
            did,
            {
                "doc_id": did,
                "title": r.get("title") or did,
                "failed": 0,
                "questions": [],
                "reasons": {},
            },
        )
        box["failed"] += 1
        if len(box["questions"]) < 6:
            box["questions"].append(r.get("question") or "")
        key = str(r.get("reason") or "").split(":")[0] or "failed"
        box["reasons"][key] = box["reasons"].get(key, 0) + 1
    out = sorted(by_doc.values(), key=lambda b: (-b["failed"], b["title"]))
    for b in out:
        b["recommendation"] = (
            "Add or enrich material so the fabric can answer these; or mark the document "
            "authoritative if it should lead."
        )
    return out[:limit]


# --------------------------------------------------------------------------
# the bake
# --------------------------------------------------------------------------
def bake(
    platform,
    tenant: str,
    *,
    limit: int | None = None,
    principal: Principal | None = None,
    force: bool = False,
    out=print,
) -> dict:
    """Bake every new/changed document of ``tenant``. Returns the summary
    (``documents, baked_docs, skipped_unchanged, questions, model_questions,
    answers, failed, answerer``)."""
    docs = platform.documents.list(tenant)
    state = load_state()
    failures = [r for r in load_failures() if r.get("tenant") not in (None, tenant)]
    # failures of THIS tenant are rebuilt for the documents baked now and kept
    # for the ones skipped as unchanged
    old_failures = [r for r in load_failures() if r.get("tenant") in (None, tenant)]
    prin = principal or bake_principal(platform, tenant)
    answer_fn, name = answerer(platform)
    summary = {
        "tenant": tenant,
        "documents": len(docs),
        "baked_docs": 0,
        "skipped_unchanged": 0,
        "questions": 0,
        "model_questions": 0,
        "answers": 0,
        "failed": 0,
        "answerer": name,
        "files": [],
    }
    todo, kept_failures = [], []
    for d in docs:
        key = f"{tenant}:{d['id']}"
        prev = state.get(key) or {}
        if not force and prev.get("content_hash") == d.get("content_hash"):
            summary["skipped_unchanged"] += 1
            kept_failures += [r for r in old_failures if r.get("doc_id") == d["id"]]
            continue
        todo.append(d)
    if limit is not None:
        todo = todo[: max(0, int(limit))]
    for d in todo:
        caps = capabilities_for(d)
        questions = template_questions(d, platform, caps)
        model_qs = model_questions(platform, d, _doc_text(platform, d))
        summary["model_questions"] += len(model_qs)
        keys = {norm(q) for q in questions}
        for q in model_qs:
            if norm(q) not in keys:
                keys.add(norm(q))
                questions.append(q)
        kept = 0
        for q in questions:
            summary["questions"] += 1
            ans, steps = answer_fn(prin, q)
            reason = _failure_reason(ans)
            if reason:
                summary["failed"] += 1
                kept_failures.append(
                    {
                        "tenant": tenant,
                        "doc_id": d["id"],
                        "title": d.get("title") or d["id"],
                        "question": q,
                        "reason": reason,
                        "at": _now(),
                    }
                )
                continue
            path = write_answer(
                q,
                ans,
                source="bake",
                model=ans.model_name,
                cost_usd=ans.cost,
                steps=steps,
                trajectory_id=ans.trajectory_id,
                extra={"doc_id": d["id"], "tenant": tenant},
            )
            summary["answers"] += 1
            summary["files"].append(path)
            kept += 1
        state[f"{tenant}:{d['id']}"] = {
            "content_hash": d.get("content_hash"),
            "title": d.get("title"),
            "kind": doc_kind(d),
            "baked_at": _now(),
            "questions": len(questions),
            "kept": kept,
        }
        summary["baked_docs"] += 1
        out(f"bake: {d.get('title') or d['id']} · {kept}/{len(questions)} answers kept")
    save_state(state)
    save_failures(failures + kept_failures)
    return summary


def summary_line(s: dict) -> str:
    return (
        f"Bake: {s['documents']} documents · {s['baked_docs']} baked · "
        f"{s['skipped_unchanged']} unchanged · {s['questions']} questions "
        f"({s['model_questions']} from the model) · {s['answers']} answers · "
        f"{s['failed']} failed · answerer {s['answerer']}"
    )
