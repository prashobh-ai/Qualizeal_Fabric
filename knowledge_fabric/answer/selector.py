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


def classify(question: str, selected, grounding: float, graph_used: bool) -> dict:
    q = question.lower()
    toks = _qtok(question)
    n_tokens = len(toks)
    doc_spread = len({c.passage.document_id for c in selected}) if selected else 0
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

    # evidence spread pushes cost up (needs cross-document synthesis)
    if doc_spread >= 3 and level < 3:
        level = 3
        reasons.append({"code": "multi_document", "detail": f"evidence spans {doc_spread} documents",
                        "signal": doc_spread})
    elif doc_spread >= 2 and level < 2:
        level = 2
        reasons.append({"code": "two_document", "detail": "evidence spans 2 documents", "signal": doc_spread})

    if graph_used:
        reasons.append({"code": "graph_multi_hop", "detail": "graph pulled connected cross-doc evidence",
                        "signal": True})
        if level < 2:
            level = 2

    # long question => more synthesis
    if n_tokens >= 12 and level < 3:
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
    lead = {
        "lookup": "Answered by the extractive core with no model — a direct look-up",
        "fast": "Routed to the fast tier — light synthesis",
        "reason": "Routed to the deep tier — this needs reasoning across evidence",
        "escalation": "Escalated to the strongest tier",
    }[name]
    tail = f" across {doc_spread} documents" if doc_spread >= 2 else ""
    return f"{lead}{tail} (grounding {round(grounding, 2)})."
