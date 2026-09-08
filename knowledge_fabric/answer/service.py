"""Answer service — the single governed path for people, agents and apps (I7).

Connect → Understand → Decide → Answer. Steps (Build Plan Section 10 + the
QualiZeal roadmap WS2/WS3), each emitting a span on one answer trace:
  A. identity & permission filter injected INSIDE retrieval (I6)
  B. five-layer cache check (answer/embedding/retrieval/graph/prompt-memory)
  C. hybrid retrieval (lexical + vector) fused with RRF, tiered, MMR
  D. graph expansion for connected/cross-document evidence
  E. grounding gate: five signals combined as a weighted geometric mean
  F. clarify-back on weak/ambiguous evidence (first-class metric, I3)
  G. 4-level model selector with an explainable "why" (WS2), budget-capped,
     escalating only when confidence fails
  H. extractive compose + citation post-check dropping unsupported claims (I1,
     I2), language-tagged, with a replayable trajectory id

With the model disabled the core still returns extractive, cited answers (I4).
Every answer records subject/roles, level+why, tokens in/out, cache savings,
language and sources to the telemetry spine for the WS3 dashboards.
"""
from __future__ import annotations

import math
import re

from ..adapters.embedder import cosine
from ..contracts.types import (
    Answer, AnswerKind, Candidate, Citation, Principal, new_id, now_ms,
)
from . import lang as langmod
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


def _qtokens(q: str) -> list[str]:
    return [t for t in _TOKEN.findall(q.lower()) if t not in _STOP]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


class AnswerService:
    def __init__(self, platform):
        self.p = platform

    def ask(self, principal: Principal, question: str, k: int = 6,
            allow_model: bool = True) -> Answer:
        p = self.p
        tenant = principal.tenant
        trace_id = new_id("traj_")
        accessible = principal.accessible_acls()
        traj = {"selected": [], "graph_node_keys": []}
        qlang = langmod.detect(question)
        rq = langmod.translate_query_to_en(question, qlang, p.model, tenant)  # retrieve on English

        with p.telemetry.span("answer", {"tenant": tenant, "trace_id": trace_id, "stage": "answer",
                                         "subject": principal.subject, "roles": principal.roles,
                                         "lang": qlang}) as span:
            # A. policy + rate limit ------------------------------------
            if not p.policy.rate_check(tenant, principal.subject):
                self._audit(principal, "ask", "rate_limited", trace_id)
                return self._plain(AnswerKind.GAP, "Rate limit exceeded — retry shortly.",
                                   trace_id, tenant, span, 0.0, qlang, principal)
            pol = p.policy.check(principal, "ask", {})
            if pol.decision.value == "deny":
                self._audit(principal, "ask", f"denied:{pol.reason}", trace_id)
                return self._plain(AnswerKind.GAP, f"Request denied: {pol.reason}",
                                   trace_id, tenant, span, 0.0, qlang, principal)

            # B. cache layer 1 — full answer cache ----------------------
            cached = p.cache.get_answer(tenant, question, accessible)
            if cached:
                pay = cached["payload"]
                saved = cached["cost"]
                span.set(kind="answer", level="cache", tier=pay["tier"], cost=0.0, tokens=0,
                         cache_hit=1, cache_technique="answer_cache", cost_saved=saved,
                         grounding=pay["grounding_score"], citations_count=len(pay["citations"]),
                         sources=[c["document_title"] for c in pay["citations"]], why=pay.get("why"))
                self._audit(principal, "ask", "answered:cache", trace_id)
                return self._from_cache(pay, trace_id, tenant, qlang, saved)

            # embedding cache (layer 3)
            qvec = p.cache.get_embedding(rq)
            if qvec is None:
                qvec = p.embedder.embed([rq])[0]
                p.cache.put_embedding(rq, qvec)

            # C. hybrid retrieval + RRF (retrieval cache, layer 2) ------
            with p.telemetry.span("answer.retrieve",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "retrieve"}):
                fused = p.cache.get_retrieval(tenant, rq, accessible)
                if fused is None:
                    vec_hits = p.vindex.search(tenant, qvec, 20, accessible)
                    lex_hits = p.lindex.search(tenant, rq, 20, accessible)
                    fused = self._rrf(vec_hits, lex_hits)
                    p.cache.put_retrieval(tenant, rq, accessible, fused)

            candidates: list[Candidate] = []
            for pid, fscore in fused[: k * 3]:
                pas = p.passages.get(tenant, pid)
                if pas:
                    candidates.append(Candidate(passage=pas, fused_score=fscore))

            # D. graph expansion (graph cache, layer 4) -----------------
            graph_before = len(candidates)
            with p.telemetry.span("answer.graph",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "graph"}):
                candidates = self._graph_expand(tenant, candidates, accessible, qvec, traj, rq)
            graph_used = len(candidates) > graph_before

            if not candidates:
                self._audit(principal, "ask", "gap:no-evidence", trace_id)
                p.curation.add(tenant, question, "gap", now_ms())
                span.set(level="gap", tier="none")
                return self._plain(AnswerKind.GAP,
                                   "No supporting evidence exists in your accessible corpus.",
                                   trace_id, tenant, span, 0.0, qlang, principal)

            selected = self._mmr(qvec, candidates, k)
            traj["selected"] = [c.passage.id for c in selected]

            # E. grounding gate -----------------------------------------
            with p.telemetry.span("answer.ground",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "ground"}):
                signals, g = self._grounding(rq, qvec, selected)
            threshold = p.grounding_threshold
            span.set(grounding=g, signals=signals)

            # F. clarify-back / declared gap ----------------------------
            if g < threshold:
                if g >= threshold * 0.5:
                    ambiguous = self._ambiguous(selected)
                    cq = self._clarify_question(question, selected, ambiguous)
                    self._audit(principal, "ask", "clarify", trace_id)
                    span.set(kind="clarify", level="clarify", citations_count=0, tier="none")
                    return Answer(AnswerKind.CLARIFY, cq, [], round(g, 3), trace_id, 0.0, 0, "none",
                                  grounding_score=g, clarify_back=cq, tenant=tenant, lang=qlang,
                                  why={"level_name": "clarify", "explain": "Evidence too weak/ambiguous to answer."})
                self._audit(principal, "ask", "gap:below-threshold", trace_id)
                p.curation.add(tenant, question, "gap", now_ms())
                span.set(kind="gap", level="gap", citations_count=0, tier="none")
                return Answer(AnswerKind.GAP,
                              "The corpus does not contain enough grounded evidence to answer this. "
                              "Logged to the gap backlog.", [], round(g, 3), trace_id, 0.0, 0, "none",
                              grounding_score=g, tenant=tenant, lang=qlang,
                              why={"level_name": "gap", "explain": "Below the grounding threshold."})

            # G. model selector (4 levels, explainable why) -------------
            decision = sel.classify(rq, selected, g, graph_used)
            tier = decision["tier"]
            if tier != "none" and (not allow_model or not p.model.available()):
                tier = "none"
                decision = {**decision, "tier": "none",
                            "reasons": decision["reasons"] + [{"code": "model_disabled",
                                "detail": "model unavailable — extractive core", "signal": True}]}
            # budget: reserve atomically or degrade to the free extractive tier
            if tier != "none":
                est = self._estimate_cost(question, selected, tier)
                agent = principal.subject if principal.agent else None
                if not p.policy.try_spend(tenant, est, agent):
                    tier = "none"
                    decision = {**decision, "tier": "none",
                                "reasons": decision["reasons"] + [{"code": "budget_degrade",
                                    "detail": "tenant budget cap reached — degraded to extractive", "signal": True}]}

            # H. compose from passages only + citation post-check -------
            with p.telemetry.span("answer.compose",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "compose"}):
                text, citations, cost, tin, tout, saved = self._compose(principal, rq, selected, tier)
            confidence = self._confidence(g, citations, selected)

            # escalation only when confidence fails (roadmap guardrail)
            if (decision["level"] < 4 and confidence < _ESCALATE_FLOOR and tier != "none"
                    and p.model.available()):
                decision = sel.escalate(decision, confidence, _ESCALATE_FLOOR)
                tier = decision["tier"]
                est = self._estimate_cost(question, selected, tier)
                if p.policy.try_spend(tenant, est, principal.subject if principal.agent else None):
                    text, citations, cost, tin, tout, saved = self._compose(
                        principal, question, selected, tier)
                    confidence = self._confidence(g, citations, selected)

            if not citations:
                self._audit(principal, "ask", "gap:post-check", trace_id)
                span.set(kind="gap", level="gap", tier=tier, cost=cost, tokens=tin + tout,
                         citations_count=0)
                return Answer(AnswerKind.GAP, "No claim could be grounded to a citation.", [],
                              round(g, 3), trace_id, cost, tin + tout, tier, grounding_score=g,
                              tenant=tenant, lang=qlang)

            text = self._localize(text, qlang, principal, tier)
            sources = [c.document_title for c in citations]

            # cache the composed answer for identical future queries
            answer = Answer(AnswerKind.ANSWER, text, citations, confidence, trace_id, cost,
                            tin + tout, tier, grounding_score=g, tenant=tenant,
                            level=decision["level"], why=decision, lang=qlang,
                            cost_saved=saved, tokens_in=tin, tokens_out=tout)
            p.cache.put_answer(tenant, question, accessible, answer.to_dict(), cost)

            self._audit(principal, "ask", "answered", trace_id)
            span.set(kind="answer", tier=tier, cost=cost, tokens=tin + tout, tokens_in=tin,
                     tokens_out=tout, citations_count=len(citations), level=decision["level_name"],
                     why=decision, cost_saved=saved, cache_technique="prompt_memory" if saved else "",
                     cache_hit=1 if saved else 0, sources=sources, trajectory=traj)
            return answer

    # ================= step helpers =================================
    def _rrf(self, vec_hits, lex_hits) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        for rank, (pid, _) in enumerate(vec_hits):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, (pid, _) in enumerate(lex_hits):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

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
        if tier != "none" and self.p.model.available():
            msg = [{"role": "system", "content": _SYSTEM_PREAMBLE},
                   {"role": "user", "content": extractive}]
            out = self.p.model.complete(principal.tenant, tier, msg, {"temperature": 0.0})
            cost = out["cost"]
            tout = out["usage"]["tokens"]
            tin = len(_SYSTEM_PREAMBLE.split()) + len(extractive.split())
            # cache layer 5: provider prompt memory discount on the repeated preamble
            input_cost = tin * (5e-6 if tier in ("deep", "escalation") else 1e-6)
            saved = self.p.cache.prompt_discount(_SYSTEM_PREAMBLE, input_cost)
            cost = max(0.0, cost - saved)
            self.p.policy.try_spend(principal.tenant, cost,
                                    principal.subject if principal.agent else None)
            checked = self._postcheck(out["text"], selected)
            text = checked or extractive
        return text, citations, cost, tin, tout, saved

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
        # With a real translation model this renders in-language; offline we tag
        # honestly and keep the grounded English text cited to the English source.
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
                      cache_hit=True, cost_saved=saved)

    def _plain(self, kind, text, trace_id, tenant, span, g, qlang, principal):
        span.set(kind=kind.value, grounding=g, citations_count=0, tier="none",
                 subject=principal.subject, roles=principal.roles, lang=qlang)
        return Answer(kind, text, [], round(g, 3), trace_id, 0.0, 0, "none",
                      grounding_score=g, tenant=tenant, lang=qlang)
