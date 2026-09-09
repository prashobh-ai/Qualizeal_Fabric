"""Answer service — the single governed path for people, agents and apps (I7).

Connect → Understand → Decide → Answer. Steps (Build Plan Section 10 + roadmap
WS2/WS3 + Stage-2), each emitting a span on one answer trace:
  A. identity & permission filter injected INSIDE retrieval (I6)
  B. reasoning plan: multistep / conditional / compare questions are decomposed
     and each step runs through this same governed path (Stage-2, Section A)
  C. five-layer cache check (answer/embedding/retrieval/graph/prompt-memory)
  D. hybrid retrieval (lexical + vector) fused with RRF, then AUTHORITY boost
     (source rank × curator-marked authoritative), tiered, MMR
  E. graph expansion for connected/cross-document evidence
  F. grounding gate: five signals combined as a weighted geometric mean
  G. clarify-back on weak/ambiguous evidence (first-class metric, I3)
  H. 4-level model selector with an explainable "why" (WS2), budget-capped,
     escalating only when confidence fails; the multi-model gateway names the
     model that ran; complexity is labelled simple / medium / complex
  I. extractive compose + citation post-check dropping unsupported claims (I1,
     I2), language-tagged, with a replayable trajectory id, the authoritative
     source among the citations, and the dataset version answered against

With the model disabled the core still returns extractive, cited answers (I4).
Every answer records subject/roles, level+why, model, complexity, tokens in/out,
cache savings, language, sources, dataset version and any reasoning trace.
"""
from __future__ import annotations

import math
import re

from ..adapters.embedder import cosine
from ..adapters.model import COMPLEXITY_OF_TIER, model_for_tier
from ..contracts.types import (
    Answer, AnswerKind, Candidate, Citation, Principal, new_id, now_ms,
)
from ..governance import authority
from ..stores import versioning
from . import lang as langmod
from . import reasoning
from . import selector as sel

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
         "what", "which", "how", "who", "when", "where", "does", "do", "did", "was",
         "were", "be", "with", "that", "this", "it", "as", "by", "at", "from", "our",
         "we", "you", "i", "can", "will", "should", "must", "may"}
_RRF_K = 60
_EPS = 1e-6
_WEIGHTS = {"retrieval": 1.0, "semantic": 1.2, "coverage": 1.2,
            "agreement": 0.8, "resolvable": 1.0}
_ESCALATE_FLOOR = 0.35
_SYSTEM_PREAMBLE = "Rephrase the cited evidence faithfully; add nothing."
_TIER_ORDER = {"none": 0, "fast": 1, "deep": 2, "escalation": 3}
_LEVEL_CX = {1: "simple", 2: "medium", 3: "complex", 4: "complex"}   # selector level -> query complexity
_CX_ORDER = {"simple": 0, "medium": 1, "complex": 2}


def _qtokens(q: str) -> list[str]:
    return [t for t in _TOKEN.findall(q.lower()) if t not in _STOP]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _max_cx(*labels: str) -> str:
    return max((l for l in labels if l), key=lambda l: _CX_ORDER.get(l, 0), default="simple")


class AnswerService:
    def __init__(self, platform):
        self.p = platform

    # =================================================================
    def ask(self, principal: Principal, question: str, k: int = 6,
            allow_model: bool = True, _nested: bool = False) -> Answer:
        p = self.p
        tenant = principal.tenant
        trace_id = new_id("traj_")
        accessible = principal.accessible_acls()
        traj = {"selected": [], "graph_node_keys": []}
        qlang = langmod.detect(question)
        rq = langmod.translate_query_to_en(question, qlang, p.model, tenant)  # retrieve on English
        plan = reasoning.plan(rq)
        span_name = "answer.step" if _nested else "answer"

        with p.telemetry.span(span_name, {"tenant": tenant, "trace_id": trace_id, "stage": "answer",
                                          "subject": principal.subject, "roles": principal.roles,
                                          "lang": qlang}) as span:
            # A. policy + rate limit (the parent request already did this for steps)
            if not _nested:
                if not p.policy.rate_check(tenant, principal.subject):
                    self._audit(principal, "ask", "rate_limited", trace_id)
                    return self._plain(AnswerKind.GAP, "Rate limit exceeded — retry shortly.",
                                       trace_id, tenant, span, 0.0, qlang, principal)
                pol = p.policy.check(principal, "ask", {})
                if pol.decision.value == "deny":
                    self._audit(principal, "ask", f"denied:{pol.reason}", trace_id)
                    return self._plain(AnswerKind.GAP, f"Request denied: {pol.reason}",
                                       trace_id, tenant, span, 0.0, qlang, principal)

            # B. cache layer 1 — full answer cache (also serves repeated reasoned questions)
            cached = p.cache.get_answer(tenant, question, accessible)
            if cached:
                pay = cached["payload"]
                saved = cached["cost"]
                span.set(kind="answer", level="cache", tier=pay["tier"], cost=0.0, tokens=0,
                         cache_hit=1, cache_technique="answer_cache", cost_saved=saved,
                         grounding=pay["grounding_score"], citations_count=len(pay["citations"]),
                         sources=[c["document_title"] for c in pay["citations"]], why=pay.get("why"),
                         model_name="cache", complexity=pay.get("complexity", ""),
                         dataset_version=pay.get("dataset_version", 0))
                if not _nested:
                    self._audit(principal, "ask", "answered:cache", trace_id)
                return self._from_cache(pay, trace_id, tenant, qlang, saved)

            # C. multistep / conditional / compare → decompose, run each step governed
            if not _nested and plan["mode"] != "single":
                return self._ask_reasoned(principal, question, plan, trace_id, span, qlang,
                                          k, allow_model)

            qvec = p.cache.get_embedding(rq)
            if qvec is None:
                qvec = p.embedder.embed([rq])[0]
                p.cache.put_embedding(rq, qvec)

            # D. hybrid retrieval + RRF (retrieval cache) + authority boost
            with p.telemetry.span("answer.retrieve",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "retrieve"}):
                fused = p.cache.get_retrieval(tenant, rq, accessible)
                if fused is None:
                    vec_hits = p.vindex.search(tenant, qvec, 20, accessible)
                    lex_hits = p.lindex.search(tenant, rq, 20, accessible)
                    fused = self._rrf(vec_hits, lex_hits)
                    p.cache.put_retrieval(tenant, rq, accessible, fused)
                fused = self._authority_boost(tenant, fused)

            candidates: list[Candidate] = []
            for pid, fscore in fused[: k * 3]:
                pas = p.passages.get(tenant, pid)
                if pas:
                    candidates.append(Candidate(passage=pas, fused_score=fscore))

            # E. graph expansion -----------------------------------------
            graph_before = len(candidates)
            with p.telemetry.span("answer.graph",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "graph"}):
                candidates = self._graph_expand(tenant, candidates, accessible, qvec, traj, rq)
            graph_used = len(candidates) > graph_before

            dsv = versioning.current_dataset(p, tenant)
            if not candidates:
                if not _nested:
                    self._audit(principal, "ask", "gap:no-evidence", trace_id)
                    p.curation.add(tenant, question, "gap", now_ms())
                span.set(level="gap", tier="none", dataset_version=dsv)
                return self._plain(AnswerKind.GAP,
                                   "No supporting evidence exists in your accessible corpus.",
                                   trace_id, tenant, span, 0.0, qlang, principal, dsv)

            selected = self._mmr(qvec, candidates, k)
            traj["selected"] = [c.passage.id for c in selected]

            # F. grounding gate ------------------------------------------
            with p.telemetry.span("answer.ground",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "ground"}):
                signals, g = self._grounding(rq, qvec, selected)
            threshold = p.grounding_threshold
            span.set(grounding=g, signals=signals, dataset_version=dsv)

            # G. clarify-back / declared gap -----------------------------
            if g < threshold:
                if g >= threshold * 0.5:
                    ambiguous = self._ambiguous(selected)
                    cq = self._clarify_question(question, selected, ambiguous)
                    if not _nested:
                        self._audit(principal, "ask", "clarify", trace_id)
                    span.set(kind="clarify", level="clarify", citations_count=0, tier="none",
                             complexity=plan["complexity"])
                    return Answer(AnswerKind.CLARIFY, cq, [], round(g, 3), trace_id, 0.0, 0, "none",
                                  grounding_score=g, clarify_back=cq, tenant=tenant, lang=qlang,
                                  complexity=plan["complexity"], dataset_version=dsv,
                                  model_name=model_for_tier("none"),
                                  why={"level_name": "clarify", "explain": "Evidence too weak/ambiguous to answer."})
                if not _nested:
                    self._audit(principal, "ask", "gap:below-threshold", trace_id)
                    p.curation.add(tenant, question, "gap", now_ms())
                span.set(kind="gap", level="gap", citations_count=0, tier="none",
                         complexity=plan["complexity"])
                return Answer(AnswerKind.GAP,
                              "The corpus does not contain enough grounded evidence to answer this. "
                              "Logged to the gap backlog.", [], round(g, 3), trace_id, 0.0, 0, "none",
                              grounding_score=g, tenant=tenant, lang=qlang, complexity=plan["complexity"],
                              dataset_version=dsv, model_name=model_for_tier("none"),
                              why={"level_name": "gap", "explain": "Below the grounding threshold."})

            # H. model selector (4 levels, explainable why) -------------
            # Titles of the reranked top-5 let the selector apply the L0.4
            # definition rule ('what is X' → Level 1 when X matches the top
            # document's title). Authority (0-100) is passed when available.
            doc_titles = {}
            for c in list(selected)[:5]:
                did = c.passage.document_id
                if did not in doc_titles:
                    d = self.p.documents.get(tenant, did)
                    doc_titles[did] = (d or {}).get("title", "")
            decision = sel.classify(rq, selected, g, graph_used, doc_titles=doc_titles)
            tier = decision["tier"]
            # complexity describes the QUERY (form + evidence spread), so it is fixed here from
            # the selector's level and never lowered by a later budget/model-off degradation
            complexity = _max_cx(plan["complexity"], _LEVEL_CX.get(decision["level"], "simple"))
            if tier != "none" and (not allow_model or not p.model.available()):
                tier = "none"
                decision = {**decision, "tier": "none",
                            "reasons": decision["reasons"] + [{"code": "model_disabled",
                                "detail": "model unavailable — extractive core", "signal": True}]}
            if tier != "none":
                est = self._estimate_cost(question, selected, tier)
                agent = principal.subject if principal.agent else None
                if not p.policy.try_spend(tenant, est, agent):
                    tier = "none"
                    decision = {**decision, "tier": "none",
                                "reasons": decision["reasons"] + [{"code": "budget_degrade",
                                    "detail": "tenant budget cap reached — degraded to extractive", "signal": True}]}

            # I. compose from passages only + citation post-check -------
            with p.telemetry.span("answer.compose",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "compose"}):
                text, citations, cost, tin, tout, saved, model_name = self._compose(
                    principal, rq, selected, tier)
            confidence = self._confidence(g, citations, selected)

            if (decision["level"] < 4 and confidence < _ESCALATE_FLOOR and tier != "none"
                    and p.model.available()):
                decision = sel.escalate(decision, confidence, _ESCALATE_FLOOR)
                tier = decision["tier"]
                est = self._estimate_cost(question, selected, tier)
                if p.policy.try_spend(tenant, est, principal.subject if principal.agent else None):
                    text, citations, cost, tin, tout, saved, model_name = self._compose(
                        principal, rq, selected, tier)
                    confidence = self._confidence(g, citations, selected)

            # L2.3 — surface the five grounding signals (the Trust bars) and the
            # retrieved-vs-cited counts (Sources: found / cited) on the why-card.
            decision = {**decision, "complexity": complexity, "model_name": model_name,
                        "signals": signals, "retrieved": len(selected)}

            if not citations:
                if not _nested:
                    self._audit(principal, "ask", "gap:post-check", trace_id)
                span.set(kind="gap", level="gap", tier=tier, cost=cost, tokens=tin + tout,
                         citations_count=0, model_name=model_name, complexity=complexity)
                return Answer(AnswerKind.GAP, "No claim could be grounded to a citation.", [],
                              round(g, 3), trace_id, cost, tin + tout, tier, grounding_score=g,
                              tenant=tenant, lang=qlang, model_name=model_name, complexity=complexity,
                              dataset_version=dsv)

            text = self._localize(text, qlang, principal, tier)
            sources = [c.document_title for c in citations]
            auth = self._authority_card(tenant, citations)

            answer = Answer(AnswerKind.ANSWER, text, citations, confidence, trace_id, cost,
                            tin + tout, tier, grounding_score=g, tenant=tenant,
                            level=decision["level"], why=decision, lang=qlang,
                            cost_saved=saved, tokens_in=tin, tokens_out=tout,
                            model_name=model_name, complexity=complexity,
                            authoritative_source=auth, dataset_version=dsv)
            p.cache.put_answer(tenant, question, accessible, answer.to_dict(), cost)

            if not _nested:
                self._audit(principal, "ask", "answered", trace_id)
            span.set(kind="answer", tier=tier, cost=cost, tokens=tin + tout, tokens_in=tin,
                     tokens_out=tout, citations_count=len(citations), level=decision["level_name"],
                     why=decision, cost_saved=saved, cache_technique="prompt_memory" if saved else "",
                     cache_hit=1 if saved else 0, sources=sources, trajectory=traj,
                     model_name=model_name, complexity=complexity, dataset_version=dsv)
            return answer

    # ================= multistep / conditional reasoning =============
    def _ask_reasoned(self, principal, question, plan, trace_id, span, qlang, k, allow_model):
        """Run a decomposed plan: every step goes through ask(_nested=True), i.e. the
        same permission filter, retrieval, grounding gate, selector and budget."""
        p = self.p
        tenant = principal.tenant
        collected: list[Answer] = []

        def ask_fn(sub_q: str) -> Answer:
            a = self.ask(principal, sub_q, k=k, allow_model=allow_model, _nested=True)
            collected.append(a)
            return a

        res = reasoning.execute(plan, ask_fn)
        cost = sum(a.cost for a in collected)
        tin = sum(a.tokens_in for a in collected)
        tout = sum(a.tokens_out for a in collected)
        saved = sum(a.cost_saved for a in collected)
        answered = [a for a in collected if a.kind == AnswerKind.ANSWER]
        tier = max((a.tier for a in collected), key=lambda t: _TIER_ORDER.get(t, 0), default="none")
        models = sorted({a.model_name for a in answered if a.model_name})
        model_name = models[0] if len(models) == 1 else (", ".join(models) if models else model_for_tier("none"))
        level = max((a.level for a in collected), default=0)
        grounding = min((a.grounding_score for a in answered), default=0.0)
        complexity = _max_cx(res.get("complexity", plan["complexity"]),
                             *[a.complexity for a in collected])
        dsv = versioning.current_dataset(p, tenant)
        surface = reasoning.to_surface(res)
        why = {"level_name": f"reasoning:{res['mode']}", "explain": res.get("explain", ""),
               "reasons": [{"code": f"reasoning_{res['mode']}", "detail": res.get("explain", ""),
                            "signal": len(res["steps"])}]
                          + [r for a in answered for r in (a.why or {}).get("reasons", [])][:6],
               "complexity": complexity, "model_name": model_name, "level": level}

        kind = res.get("kind", "answer")
        if kind == "clarify":
            cq = res.get("clarify_back") or "One step of this question needs clarification."
            self._audit(principal, "ask", "clarify", trace_id)
            span.set(kind="clarify", level="clarify", citations_count=0, tier=tier, cost=cost,
                     tokens=tin + tout, tokens_in=tin, tokens_out=tout, model_name=model_name,
                     complexity=complexity, dataset_version=dsv, reasoning=surface, why=why)
            return Answer(AnswerKind.CLARIFY, cq, [], round(grounding, 3), trace_id, cost, tin + tout,
                          tier, grounding_score=grounding, clarify_back=cq, tenant=tenant, lang=qlang,
                          level=level, why=why, model_name=model_name, complexity=complexity,
                          dataset_version=dsv, reasoning=surface, tokens_in=tin, tokens_out=tout,
                          cost_saved=saved)
        if kind == "gap" or not res.get("citations"):
            self._audit(principal, "ask", f"gap:step:{res.get('gap_step')}", trace_id)
            p.curation.add(tenant, question, "gap", now_ms())
            span.set(kind="gap", level="gap", citations_count=0, tier=tier, cost=cost,
                     tokens=tin + tout, tokens_in=tin, tokens_out=tout, model_name=model_name,
                     complexity=complexity, dataset_version=dsv, reasoning=surface, why=why)
            return Answer(AnswerKind.GAP, res.get("final_text") or
                          "A step of this question has no grounded evidence in the corpus.",
                          [], round(grounding, 3), trace_id, cost, tin + tout, tier,
                          grounding_score=grounding, tenant=tenant, lang=qlang, level=level, why=why,
                          model_name=model_name, complexity=complexity, dataset_version=dsv,
                          reasoning=surface, tokens_in=tin, tokens_out=tout, cost_saved=saved)

        citations = res["citations"]
        text = self._localize(res["final_text"], qlang, principal, tier)
        auth = self._authority_card(tenant, citations)
        self._audit(principal, "ask", f"answered:{res['mode']}", trace_id)
        answer = Answer(AnswerKind.ANSWER, text, citations, round(res["confidence"], 3), trace_id,
                        cost, tin + tout, tier, grounding_score=grounding, tenant=tenant,
                        level=level, why=why, lang=qlang, cost_saved=saved, tokens_in=tin,
                        tokens_out=tout, model_name=model_name, complexity=complexity,
                        authoritative_source=auth, dataset_version=dsv, reasoning=surface)
        p.cache.put_answer(tenant, question, principal.accessible_acls(), answer.to_dict(), cost)
        span.set(kind="answer", tier=tier, cost=cost, tokens=tin + tout, tokens_in=tin,
                 tokens_out=tout, citations_count=len(citations), level=f"reasoning:{res['mode']}",
                 why=why, cost_saved=saved, cache_hit=1 if saved else 0,
                 cache_technique="prompt_memory" if saved else "",
                 sources=[c.document_title for c in citations], grounding=grounding,
                 model_name=model_name, complexity=complexity, dataset_version=dsv,
                 reasoning=surface)
        return answer

    # ================= step helpers =================================
    def _rrf(self, vec_hits, lex_hits) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        for rank, (pid, _) in enumerate(vec_hits):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, (pid, _) in enumerate(lex_hits):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def _authority_boost(self, tenant, fused):
        """Re-weight fused hits by source rank and curator-marked authority."""
        passage_doc = {}
        for pid, _ in fused:
            pas = self.p.passages.get(tenant, pid)
            if pas:
                passage_doc[pid] = (pas.document_id, pas.provenance.source)
        try:
            return authority.boost(self.p, tenant, fused, passage_doc)
        except Exception:
            return fused

    def _authority_card(self, tenant, citations):
        try:
            card = authority.authoritative_source(self.p, tenant, citations)
            if card is not None:
                card = dict(card)
                card["conflicts"] = authority.conflicts(self.p, tenant, citations)
            return card
        except Exception:
            return None

    def _graph_expand(self, tenant, candidates, accessible, qvec, traj, question):
        if not candidates:
            return candidates
        have = {c.passage.id for c in candidates}
        node_keys = self.p.cache.get_graph(tenant, question, accessible)
        if node_keys is None:
            node_keys = set()
            for c in candidates[:3]:
                for nid in self.p.graph_repo.node_for_passage(tenant, c.passage.id):
                    for e in self.p.graph.neighbors(tenant, nid, hops=1):
                        for other in (e["src"], e["dst"]):
                            row = self.p.db.one(
                                "SELECT canonical_key FROM graph_nodes WHERE tenant=? AND id=?",
                                (tenant, other))
                            if row:
                                node_keys.add(row["canonical_key"])
            node_keys = sorted(node_keys)
            self.p.cache.put_graph(tenant, question, accessible, node_keys)
        traj["graph_node_keys"] = node_keys
        for pas in self.p.passages.for_tenant(tenant):
            if pas.id in have:
                continue
            if not (set(self.p.passages.acl_of(tenant, pas.id)) & set(accessible)):
                continue
            low = pas.text.lower()
            if any(key in low for key in node_keys if len(key) > 3):
                v = self._vec_of(tenant, pas.id)
                candidates.append(Candidate(passage=pas, fused_score=0.0,
                                            vector_score=cosine(qvec, v) if v else 0.0,
                                            graph_hops=1, source_of="graph"))
        return candidates

    def _vec_of(self, tenant, pid):
        import json
        r = self.p.db.one("SELECT vec FROM embeddings WHERE tenant=? AND passage_id=?", (tenant, pid))
        return json.loads(r["vec"]) if r else None

    def _mmr(self, qvec, candidates, k, lam=0.7):
        pool = list(candidates)
        vecs = {c.passage.id: (self._vec_of(c.passage.tenant, c.passage.id) or []) for c in pool}
        for c in pool:
            if not c.vector_score and vecs[c.passage.id]:
                c.vector_score = cosine(qvec, vecs[c.passage.id])
        selected = []
        while pool and len(selected) < k:
            best, best_score = None, -1e9
            for c in pool:
                rel = 0.5 * c.vector_score + 0.5 * min(1.0, c.fused_score * 100)
                red = max((cosine(vecs[c.passage.id], vecs[s.passage.id])
                           for s in selected if vecs[c.passage.id] and vecs[s.passage.id]), default=0.0)
                score = lam * rel - (1 - lam) * red
                if score > best_score:
                    best, best_score = c, score
            selected.append(best)
            pool.remove(best)
        return selected

    def _grounding(self, question, qvec, selected):
        qtok = set(_qtokens(question))
        ceiling = 2.0 / (_RRF_K + 1)
        s1 = min(1.0, max((c.fused_score for c in selected), default=0.0) / ceiling)
        s2 = max(0.0, min(1.0, max((c.vector_score for c in selected), default=0.0)))
        covered = set()
        for c in selected:
            covered |= (qtok & set(_TOKEN.findall(c.passage.text.lower())))
        s3 = (len(covered) / len(qtok)) if qtok else 0.5
        corroborating = sum(1 for c in selected
                            if qtok & set(_TOKEN.findall(c.passage.text.lower())))
        s4 = min(1.0, corroborating / min(3, max(1, len(selected)))) if qtok else s2
        s5 = sum(1 for c in selected if c.passage.coordinate.locator) / max(1, len(selected))
        signals = {"retrieval": s1, "semantic": s2, "coverage": s3, "agreement": s4, "resolvable": s5}
        num = sum(_WEIGHTS[key] * math.log(max(v, _EPS)) for key, v in signals.items())
        g = math.exp(num / sum(_WEIGHTS.values()))
        return {key: round(v, 4) for key, v in signals.items()}, round(g, 4)

    def _ambiguous(self, selected):
        docs = {}
        for c in selected[:4]:
            docs.setdefault(c.passage.document_id, c.fused_score)
        top = sorted(docs.values(), reverse=True)
        return len(top) >= 2 and top[1] > 0 and (top[0] - top[1]) / (top[0] + _EPS) < 0.15

    def _clarify_question(self, question, selected, ambiguous):
        titles = []
        for c in selected[:3]:
            d = self.p.documents.get(c.passage.tenant, c.passage.document_id)
            if d and d["title"] not in titles:
                titles.append(d["title"])
        if ambiguous and len(titles) >= 2:
            return (f"Your question could refer to more than one area — I found relevant material in "
                    f"“{titles[0]}” and “{titles[1]}”. Which did you mean, or can you add a detail?")
        return ("I don't have strongly grounded evidence for that yet. Could you narrow the question "
                "— e.g. name the specific document, release, or requirement you mean?")

    def _estimate_cost(self, question, selected, tier):
        toks = len(question.split()) + sum(len(c.passage.text.split()) for c in selected)
        return toks * (5e-6 if tier in ("deep", "escalation") else 1e-6)

    def _confidence(self, g, citations, selected):
        return round(min(0.99, g * 0.9 + 0.05 * len(citations) / max(1, len(selected))), 3)

    def _compose(self, principal, question, selected, tier):
        qtok = set(_qtokens(question))
        ranked = []
        for c in selected:
            best_sent, best_ov = c.passage.abstract, 0.0
            for s in _sentences(c.passage.text) or [c.passage.text]:
                ov = len(qtok & set(_TOKEN.findall(s.lower())))
                if ov >= best_ov:
                    best_ov, best_sent = ov, s
            ranked.append((c, best_sent, best_ov))
        ranked.sort(key=lambda x: (x[0].vector_score + x[2]), reverse=True)

        cite_map, citations, parts = {}, [], []
        for c, sent, _ in ranked[:3]:
            d = self.p.documents.get(c.passage.tenant, c.passage.document_id)
            title = d["title"] if d else "document"
            if c.passage.id not in cite_map:
                cite_map[c.passage.id] = len(citations) + 1
                citations.append(Citation(document_id=c.passage.document_id, document_title=title,
                                          coordinate=c.passage.coordinate, passage_id=c.passage.id,
                                          snippet=sent[:200]))
            parts.append(f"{sent} [{cite_map[c.passage.id]}]")
        extractive = " ".join(parts)

        cost = tin = tout = 0
        saved = 0.0
        text = extractive
        model_name = model_for_tier("none")
        if tier != "none" and self.p.model.available():
            msg = [{"role": "system", "content": _SYSTEM_PREAMBLE},
                   {"role": "user", "content": extractive}]
            out = self.p.model.complete(principal.tenant, tier, msg, {"temperature": 0.0})
            cost = out["cost"]
            usage = out.get("usage", {})
            tin = int(usage.get("in") or (len(_SYSTEM_PREAMBLE.split()) + len(extractive.split())))
            tout = int(usage.get("out") or max(1, len(out.get("text", "").split())))
            model_name = out.get("model_name") or model_for_tier(tier)
            input_cost = tin * (5e-6 if tier in ("deep", "escalation") else 1e-6)
            saved = self.p.cache.prompt_discount(_SYSTEM_PREAMBLE, input_cost)
            cost = max(0.0, cost - saved)
            self.p.policy.try_spend(principal.tenant, cost,
                                    principal.subject if principal.agent else None)
            checked = self._postcheck(out["text"], selected)
            text = checked or extractive
        return text, citations, cost, tin, tout, saved, model_name

    def _postcheck(self, text, selected):
        corpus = [set(_TOKEN.findall(c.passage.text.lower())) for c in selected]
        kept = []
        for s in _sentences(text):
            stok = set(_TOKEN.findall(s.lower())) - _STOP
            if not stok:
                continue
            if re.search(r"\[\d+\]", s):
                kept.append(s); continue
            support = max((len(stok & ct) / max(1, len(stok)) for ct in corpus), default=0.0)
            if support >= 0.5:
                kept.append(s)
        return " ".join(kept)

    def _localize(self, text, qlang, principal, tier):
        if qlang == "en":
            return text
        return f"[{langmod.label(qlang)} — cited to the English source of truth]\n{text}"

    # ================= plumbing =====================================
    def _audit(self, principal, action, decision, trace_id):
        self.p.audit.write(principal.tenant, principal.subject, principal.agent, action,
                           "answer", decision, trace_id, now_ms())

    def _from_cache(self, pay, trace_id, tenant, qlang, saved):
        from ..contracts.types import Coordinate, CoordinateKind
        cites = [Citation(c["document_id"], c["document_title"],
                          Coordinate(CoordinateKind(c["coordinate"]["kind"]), c["coordinate"]["locator"]),
                          c["passage_id"], c["snippet"]) for c in pay["citations"]]
        return Answer(AnswerKind.ANSWER, pay["answer_text"], cites, pay["confidence"], trace_id,
                      0.0, 0, pay["tier"], grounding_score=pay["grounding_score"], tenant=tenant,
                      level=pay.get("level", 0), why=pay.get("why"), lang=qlang,
                      cache_hit=True, cost_saved=saved, model_name=pay.get("model_name", "cache"),
                      complexity=pay.get("complexity", ""),
                      authoritative_source=pay.get("authoritative_source"),
                      dataset_version=pay.get("dataset_version", 0), reasoning=pay.get("reasoning"))

    def _plain(self, kind, text, trace_id, tenant, span, g, qlang, principal, dsv=0):
        span.set(kind=kind.value, grounding=g, citations_count=0, tier="none",
                 subject=principal.subject, roles=principal.roles, lang=qlang, dataset_version=dsv)
        return Answer(kind, text, [], round(g, 3), trace_id, 0.0, 0, "none",
                      grounding_score=g, tenant=tenant, lang=qlang, dataset_version=dsv,
                      model_name=model_for_tier("none"))
