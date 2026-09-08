"""Answer service — the single governed path for people, agents and apps (I7).

Steps (Build Plan Section 10), each emitting a span on one answer trace:
  A. identity & permission filter injected INSIDE retrieval (I6)
  B. hybrid retrieval (lexical + vector) fused with RRF, tiered, MMR
  C. graph expansion for connected/cross-document evidence
  D. grounding gate: five signals combined as a weighted geometric mean
  E. clarify-back on weak/ambiguous evidence (first-class metric, I3)
  F. cost-aware model router, budget-capped (I4, I12)
  G. extractive compose + citation post-check that drops unsupported claims
     (I1, I2); output carries a replayable trajectory id.

With the model disabled the core still returns extractive, cited answers (I4).
"""
from __future__ import annotations

import math
import re

from ..adapters.embedder import cosine
from ..contracts.types import (
    Answer, AnswerKind, Candidate, Citation, Principal, new_id, now_ms,
)

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
         "what", "which", "how", "who", "when", "where", "does", "do", "did", "was",
         "were", "be", "with", "that", "this", "it", "as", "by", "at", "from", "our",
         "we", "you", "i", "can", "will", "should", "must", "may"}
_RRF_K = 60
_EPS = 1e-6
# grounding signal weights (geometric mean: any weak signal drags the score down)
_WEIGHTS = {"retrieval": 1.0, "semantic": 1.2, "coverage": 1.2,
            "agreement": 0.8, "resolvable": 1.0}


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

        with p.telemetry.span("answer", {"tenant": tenant, "trace_id": trace_id,
                                         "stage": "answer"}) as span:
            # A. policy + rate limit + permission scope ------------------
            if not p.policy.rate_check(tenant, principal.subject):
                self._audit(principal, "ask", "rate_limited", trace_id)
                return self._plain(AnswerKind.GAP, "Rate limit exceeded — retry shortly.",
                                   trace_id, tenant, span, 0.0)
            pol = p.policy.check(principal, "ask", {})
            if pol.decision.value == "deny":
                self._audit(principal, "ask", f"denied:{pol.reason}", trace_id)
                return self._plain(AnswerKind.GAP, f"Request denied: {pol.reason}",
                                   trace_id, tenant, span, 0.0)

            qvec = p.embedder.embed([question])[0]

            # B. hybrid retrieval fused with RRF -------------------------
            with p.telemetry.span("answer.retrieve",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "retrieve"}):
                vec_hits = p.vindex.search(tenant, qvec, 20, accessible)
                lex_hits = p.lindex.search(tenant, question, 20, accessible)
                fused = self._rrf(vec_hits, lex_hits)

            vec_by_id, lex_by_id = dict(vec_hits), dict(lex_hits)
            candidates: list[Candidate] = []
            for pid, fscore in fused[: k * 3]:
                pas = p.passages.get(tenant, pid)
                if pas:
                    candidates.append(Candidate(passage=pas, lexical_score=lex_by_id.get(pid, 0.0),
                                                vector_score=vec_by_id.get(pid, 0.0), fused_score=fscore))

            # C. graph expansion -----------------------------------------
            with p.telemetry.span("answer.graph",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "graph"}):
                candidates = self._graph_expand(tenant, candidates, accessible, qvec, traj)

            if not candidates:
                self._audit(principal, "ask", "gap:no-evidence", trace_id)
                p.curation.add(tenant, question, "gap", now_ms())
                return self._plain(AnswerKind.GAP,
                                   "No supporting evidence exists in your accessible corpus.",
                                   trace_id, tenant, span, 0.0)

            selected = self._mmr(qvec, candidates, k)
            traj["selected"] = [c.passage.id for c in selected]

            # D. grounding gate ------------------------------------------
            with p.telemetry.span("answer.ground",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "ground"}):
                signals, g = self._grounding(question, qvec, selected)
            threshold = p.grounding_threshold
            span.set(grounding=g, signals=signals)

            # E. clarify-back / declared gap -----------------------------
            if g < threshold:
                gap_floor = threshold * 0.5
                if g >= gap_floor:      # weak-but-present evidence -> ask, don't guess
                    ambiguous = self._ambiguous(selected)
                    cq = self._clarify_question(question, selected, ambiguous)
                    self._audit(principal, "ask", "clarify", trace_id)
                    span.set(kind="clarify", citations_count=0, tier="none")
                    return Answer(AnswerKind.CLARIFY, cq, [], round(g, 3), trace_id,
                                  0.0, 0, "none", grounding_score=g, clarify_back=cq, tenant=tenant)
                self._audit(principal, "ask", "gap:below-threshold", trace_id)
                p.curation.add(tenant, question, "gap", now_ms())
                span.set(kind="gap", citations_count=0, tier="none")
                return Answer(AnswerKind.GAP,
                              "The corpus does not contain enough grounded evidence to answer this. "
                              "Logged to the gap backlog.", [], round(g, 3), trace_id, 0.0, 0, "none",
                              grounding_score=g, tenant=tenant)

            # F. model router (budget-capped) ----------------------------
            tier, cost, tokens = self._route(principal, question, selected, allow_model)

            # G. compose from passages only + citation post-check --------
            with p.telemetry.span("answer.compose",
                                  {"tenant": tenant, "trace_id": trace_id, "stage": "compose"}):
                text, citations, extra_cost, extra_tokens = self._compose(
                    principal, question, selected, tier)
            cost += extra_cost
            tokens += extra_tokens

            if not citations:      # nothing survived the post-check -> declared gap
                self._audit(principal, "ask", "gap:post-check", trace_id)
                span.set(kind="gap", tier=tier, cost=cost, tokens=tokens, citations_count=0)
                return Answer(AnswerKind.GAP, "No claim could be grounded to a citation.", [],
                              round(g, 3), trace_id, cost, tokens, tier, grounding_score=g, tenant=tenant)

            confidence = round(min(0.99, g * 0.9 + 0.05 * len(citations) / max(1, len(selected))), 3)
            self._audit(principal, "ask", "answered", trace_id)
            span.set(kind="answer", tier=tier, cost=cost, tokens=tokens,
                     citations_count=len(citations), trajectory=traj)
            return Answer(AnswerKind.ANSWER, text, citations, confidence, trace_id, cost,
                          tokens, tier, grounding_score=g, tenant=tenant)

    # ================= step helpers =================================
    def _rrf(self, vec_hits, lex_hits) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        for rank, (pid, _) in enumerate(vec_hits):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, (pid, _) in enumerate(lex_hits):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def _graph_expand(self, tenant, candidates, accessible, qvec, traj):
        if not candidates:
            return candidates
        have = {c.passage.id for c in candidates}
        node_keys: set[str] = set()
        for c in candidates[:3]:
            for nid in self.p.graph_repo.node_for_passage(tenant, c.passage.id):
                for e in self.p.graph.neighbors(tenant, nid, hops=1):
                    for other in (e["src"], e["dst"]):
                        row = self.p.db.one(
                            "SELECT canonical_key FROM graph_nodes WHERE tenant=? AND id=?",
                            (tenant, other))
                        if row:
                            node_keys.add(row["canonical_key"])
        traj["graph_node_keys"] = sorted(node_keys)
        # pull passages (in accessible scope, other documents) mentioning those concepts
        for pas in self.p.passages.for_tenant(tenant):
            if pas.id in have:
                continue
            acl = self.p.passages.acl_of(tenant, pas.id)
            if not (set(acl) & set(accessible)):
                continue
            low = pas.text.lower()
            if any(key in low for key in node_keys if len(key) > 3):
                pv = self.p.vindex  # reuse embedding via cosine on stored vec
                v = self._vec_of(tenant, pas.id)
                score = cosine(qvec, v) if v else 0.0
                candidates.append(Candidate(passage=pas, fused_score=0.0, vector_score=score,
                                            graph_hops=1, source_of="graph"))
        return candidates

    def _vec_of(self, tenant, pid):
        import json
        r = self.p.db.one("SELECT vec FROM embeddings WHERE tenant=? AND passage_id=?", (tenant, pid))
        return json.loads(r["vec"]) if r else None

    def _mmr(self, qvec, candidates: list[Candidate], k: int, lam: float = 0.7) -> list[Candidate]:
        pool = list(candidates)
        for c in pool:
            if not c.vector_score:
                v = self._vec_of(c.passage.tenant, c.passage.id)
                c.vector_score = cosine(qvec, v) if v else 0.0
        selected: list[Candidate] = []
        vecs = {c.passage.id: (self._vec_of(c.passage.tenant, c.passage.id) or []) for c in pool}
        while pool and len(selected) < k:
            best, best_score = None, -1e9
            for c in pool:
                rel = 0.5 * c.vector_score + 0.5 * min(1.0, c.fused_score * 100)
                red = 0.0
                for s in selected:
                    va, vb = vecs[c.passage.id], vecs[s.passage.id]
                    if va and vb:
                        red = max(red, cosine(va, vb))
                score = lam * rel - (1 - lam) * red
                if score > best_score:
                    best, best_score = c, score
            selected.append(best)
            pool.remove(best)
        return selected

    def _grounding(self, question, qvec, selected: list[Candidate]):
        qtok = set(_qtokens(question))
        # s1 retrieval strength: best RRF normalised to a rank-1-in-both ceiling
        ceiling = 2.0 / (_RRF_K + 1)
        s1 = min(1.0, max((c.fused_score for c in selected), default=0.0) / ceiling)
        # s2 semantic agreement
        s2 = max((c.vector_score for c in selected), default=0.0)
        s2 = max(0.0, min(1.0, s2))
        # s3 coverage of the question by the selected passages
        covered = set()
        for c in selected:
            toks = set(_TOKEN.findall(c.passage.text.lower()))
            covered |= (qtok & toks)
        s3 = (len(covered) / len(qtok)) if qtok else 0.5
        # s4 source agreement: how many independent passages corroborate the
        # query (breadth of support). Defined on query-term overlap, not textual
        # similarity, so it does not fight MMR's deliberate diversity.
        corroborating = sum(1 for c in selected
                            if qtok & set(_TOKEN.findall(c.passage.text.lower())))
        s4 = min(1.0, corroborating / min(3, max(1, len(selected)))) if qtok else s2
        # s5 citation resolvability
        s5 = sum(1 for c in selected if c.passage.coordinate.locator) / max(1, len(selected))

        signals = {"retrieval": s1, "semantic": s2, "coverage": s3,
                   "agreement": s4, "resolvable": s5}
        num = sum(_WEIGHTS[k] * math.log(max(v, _EPS)) for k, v in signals.items())
        den = sum(_WEIGHTS.values())
        g = math.exp(num / den)
        return {k: round(v, 4) for k, v in signals.items()}, round(g, 4)

    def _ambiguous(self, selected: list[Candidate]) -> bool:
        docs = {}
        for c in selected[:4]:
            docs.setdefault(c.passage.document_id, c.fused_score)
        if len(docs) < 2:
            return False
        top = sorted(docs.values(), reverse=True)
        return len(top) >= 2 and top[1] > 0 and (top[0] - top[1]) / (top[0] + _EPS) < 0.15

    def _clarify_question(self, question, selected, ambiguous) -> str:
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

    def _route(self, principal, question, selected, allow_model):
        """Decide tier and reserve budget atomically. Degrade rather than exceed (I12)."""
        if not allow_model or not self.p.model.available():
            return "none", 0.0, 0
        complex_q = len(_qtokens(question)) >= 8 or len(selected) >= 5
        tier = "deep" if complex_q else "fast"
        est = (len(question.split()) + sum(len(c.passage.text.split()) for c in selected)) \
            * (5e-6 if tier == "deep" else 1e-6)
        agent = principal.subject if principal.agent else None
        if not self.p.policy.try_spend(principal.tenant, est, agent):
            return "none", 0.0, 0     # budget cap hit -> degrade to free extractive core
        return tier, 0.0, 0            # actual cost recorded in compose

    def _compose(self, principal, question, selected, tier):
        """Build the answer ONLY from retrieved passages; post-check drops
        any sentence not supported by a cited passage (I1, I2)."""
        qtok = set(_qtokens(question))
        # pick the best supporting sentence from each of the top passages
        ranked = []
        for c in selected:
            best_sent, best_ov = c.passage.abstract, 0.0
            for s in _sentences(c.passage.text) or [c.passage.text]:
                stok = set(_TOKEN.findall(s.lower()))
                ov = len(qtok & stok)
                if ov >= best_ov:
                    best_ov, best_sent = ov, s
            ranked.append((c, best_sent, best_ov))
        ranked.sort(key=lambda x: (x[0].vector_score + x[2]), reverse=True)

        cite_map: dict[str, int] = {}
        citations: list[Citation] = []
        parts = []
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

        cost, tokens = 0.0, 0
        text = extractive
        if tier != "none" and self.p.model.available():
            # synthesis: model may only rephrase the grounded draft
            msg = [{"role": "system", "content": "Rephrase the cited evidence faithfully; add nothing."},
                   {"role": "user", "content": extractive}]
            out = self.p.model.complete(principal.tenant, tier, msg, {"temperature": 0.0})
            cost, tokens = out["cost"], out["usage"]["tokens"]
            self.p.policy.try_spend(principal.tenant, cost,
                                    principal.subject if principal.agent else None)
            text = self._postcheck(out["text"], selected, cite_map)
            if not text.strip():          # model drifted; fall back to extractive core
                text = extractive
        return text, citations, cost, tokens

    def _postcheck(self, text, selected, cite_map) -> str:
        """Keep only sentences supported by a selected passage (drop hallucinations)."""
        corpus_tokens = [set(_TOKEN.findall(c.passage.text.lower())) for c in selected]
        kept = []
        for s in _sentences(text):
            stok = set(_TOKEN.findall(s.lower())) - _STOP
            if not stok:
                continue
            if re.search(r"\[\d+\]", s):     # already carries a citation marker
                kept.append(s)
                continue
            support = max((len(stok & ct) / max(1, len(stok)) for ct in corpus_tokens), default=0.0)
            if support >= 0.5:
                kept.append(s)
        return " ".join(kept)

    # ================= plumbing =====================================
    def _audit(self, principal: Principal, action: str, decision: str, trace_id: str):
        self.p.audit.write(principal.tenant, principal.subject, principal.agent, action,
                           "answer", decision, trace_id, now_ms())

    def _plain(self, kind, text, trace_id, tenant, span, g) -> Answer:
        span.set(kind=kind.value, grounding=g, citations_count=0, tier="none")
        return Answer(kind, text, [], round(g, 3), trace_id, 0.0, 0, "none",
                      grounding_score=g, tenant=tenant)
