"""Model selector — four levels, decided with cheap signals and no AI model
(WS2 · UNDERSTAND & DECIDE).

Roadmap: "Four levels: look it up → reason about it · Decision takes
milliseconds, uses no AI model · Escalates only when confidence fails."

The selector classifies query complexity from signals that cost nothing to
compute (question form, length, evidence spread, multi-hop, grounding) and
returns an explainable decision: the chosen level + a list of reason codes +
one plain-language sentence — the "why-card" the Ask console shows and the
dashboard aggregates.

Levels → tiers:
  L1 lookup     → tier none  (extractive core, no model — the cheapest tier)
  L2 fast       → tier fast
  L3 reason     → tier deep
  L4 escalation → tier escalation  (only when confidence fails)
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
         "what", "which", "how", "who", "when", "where", "does", "do", "did", "was"}
_REASONING = ("why", "how", "compare", "difference", "explain", "analyse", "analyze",
              "evaluate", "recommend", "implication", "trade-off", "tradeoff", "versus", "vs")
_LOOKUP = ("what", "which", "who", "when", "where", "list", "define", "status")

LEVELS = {
    1: ("lookup", "none"),
    2: ("fast", "fast"),
    3: ("reason", "deep"),
    4: ("escalation", "escalation"),
}


def _qtok(q):
    return [t for t in _TOKEN.findall(q.lower()) if t not in _STOP]


_DEFINITION = re.compile(r"^\s*(?:what|who)\s+(?:is|are)\s+(.+?)\??$|^\s*define\s+(.+?)\??$", re.I)


def _definition_target(question: str) -> str:
    """The subject of a 'what is X / who is X / define X' question, lowercased."""
    m = _DEFINITION.match(question.strip())
    if not m:
        return ""
    target = m.group(1) or m.group(2) or ""
    # drop a leading article and keep the salient noun tokens
    toks = [t for t in _TOKEN.findall(target.lower()) if t not in _STOP]
    return " ".join(toks)


def classify(question: str, selected, grounding: float, graph_used: bool,
             doc_titles: dict | None = None, doc_authority: dict | None = None) -> dict:
    q = question.lower()
    toks = _qtok(question)
    n_tokens = len(toks)
    # L0.4 — evidence spread is measured over the reranked top-5 only, so a
    # long tail of loosely-related documents cannot escalate a simple question.
    top5 = list(selected)[:5] if selected else []
    doc_spread = len({c.passage.document_id for c in top5})
    reasons = []

    is_reasoning = any(w in q for w in _REASONING)
    is_lookup = q.split()[:1] and q.split()[0] in _LOOKUP and not is_reasoning

    # base level from question form
    if is_reasoning:
        level = 3
        reasons.append({"code": "reasoning_intent", "detail": "question asks to reason/compare/explain",
                        "signal": next((w for w in _REASONING if w in q), "")})
    elif is_lookup and n_tokens <= 8:
        level = 1
        reasons.append({"code": "factual_lookup", "detail": "short factual look-up phrasing",
                        "signal": q.split()[0]})
    else:
        level = 2
        reasons.append({"code": "synthesis", "detail": "moderate synthesis question",
                        "signal": f"{n_tokens} content tokens"})

    # L0.4 — definition rule wins early: 'what is / who is / define <X>' where
    # <X> matches the top document's title (an authoritative source raises
    # confidence) resolves to Level 1 Quote it, and locks against escalation
    # by document spread. Authority is honoured when a 0-100 score is supplied
    # (wired in F2.1); until then a title match on the top document is enough.
    define_locked = False
    target = "" if is_reasoning else _definition_target(question)
    if target and top5:
        top_doc = top5[0].passage.document_id
        title = (doc_titles or {}).get(top_doc, "").lower()
        auth = (doc_authority or {}).get(top_doc)
        title_hit = bool(title) and any(w in title for w in target.split())
        authoritative = auth is None or auth >= 60
        if title_hit and authoritative:
            level = 1
            define_locked = True
            reasons.append({"code": "definition", "signal": target,
                            "detail": f"definition of '{target}' found in the top document title"})

    # evidence spread pushes cost up (needs cross-document synthesis). A lookup
    # is raised at most to Level 2 by document spread; only a reasoning/
    # comparison intent takes it to Level 3 (L0.4).
    if not define_locked:
        if doc_spread >= 3 and level < 3:
            if is_reasoning:
                level = 3
                reasons.append({"code": "multi_document", "detail": f"evidence spans {doc_spread} documents",
                                "signal": doc_spread})
            elif level < 2:
                level = 2
                reasons.append({"code": "multi_document", "signal": doc_spread,
                                "detail": f"evidence spans {doc_spread} documents (lookup capped at summarise)"})
        elif doc_spread >= 2 and level < 2:
            level = 2
            reasons.append({"code": "two_document", "detail": "evidence spans 2 documents", "signal": doc_spread})

    if graph_used and not define_locked:
        reasons.append({"code": "graph_multi_hop", "detail": "graph pulled connected cross-doc evidence",
                        "signal": True})
        if level < 2:
            level = 2

    # long question => more synthesis
    if n_tokens >= 12 and level < 3 and not define_locked:
        level = 3
        reasons.append({"code": "long_query", "detail": "long, information-dense question",
                        "signal": n_tokens})

    # strong grounding on a simple question keeps it cheap
    if level == 1 and grounding >= 0.7:
        reasons.append({"code": "high_grounding", "detail": "answer sits in one place, well grounded",
                        "signal": round(grounding, 3)})

    name, tier = LEVELS[level]
    complexity = round(min(1.0, 0.15 * level + 0.03 * n_tokens + 0.1 * doc_spread), 3)
    explain = _explain(name, reasons, doc_spread, grounding)
    return {"level": level, "level_name": name, "tier": tier, "reasons": reasons,
            "explain": explain, "complexity": complexity}


def escalate(decision: dict, confidence: float, floor: float) -> dict:
    """Escalate one level only when confidence fails (roadmap guardrail)."""
    if decision["level"] >= 4 or confidence >= floor:
        return decision
    new_level = min(4, decision["level"] + 1)
    name, tier = LEVELS[new_level]
    d = dict(decision)
    d.update(level=new_level, level_name=name, tier=tier,
             reasons=decision["reasons"] + [{"code": "confidence_fail",
                                             "detail": f"post-check confidence {round(confidence,3)} < {floor}",
                                             "signal": round(confidence, 3)}])
    d["explain"] = f"Escalated to {name}: initial confidence {round(confidence,3)} fell below {floor}."
    return d


def _explain(name, reasons, doc_spread, grounding) -> str:
    # Reader-facing wording only: the level is named by its reader word, never
    # by an internal code or the routing tier (L1.5 / D11 — this string is shown
    # verbatim in the answer card's "Why" row).
    lead = {
        "lookup": "Looked it up — a direct answer from the source",
        "fast": "Quoted the most relevant passages — a light synthesis",
        "reason": "Summarised across the evidence",
        "escalation": "Reasoned across the strongest evidence",
    }[name]
    tail = f" spanning {doc_spread} documents" if doc_spread >= 2 else ""
    return f"{lead}{tail} (grounding {round(grounding, 2)})."
