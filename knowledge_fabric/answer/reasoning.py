"""Multistep + conditional reasoning — a deterministic planner/executor
(Stage-2 Section A, "multistep and conditional reasoning").

No model is involved.  ``plan`` turns one natural-language question into a
small DAG of sub-questions using rule-based cues; ``execute`` runs the DAG
through the *governed* answer path (``ask_fn`` — normally
``AnswerService.ask`` on a sub-question) and composes one answer whose text
is built ONLY from the step answers (invariant I1: nothing is added), with
citations merged, de-duplicated by passage and re-numbered (I2).

Question forms recognised
-------------------------
* conjunction multistep   "What is X and what is Y?", "… then …", "… after that …", "…; …"
* conditional             "If X then Y (otherwise Z)", "If X, Y", "When X, does/what … Y",
                          "<question> if X"
* comparison              "Compare A and B", "difference between A and B", "A vs B",
                          "A versus B", "how does A differ from B", "A compared to B"
* single (fallback)       everything else — one lookup step

Condition evaluation (``_decide``) is transparent: the condition clause is
asked as a sub-question; the best-overlapping evidence sentence decides
True/False by polarity (negation in the sentence vs. negation in the clause);
grounding below ``CONDITION_THRESHOLD`` or overlap below
``CONDITION_OVERLAP`` yields ``None`` → the whole result becomes a clarify.

Complexity labels (``complexity``) use a documented point score:
  +2 per lookup/condition step beyond the first · +3 conditional · +3 comparison
  +1 reasoning cue (why/how/explain/…) · +1 long question (≥ ``LONG_WORDS`` words)
  simple = 0 points · medium = 1–2 · complex = ≥ 3
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..contracts.types import Answer, AnswerKind, Citation

# --------------------------------------------------------------------------
# tunables (all documented in the module docstring)
# --------------------------------------------------------------------------
CONDITION_THRESHOLD = 0.5  # min grounding_score for a condition to be decidable
CONDITION_OVERLAP = 0.5  # min share of condition terms the evidence sentence must cover
LONG_WORDS = 15  # a question with ≥ this many words earns a complexity point
COMPLEX_POINTS = 3  # score ≥ this → "complex"; ≥ 1 → "medium"; 0 → "simple"

_TOKEN = re.compile(r"[a-z0-9]+")
_MARKER = re.compile(r"\[(\d+)\]")
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "is",
    "are",
    "what",
    "which",
    "how",
    "who",
    "when",
    "where",
    "does",
    "do",
    "did",
    "was",
    "were",
    "be",
    "with",
    "that",
    "this",
    "it",
    "as",
    "by",
    "at",
    "from",
    "our",
    "we",
    "you",
    "i",
    "can",
    "will",
    "should",
    "must",
    "may",
    "then",
    "if",
    "otherwise",
    "else",
    "true",
    "case",
}
_QUESTION_START = (
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "when",
    "where",
    "why",
    "how",
    "does",
    "do",
    "did",
    "is",
    "are",
    "was",
    "were",
    "can",
    "could",
    "should",
    "would",
    "will",
    "must",
    "list",
    "explain",
    "describe",
    "tell",
    "show",
    "give",
    "summarise",
    "summarize",
    "find",
    "identify",
    "name",
    "define",
    "outline",
    "compare",
    "state",
    "provide",
)
_REASONING_CUES = (
    "why",
    "how",
    "explain",
    "analyse",
    "analyze",
    "evaluate",
    "recommend",
    "implication",
    "trade-off",
    "tradeoff",
    "justify",
    "assess",
)
_AUX = (
    "is",
    "are",
    "was",
    "were",
    "has",
    "have",
    "had",
    "can",
    "could",
    "must",
    "should",
    "will",
    "would",
    "does",
    "do",
    "did",
)
_NEGATION = re.compile(
    r"(?:\b(?:not|no|never|none|cannot|without|neither|nor|unless|"
    r"fails?|failed)\b|n't\b)"
)

# connectors that split a question into sequential/parallel lookups
_CONNECTOR = re.compile(
    r"\s*(?:;|,?\s+and\s+then\b|,?\s+then\b|,?\s+after\s+that\b,?|,?\s+and\s+also\b|"
    r",?\s+and\b|,?\s+also\b)\s*",
    re.IGNORECASE,
)
_ORDERED = ("then", "after that", ";")

# conditional shapes
_IF_THEN = re.compile(
    r"^\s*if\s+(?P<cond>.+?)\s*(?:,\s*then\b|\bthen\b|,)\s*(?P<then>.+?)"
    r"(?:\s*(?:,|;)?\s*\b(?:otherwise|else|if\s+not)\b\s*,?\s*(?P<else>.+?))?\s*[?.]?\s*$",
    re.IGNORECASE,
)
_WHEN = re.compile(
    r"^\s*(?:when|whenever)\s+(?P<cond>.+?)\s*,\s*(?P<then>(?:"
    + "|".join(_QUESTION_START)
    + r")\b.+?)\s*[?.]?\s*$",
    re.IGNORECASE,
)
_TRAILING_IF = re.compile(
    r"^\s*(?P<then>(?:" + "|".join(_QUESTION_START) + r")\b.+?)\s+if\s+(?P<cond>.+?)\s*[?.]?\s*$",
    re.IGNORECASE,
)

# comparison shapes (ordered: most specific first)
_COMPARE = [
    re.compile(
        r"\bdifferences?\s+between\s+(?P<a>.+?)\s+and\s+(?P<b>.+?)\s*[?.]?\s*$", re.IGNORECASE
    ),
    re.compile(
        r"\bcompare\s+(?P<a>.+?)\s+(?:and|with|to|vs\.?|versus|against)\s+(?P<b>.+?)\s*[?.]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhow\s+(?:does|do|is|are)\s+(?P<a>.+?)\s+(?:differ|different)\s+from\s+(?P<b>.+?)\s*[?.]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?P<a>.+?)\s+compared\s+(?:to|with)\s+(?P<b>.+?)\s*[?.]?\s*$", re.IGNORECASE),
    re.compile(r"^(?P<a>.+?)\s+(?:vs\.?|versus)\s+(?P<b>.+?)\s*[?.]?\s*$", re.IGNORECASE),
]
_LEAD_QUESTION = re.compile(
    r"^\s*(?:what|which|who|how)\s+(?:is|are|was|were|do|does)\s+(?:the\s+)?", re.IGNORECASE
)
_LEAD_COMPARE = re.compile(
    r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:compare\s+)?", re.IGNORECASE
)


# --------------------------------------------------------------------------
# small text helpers
# --------------------------------------------------------------------------
def _terms(text: str) -> set[str]:
    """Content tokens (stop words removed, citation markers stripped)."""
    return {t for t in _TOKEN.findall(_MARKER.sub(" ", text).lower()) if t not in _STOP}


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _clean(clause: str) -> str:
    c = clause.strip().strip(",;:").strip()
    c = re.sub(r"^\s*(?:then|also|after that|and)\b\s*,?\s*", "", c, flags=re.IGNORECASE)
    return c.strip().rstrip("?.!").strip()


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _looks_like_question(clause: str) -> bool:
    first = _TOKEN.findall(clause.lower())[:1]
    return bool(first) and first[0] in _QUESTION_START


def _as_question(clause: str) -> str:
    """Turn a clause into a retrievable sub-question (keeps the clause's own words)."""
    c = _clean(clause)
    return _capitalise(c) + "?" if c else ""


def _condition_question(clause: str) -> str:
    """'the release is blocked' → 'Is the release blocked?'; otherwise 'Is it the case that …?'"""
    c = _clean(clause)
    if _looks_like_question(c):
        return _capitalise(c) + "?"
    m = re.match(
        r"^(?P<subj>.+?)\s+(?P<aux>" + "|".join(_AUX) + r")\s+(?P<rest>.+)$", c, re.IGNORECASE
    )
    if m and _TOKEN.findall(m.group("subj")):
        return f"{m.group('aux').capitalize()} {m.group('subj')} {m.group('rest')}?"
    return f"Is it the case that {c}?"


def _has_negation(text: str) -> bool:
    return bool(_NEGATION.search(text.lower().replace("’", "'")))


def _strip_compare_lead(phrase: str) -> str:
    return _LEAD_QUESTION.sub("", _LEAD_COMPARE.sub("", phrase)).strip()


def _distribute(a: str, b: str) -> tuple[str, str]:
    """'entry criteria for smoke' / 'regression' → both sides carry 'entry criteria for …'."""
    m = re.match(r"^(?P<head>.+?\s(?:for|of|in|on))\s+(?P<tail>.+)$", a, re.IGNORECASE)
    if m and not re.search(r"\s(?:for|of|in|on)\s", b, re.IGNORECASE):
        return a, f"{m.group('head')} {b}"
    return a, b


# --------------------------------------------------------------------------
# planner
# --------------------------------------------------------------------------
def _step(
    sid: str,
    question: str,
    kind: str,
    depends_on: list[str] | None = None,
    branch: dict | None = None,
    **extra: Any,
) -> dict:
    s = {
        "id": sid,
        "question": question,
        "kind": kind,
        "depends_on": list(depends_on or []),
        "branch": branch,
    }
    s.update(extra)
    return s


def _split_conjunction(question: str) -> tuple[list[str], list[str]]:
    """Split on connectors; a bare 'and'/'also' splits only when the right side
    starts like a question (so 'entry and exit criteria' stays whole)."""
    q = question.strip()
    pieces, connectors = [], []
    last = 0
    for m in _CONNECTOR.finditer(q):
        left, right = q[last : m.start()], q[m.end() :]
        word = re.sub(r"[,;\s]+", " ", m.group(0).strip().lower()).strip(", ")
        word = "; " if word == ";" else word
        ordered = any(o in word for o in _ORDERED)
        if not left.strip() or not right.strip():
            continue
        if not ordered and not _looks_like_question(right):
            continue
        if len(_TOKEN.findall(left)) < 2 or len(_TOKEN.findall(right)) < 2:
            continue
        pieces.append(left)
        connectors.append(word.strip())
        last = m.end()
    pieces.append(q[last:])
    return [p for p in pieces if _clean(p)], connectors


def _plan_conditional(question: str) -> dict | None:
    m = _IF_THEN.match(question)
    form = "if-then"
    if m is None:
        m = _WHEN.match(question)
        form = "when"
    if m is None:
        m = _TRAILING_IF.match(question)
        form = "trailing-if"
        if m is not None and len(_terms(m.group("then"))) < 2:
            m = None  # "what happens if …" is a plain lookup, not a checkable branch
    if m is None:
        return None
    cond, then_ = _clean(m.group("cond")), _clean(m.group("then"))
    else_ = _clean(m.group("else")) if form == "if-then" and m.group("else") else ""
    if not cond or not then_:
        return None
    steps = [_step("s1", _condition_question(cond), "condition", clause=cond, role="condition")]
    steps.append(_step("s2", _as_question(then_), "lookup", ["s1"], clause=then_, role="then"))
    branch = {"if_true": "s2", "if_false": None}
    deps = ["s1", "s2"]
    if else_:
        steps.append(
            _step("s3", _as_question(else_), "lookup", ["s1"], clause=else_, role="otherwise")
        )
        branch["if_false"] = "s3"
        deps.append("s3")
    steps[0]["branch"] = branch
    steps.append(
        _step(
            f"s{len(steps) + 1}",
            "Compose the selected branch.",
            "synthesize",
            deps,
            role="synthesize",
        )
    )
    return {
        "mode": "conditional",
        "steps": steps,
        "signals": {
            "conditional": True,
            "form": form,
            "has_otherwise": bool(else_),
            "condition": cond,
        },
    }


def _plan_compare(question: str) -> dict | None:
    for rx in _COMPARE:
        m = rx.search(question)
        if not m:
            continue
        a, b = _strip_compare_lead(_clean(m.group("a"))), _clean(m.group("b"))
        if not _terms(a) or not _terms(b):
            continue
        a, b = _distribute(a, b)
        steps = [
            _step("s1", f"What is {a}?", "lookup", clause=a, role="a"),
            _step("s2", f"What is {b}?", "lookup", clause=b, role="b"),
            _step("s3", f"Compare {a} with {b}.", "compare", ["s1", "s2"], role="compare"),
        ]
        return {"mode": "compare", "steps": steps, "signals": {"comparison": True, "a": a, "b": b}}
    return None


def _plan_multistep(question: str) -> dict | None:
    pieces, connectors = _split_conjunction(question)
    if len(pieces) < 2:
        return None
    steps = []
    for i, piece in enumerate(pieces):
        sid = f"s{i + 1}"
        deps = [f"s{i}"] if i and any(o in connectors[i - 1] for o in _ORDERED) else []
        steps.append(
            _step(sid, _as_question(piece), "lookup", deps, clause=_clean(piece), role="lookup")
        )
    steps.append(
        _step(
            f"s{len(pieces) + 1}",
            "Merge the step answers.",
            "synthesize",
            [s["id"] for s in steps],
            role="synthesize",
        )
    )
    return {
        "mode": "multistep",
        "steps": steps,
        "signals": {
            "connectives": connectors,
            "ordered": any(any(o in c for o in _ORDERED) for c in connectors),
        },
    }


def _score(question: str, mode: str, steps: list[dict]) -> tuple[int, dict]:
    asks = [s for s in steps if s["kind"] in ("lookup", "condition")]
    cues = sorted(
        c for c in _REASONING_CUES if re.search(r"\b" + re.escape(c) + r"\b", question.lower())
    )
    words = len(_TOKEN.findall(question))
    points = 2 * max(0, len(asks) - 1)
    points += 3 if mode == "conditional" else 0
    points += 3 if mode == "compare" else 0
    points += 1 if cues else 0
    points += 1 if words >= LONG_WORDS else 0
    return points, {"n_steps": len(asks), "reasoning_cues": cues, "words": words, "score": points}


def _label(points: int) -> str:
    if points >= COMPLEX_POINTS:
        return "complex"
    return "medium" if points >= 1 else "simple"


def _build_plan(question: str) -> dict:
    q = (question or "").strip()
    built = _plan_conditional(q) or _plan_compare(q) or _plan_multistep(q)
    if built is None:
        built = {
            "mode": "single",
            "steps": [
                _step(
                    "s1",
                    q if q.endswith("?") else _as_question(q) or q,
                    "lookup",
                    clause=_clean(q),
                    role="lookup",
                )
            ],
            "signals": {},
        }
    points, sig = _score(q, built["mode"], built["steps"])
    signals = {
        "conditional": False,
        "comparison": False,
        "connectives": [],
        **built["signals"],
        **sig,
    }
    return {
        "mode": built["mode"],
        "steps": built["steps"],
        "complexity": _label(points),
        "signals": signals,
        "question": q,
    }


def plan(question: str) -> dict:
    """Deterministic, rule-based plan for ``question`` (see module docstring for shapes)."""
    return _build_plan(question)


def complexity(question: str, plan: dict | None = None) -> str:
    """'simple' | 'medium' | 'complex' — from ``plan`` when given, else from a fresh plan."""
    p = plan if plan is not None else _build_plan(question)
    if "complexity" in p:
        return p["complexity"]
    return _label(_score(question, p.get("mode", "single"), p.get("steps", []))[0])


def explain(plan: dict) -> str:
    """One plain sentence for the why-card describing how the question is being handled."""
    mode, steps, sig = plan.get("mode", "single"), plan.get("steps", []), plan.get("signals", {})
    n = sum(1 for s in steps if s["kind"] in ("lookup", "condition"))
    if mode == "multistep":
        joins = sorted(set(sig.get("connectives", []))) or ["and"]
        order = "answered in order" if sig.get("ordered") else "answered independently"
        return (
            f"Multistep: split into {n} lookups joined by "
            f"{', '.join(repr(j) for j in joins)}, {order} and merged into one cited answer."
        )
    if mode == "conditional":
        tail = (
            " with an otherwise-branch"
            if sig.get("has_otherwise")
            else " (no otherwise-branch given)"
        )
        return (
            f"Conditional: first checks whether “{sig.get('condition', '')}” holds, "
            f"then answers only the branch the evidence supports{tail}."
        )
    if mode == "compare":
        return (
            f"Comparison: looks up “{sig.get('a', '')}” and “{sig.get('b', '')}” "
            f"separately and sets the grounded findings side by side."
        )
    return "Single lookup: answered directly from the governed corpus in one step."


# --------------------------------------------------------------------------
# executor
# --------------------------------------------------------------------------
def _kind_of(answer: Answer) -> str:
    k = getattr(answer, "kind", AnswerKind.ANSWER)
    return k.value if isinstance(k, AnswerKind) else str(k)


def _decide(clause: str, answer: Answer, threshold: float) -> dict:
    """Decide a condition from its sub-answer.  Returns {"condition", "reason", "evidence",
    "overlap", "negated"} — ``condition`` is None whenever the evidence is not decisive."""
    out = {"condition": None, "reason": "", "evidence": "", "overlap": 0.0, "negated": False}
    if _kind_of(answer) != AnswerKind.ANSWER.value:
        out["reason"] = f"no grounded evidence ({_kind_of(answer)})"
        return out
    g = float(getattr(answer, "grounding_score", 0.0) or 0.0)
    if g < threshold:
        out["reason"] = f"grounding {g:.2f} below threshold {threshold:.2f}"
        return out
    cond_terms = _terms(clause)
    if not cond_terms:
        out["reason"] = "condition has no content terms"
        return out
    best, best_ov = "", 0.0
    for s in _sentences(answer.answer_text) or [answer.answer_text]:
        ov = len(cond_terms & _terms(s)) / len(cond_terms)
        if ov > best_ov:
            best, best_ov = s, ov
    out["evidence"], out["overlap"] = best, round(best_ov, 3)
    if best_ov < CONDITION_OVERLAP:
        out["reason"] = (
            f"evidence covers {best_ov:.0%} of the condition terms (< {CONDITION_OVERLAP:.0%})"
        )
        return out
    sent_neg, cond_neg = _has_negation(best), _has_negation(clause)
    out["negated"] = sent_neg
    out["condition"] = sent_neg == cond_neg
    out["reason"] = (
        "evidence states the condition" if not sent_neg else "evidence negates the condition"
    )
    if cond_neg:
        out["reason"] += " (condition itself is negated)"
    return out


class _CitationBook:
    """Merges citations across steps: one number per passage_id, first-seen order."""

    def __init__(self) -> None:
        self.citations: list[Citation] = []
        self._num: dict[str, int] = {}

    def renumber(self, text: str, citations: list[Citation]) -> tuple[str, list[int]]:
        numbers = []
        for c in citations:
            if c.passage_id not in self._num:
                self._num[c.passage_id] = len(self.citations) + 1
                self.citations.append(c)
            numbers.append(self._num[c.passage_id])

        def sub(m: re.Match) -> str:
            i = int(m.group(1)) - 1
            return f"[{numbers[i]}]" if 0 <= i < len(numbers) else ""

        return _MARKER.sub(sub, text).replace("  ", " ").strip(), numbers


def _blank(step: dict, skipped: bool, reason: str) -> dict:
    return {
        "id": step["id"],
        "question": step["question"],
        "kind": step["kind"],
        "answer_text": "",
        "citations": [],
        "citation_numbers": [],
        "grounding": 0.0,
        "confidence": 0.0,
        "answer_kind": None,
        "kind_result": {"condition": None, "reason": reason},
        "skipped": skipped,
        "role": step.get("role"),
        "clause": step.get("clause"),
    }


def execute(
    plan: dict, ask_fn: Callable[[str], Answer], *, condition_threshold: float = CONDITION_THRESHOLD
) -> dict:
    """Run ``plan`` through ``ask_fn`` (the governed path) and compose one answer.

    Returns {"mode", "steps": [...], "final_text", "citations", "confidence", "complexity",
    "kind": "answer"|"clarify"|"gap", "gap_step": id|None, "clarify_back": str|None,
    "explain": str}.  ``final_text`` is composed only from step answer texts (plus neutral
    structural labels); citations are merged, de-duplicated by passage_id and renumbered.
    """
    steps = plan.get("steps", [])
    by_id = {s["id"]: s for s in steps}
    book = _CitationBook()
    results: dict[str, dict] = {}
    # branch targets: step id -> (condition step id, required value)
    gate: dict[str, tuple[str, bool]] = {}
    for s in steps:
        br = s.get("branch") or {}
        if br.get("if_true"):
            gate[br["if_true"]] = (s["id"], True)
        if br.get("if_false"):
            gate[br["if_false"]] = (s["id"], False)

    ordered: list[dict] = []
    for s in steps:
        kind = s["kind"]
        if s["id"] in gate:
            cid, want = gate[s["id"]]
            got = results.get(cid, {}).get("kind_result", {}).get("condition")
            if got is None or got != want:
                r = _blank(
                    s, True, "branch not selected" if got is not None else "condition undecided"
                )
                results[s["id"]] = r
                ordered.append(r)
                continue
        if kind in ("lookup", "condition"):
            ans = ask_fn(s["question"])
            text, numbers = book.renumber(ans.answer_text or "", list(ans.citations or []))
            akind = _kind_of(ans)
            r = _blank(s, False, "")
            r.update(
                {
                    "answer_text": text,
                    "citations": list(ans.citations or []),
                    "citation_numbers": numbers,
                    "answer_kind": akind,
                    "grounding": round(float(getattr(ans, "grounding_score", 0.0) or 0.0), 4),
                    "confidence": round(float(getattr(ans, "confidence", 0.0) or 0.0), 4),
                    "clarify_back": getattr(ans, "clarify_back", None),
                }
            )
            if kind == "condition":
                r["kind_result"] = _decide(s.get("clause", s["question"]), ans, condition_threshold)
            else:
                r["kind_result"] = {"condition": None, "answer_kind": akind}
        elif kind == "compare":
            r = _compose_compare(s, results, by_id)
        else:  # synthesize
            r = _compose_synthesize(s, results)
        results[s["id"]] = r
        ordered.append(r)

    return _finalise(plan, ordered, book)


def _answered(r: dict) -> bool:
    return (
        (not r["skipped"])
        and r["kind"] in ("lookup", "condition")
        and r["answer_kind"] == AnswerKind.ANSWER.value
        and bool(r["answer_text"])
    )


def _compose_compare(step: dict, results: dict, by_id: dict) -> dict:
    a_id, b_id = step["depends_on"][0], step["depends_on"][1]
    ra, rb = results.get(a_id), results.get(b_id)
    r = _blank(step, False, "")
    la, lb = by_id[a_id].get("clause", "A"), by_id[b_id].get("clause", "B")
    parts = []
    if ra and _answered(ra):
        parts.append(f"{_capitalise(la)}: {ra['answer_text']}")
    if rb and _answered(rb):
        parts.append(f"{_capitalise(lb)}: {rb['answer_text']}")
    ta = _terms(ra["answer_text"]) if ra else set()
    tb = _terms(rb["answer_text"]) if rb else set()
    r["answer_text"] = " ".join(parts)
    r["kind_result"] = {
        "condition": None,
        "a": la,
        "b": lb,
        "shared_terms": sorted(ta & tb)[:12],
        "only_a": sorted(ta - tb)[:12],
        "only_b": sorted(tb - ta)[:12],
        "both_answered": bool(parts) and len(parts) == 2,
    }
    return r


def _compose_synthesize(step: dict, results: dict) -> dict:
    r = _blank(step, False, "")
    used, parts = [], []
    for dep in step["depends_on"]:
        d = results.get(dep)
        if not d or not _answered(d):
            continue
        used.append(dep)
        if d["kind"] == "condition":
            cond = d["kind_result"].get("condition")
            if cond is None:
                continue
            state = "holds" if cond else "does not hold"
            parts.append(
                f"Condition “{d.get('clause') or d['question']}” {state} "
                f"based on the evidence: {d['answer_text']}"
            )
        else:
            parts.append(d["answer_text"])
    r["answer_text"] = " ".join(parts)
    r["kind_result"] = {"condition": None, "merged_steps": used}
    return r


def _finalise(plan: dict, ordered: list[dict], book: _CitationBook) -> dict:
    executed = [r for r in ordered if not r["skipped"] and r["kind"] in ("lookup", "condition")]
    gap = next((r for r in executed if r["answer_kind"] == AnswerKind.GAP.value), None)
    clar = next((r for r in executed if r["answer_kind"] == AnswerKind.CLARIFY.value), None)
    undecided = next(
        (
            r
            for r in executed
            if r["kind"] == "condition"
            and r["answer_kind"] == AnswerKind.ANSWER.value
            and r["kind_result"].get("condition") is None
        ),
        None,
    )
    answered = [r for r in executed if _answered(r)]
    idx = {r["id"]: i + 1 for i, r in enumerate(ordered)}

    kind, gap_step, clarify_back = "answer", None, None
    partial = " ".join(r["answer_text"] for r in answered if r["kind"] == "lookup")
    if gap is not None:
        kind, gap_step = "gap", gap["id"]
        final = f"Step {idx[gap['id']]} (“{gap['question']}”) lacked evidence: {gap['answer_text']}"
        if partial:
            final = f"Partial result. {partial} {final}"
    elif clar is not None:
        kind, gap_step, clarify_back = (
            "clarify",
            clar["id"],
            clar.get("clarify_back") or clar["answer_text"],
        )
        final = (
            f"Step {idx[clar['id']]} (“{clar['question']}”) needs clarification: "
            f"{clar['answer_text']}"
        )
    elif undecided is not None:
        kind, gap_step = "clarify", undecided["id"]
        clause = undecided.get("clause") or undecided["question"]
        clarify_back = (
            f"Please confirm whether “{clause}” applies "
            f"({undecided['kind_result'].get('reason', 'undecided')})."
        )
        final = (
            f"Step {idx[undecided['id']]} could not decide whether “{clause}” holds "
            f"from the evidence: {undecided['answer_text']} {clarify_back}"
        ).strip()
    else:
        final = _compose_final(plan, ordered)
        if not final:
            kind = "gap"
            final = "No step produced a grounded answer."

    confidence = 0.0
    if answered:
        required = len(executed)
        coverage = len(answered) / max(1, required)
        confidence = round(min(r["confidence"] for r in answered) * coverage, 3)
    if kind != "answer":
        confidence = min(
            confidence, round(min((r["confidence"] for r in answered), default=0.0) * 0.5, 3)
        )

    return {
        "mode": plan.get("mode", "single"),
        "steps": ordered,
        "final_text": final,
        "citations": list(book.citations),
        "confidence": confidence,
        "complexity": plan.get("complexity") or complexity(plan.get("question", ""), plan),
        "kind": kind,
        "gap_step": gap_step,
        "clarify_back": clarify_back,
        "explain": explain(plan),
    }


def _compose_final(plan: dict, ordered: list[dict]) -> str:
    mode = plan.get("mode", "single")
    last = ordered[-1] if ordered else None
    if (
        mode in ("multistep", "compare", "conditional")
        and last
        and last["kind"] in ("synthesize", "compare")
    ):
        text = last["answer_text"]
        if mode == "conditional" and text:
            cond = next((r for r in ordered if r["kind"] == "condition"), None)
            if (
                cond
                and cond["kind_result"].get("condition") is False
                and not any(r["kind"] == "lookup" and not r["skipped"] for r in ordered)
            ):
                text += " No alternative branch was given for this case."
        return text
    return " ".join(r["answer_text"] for r in ordered if _answered(r))


def to_surface(result: dict) -> dict:
    """Shape consumed by the Ask page's reasoning timeline (Section G)."""
    return {
        "mode": result["mode"],
        "kind": result.get("kind", "answer"),
        "complexity": result.get("complexity"),
        "explain": result.get("explain"),
        "steps": [
            {
                "id": s["id"],
                "question": s["question"],
                "kind": s["kind"],
                "answer_text": s["answer_text"],
                "grounding": s["grounding"],
                "condition": s["kind_result"].get("condition"),
                "skipped": s["skipped"],
                "reason": s["kind_result"].get("reason", ""),
            }
            for s in result["steps"]
        ],
    }
