"""Two-turn conversation context (T26).

A follow-up question rarely repeats its subject — "what about its pricing",
"and for testers?", "the second one". This module rewrites such a question from
the last one or two turns before retrieval, or asks back when the reference is
genuinely ambiguous. It is deterministic, model-free, and mirrored verbatim in
the browser engine so the static showcase and the server resolve identically.

The rolling state per session is small and capped: the recent questions, the
subject in view and its document, the documents the last answer cited, the last
answer's kind, and any clarify options it offered. The resolution ladder, in
order:

1. clarify option — the last turn asked back and this question is one of its
   options → re-ask the original question with that option.
2. pronoun / possessive with no entity of its own → substitute the subject.
3. ellipsis with no verb ("and pricing?", "for testers?") → subject + question.
4. ordinal ("the second one") → the matching document from the last answer.
5. comparative ("what about <entity>") → the previous question with the entity
   swapped.
6. two-turn reach — if 2–5 find nothing in the immediately previous turn, try
   the turn before it, then stop.

When a pronoun has no subject to resolve to, or the reference matches two
subjects equally, it returns a clarify with 2–4 chips instead of a rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN = re.compile(r"[a-z0-9]+")
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
    "with",
    "that",
    "this",
    "it",
    "as",
    "by",
    "at",
    "from",
    "about",
    "me",
    "my",
}
# pronoun / possessive with no entity of its own
_PRONOUN = re.compile(
    r"\b(it'?s?|its|they|them|their|theirs|the same|there|"
    r"this|that|these|those|one|the (?:product|tool|platform|service|solution|offering))\b",
    re.I,
)
_ORDINAL = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": -1}
_INTENT_NO_ENTITY = re.compile(
    r"\b(compare|comparison|difference|differ|integrate|migrate)\b", re.I
)
_HAS_VERB = re.compile(
    r"\b(is|are|was|were|do|does|did|has|have|can|will|should|use|uses|work|works|"
    r"cost|costs|support|supports|provide|provides|run|runs|make|made|help|helps|"
    r"handle|handles|mean|means|need|needs)\b",
    re.I,
)


def _toks(s: str) -> list[str]:
    return _TOKEN.findall((s or "").lower())


def _content(s: str) -> list[str]:
    return [t for t in _toks(s) if t not in _STOP]


def subject_in(text: str, subjects: dict) -> str:
    """The distinctive subject the text names (a product/entity token that titles
    exactly one document), or "". Matches whole tokens — never a substring, so
    "testers" does not match a "test" subject — latest mention wins."""
    toks = _toks(text)
    pos = {t: i for i, t in enumerate(toks)}
    found, at = "", -1
    for key in subjects:
        i = pos.get(key, -1)
        if i > at:
            at, found = i, key
    return found


@dataclass
class Turn:
    question: str = ""
    subject: str = ""  # distinctive subject key of this turn, if any
    answer_docs: list[str] = field(default_factory=list)
    kind: str = "answer"
    options: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    question: str  # the (possibly rewritten) question to retrieve on
    understood_as: str | None = None  # shown under the bubble when a rewrite happened
    clarify: dict | None = None  # {"chips": [...], "reason": "..."} when ambiguous


def _sticky_subject(turns: list[Turn], subjects: dict) -> tuple[str, int]:
    """Most recent turn (within the two-turn window) that names a subject."""
    for depth, t in enumerate(reversed(turns[-2:])):
        s = t.subject or subject_in(t.question, subjects)
        if s:
            return s, depth
    return "", -1


def _recent_subjects(turns: list[Turn], subjects: dict, n: int = 3) -> list[str]:
    seen, out = set(), []
    for t in reversed(turns):
        s = t.subject or subject_in(t.question, subjects)
        if s and s not in seen:
            seen.add(s)
            out.append(subjects.get(s, s))
        if len(out) >= n:
            break
    return out


def resolve(question: str, turns: list[Turn], subjects: dict, bank_subjects=None) -> Resolution:
    """Rewrite ``question`` from the two-turn window, or ask back. ``subjects`` maps
    a distinctive lowercase key to its display name; ``turns`` is oldest→newest."""
    q = (question or "").strip()
    turns = turns or []
    here = subject_in(q, subjects)

    # 1) clarify option — the previous turn asked back and this answers it.
    if turns and turns[-1].kind == "clarify" and turns[-1].options:
        qc = set(_content(q))
        for opt in turns[-1].options:
            oc = set(_content(opt))
            overlap = len(qc & oc) / (len(oc) or 1)
            if q.lower() == opt.lower() or (oc and overlap >= 0.6):
                orig = turns[-1].question or opt
                rewritten = re.sub(_PRONOUN, opt, orig) if _PRONOUN.search(orig) else opt
                return Resolution(rewritten, understood_as=rewritten)

    # 5) comparative continuation — "what about <entity>": swap the new entity
    # into the previous question's intent. Checked before the self-contained
    # short-circuit, since the follow-up names its own (new) entity.
    if turns and re.match(r"^(what about|how about|and)\b", q, re.I) and here:
        prev = turns[-1].question
        prev_sub = subject_in(prev, subjects)
        if prev_sub and prev_sub != here:
            rewritten = re.sub(re.escape(subjects[prev_sub]), subjects[here], prev, flags=re.I)
            if rewritten.lower() != prev.lower():
                return Resolution(rewritten, understood_as=rewritten)

    # A question that already names its own subject is self-contained.
    if here:
        return Resolution(q)

    has_pronoun = bool(_PRONOUN.search(q))
    is_ellipsis = not _HAS_VERB.search(q) and (
        bool(re.match(r"^(and|what about|how about|in |for |with |on )\b", q, re.I))
        or len(_content(q)) <= 3
    )
    ordinal_key = next((k for k in _ORDINAL if re.search(rf"\b{k}\b", q, re.I)), None)

    subject_key, _depth = _sticky_subject(turns, subjects)
    subject = subjects.get(subject_key, subject_key)

    # 5) comparative continuation — "what about <entity>": swap the entity into
    # the previous question's intent.
    if turns and re.match(r"^(what about|how about|and)\b", q, re.I):
        m_subject = subject_in(q, subjects)  # a *new* entity named in the follow-up
        prev = turns[-1].question
        if m_subject and prev and subject_in(prev, subjects):
            rewritten = re.sub(
                re.escape(subjects[subject_in(prev, subjects)]),
                subjects[m_subject],
                prev,
                flags=re.I,
            )
            if rewritten.lower() != prev.lower():
                return Resolution(rewritten, understood_as=rewritten)

    # 4) ordinal — resolve to the matching document of the last answer.
    if ordinal_key is not None and turns and turns[-1].answer_docs:
        docs = turns[-1].answer_docs
        idx = _ORDINAL[ordinal_key]
        if -len(docs) <= idx < len(docs):
            doc = docs[idx]
            rewritten = f"{q} ({doc})"
            return Resolution(rewritten, understood_as=rewritten)

    # 2/3) pronoun or ellipsis — need a subject to resolve to.
    if has_pronoun or is_ellipsis:
        if subject:
            if has_pronoun:
                rewritten = re.sub(_PRONOUN, subject, q)
            else:  # ellipsis: subject + question
                tail = re.sub(r"^(and|what about|how about)\b", "", q, flags=re.I).strip(" ?")
                rewritten = f"{subject} {tail}".strip()
            return Resolution(rewritten, understood_as=rewritten)
        # pronoun with nothing to resolve to → ask back
        chips = _recent_subjects(turns, subjects) or (bank_subjects or [])[:3]
        if chips:
            return Resolution(q, clarify={"chips": chips[:3], "reason": "Which one do you mean?"})

    # intent with no entity ("compare", "difference") and no subject → ask back.
    if _INTENT_NO_ENTITY.search(q) and not subject:
        chips = _recent_subjects(turns, subjects) or (bank_subjects or [])[:3]
        if chips:
            return Resolution(q, clarify={"chips": chips[:3], "reason": "Compare which two?"})

    return Resolution(q)
