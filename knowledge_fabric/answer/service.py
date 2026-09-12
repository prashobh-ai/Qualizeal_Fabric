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

import hashlib
import math
import os
import re

from .. import curation
from ..adapters.embedder import cosine
from ..adapters.model import model_for_tier
from ..contracts.types import (
    Answer,
    AnswerKind,
    Candidate,
    Citation,
    Coordinate,
    CoordinateKind,
    Principal,
    new_id,
    now_ms,
)
from ..governance import authority
from ..stores import versioning
from ..telemetry import timing
from . import aggregate, personas, reasoning
from . import lang as langmod
from . import registry as known
from . import selector as sel
from .search import discover, is_discovery

_TOKEN = re.compile(r"[a-z0-9]+")
_URL = re.compile(r"https?://\S+")
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
}
_RRF_K = 60
_EPS = 1e-6
_WEIGHTS = {"retrieval": 1.0, "semantic": 1.2, "coverage": 1.2, "agreement": 0.8, "resolvable": 1.0}
_ESCALATE_FLOOR = 0.35
_SYSTEM_PREAMBLE = "Rephrase the cited evidence faithfully; add nothing."
_TIER_ORDER = {"none": 0, "fast": 1, "deep": 2, "escalation": 3}
_LEVEL_CX = {
    1: "simple",
    2: "medium",
    3: "complex",
    4: "complex",
}  # selector level -> query complexity
_CX_ORDER = {"simple": 0, "medium": 1, "complex": 2}


def _qtokens(q: str) -> list[str]:
    return [t for t in _TOKEN.findall(q.lower()) if t not in _STOP]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _max_cx(*labels: str) -> str:
    return max((lv for lv in labels if lv), key=lambda lv: _CX_ORDER.get(lv, 0), default="simple")


class AnswerService:
    def __init__(self, platform):
        self.p = platform

    # =================================================================
    def ask(
        self,
        principal: Principal,
        question: str,
        k: int = 6,
        allow_model: bool = True,
        _nested: bool = False,
        context: dict | None = None,
    ) -> Answer:
        """The public entry. T26: resolve a follow-up against the two-turn
        conversation window before answering, and stamp ``understood_as`` when a
        rewrite happened; when the reference is ambiguous, ask back with chips."""
        understood = None
        if not _nested and context:
            res = self._resolve_context(principal, question, context)
            if res.clarify:
                clarify = self._context_clarify(principal, res.clarify)
                clarify.role_view = self._persona_view(principal, clarify)
                return clarify
            question, understood = res.question, res.understood_as
        answer = self._answer(
            principal, question, k, allow_model, _nested, context_resolved=bool(understood)
        )
        if understood:
            answer.understood_as = understood
        # T27 — stamp the designation/persona lens on the finished answer. The
        # grounded facts and citations stay truthful; the persona changed the
        # emphasis (which grounded evidence led, applied in retrieval) and the
        # depth (how many sentences, applied in composition), and the lens frames
        # the result. Skipped for nested sub-asks (internal, not user-facing).
        if not _nested:
            answer.role_view = self._persona_view(principal, answer)
            self._answer_first(principal, answer)
        return answer

    def _answer_first(self, principal, answer) -> None:
        """T81/T84/T85 — the answer-first contract. The composed answer is the
        direct ``result``, rendered immediately (a facts/KPI answer already
        costs no model tokens); the persona's ``Explain`` offers (T84) call
        ``POST /api/explain`` as a separate ledgered step to produce the
        narrative on demand; and every answer carries a governance line (T85)."""
        answer.result = answer.answer_text
        answered = answer.kind == AnswerKind.ANSWER
        contract = personas.contract_for(principal.designation)
        answer.explain = {
            "available": answered,
            "offers": list(contract["offers"]) if answered else [],
            "trace_id": answer.trajectory_id,
        }
        answer.governance = self._governance_line(principal.tenant, answer)

    def _governance_line(self, tenant, answer) -> dict | None:
        """T85 — source kind, authority and freshness for the answer's lead
        citation, so a leader sees it is governed and a curator sees it is
        current without opening the card. ``stale`` when the source has not been
        refreshed within the freshness window (90 days)."""
        cites = answer.citations
        if not cites:
            return None
        doc = self.p.documents.get(tenant, cites[0].document_id) or {}
        ingested = doc.get("ingested_at")
        fresh = aggregate.as_of_text({"as_of": ingested} if ingested else None)
        stale = False
        if ingested:
            try:
                age_ms = now_ms() - int(ingested)
                stale = age_ms > 90 * 24 * 3600 * 1000
            except (TypeError, ValueError):
                stale = False
        auth = answer.authoritative_source or {}
        return {
            "source_kind": doc.get("source") or "internal",
            "authority": auth.get("source") or ("authoritative" if auth else "cited"),
            "freshness": fresh,
            "stale": bool(stale),
            "document_id": cites[0].document_id,
        }

    def _question_of_trace(self, trace_id: str, tenant: str) -> str:
        """Recover the resolved question the answer span stored (T81)."""
        import json as _json

        try:
            spans = self.p.telemetry.trace(trace_id)
        except Exception:
            return ""
        for s in spans:
            if s.get("name") == "answer" and s.get("tenant") == tenant:
                try:
                    return (_json.loads(s.get("attrs") or "{}") or {}).get("question") or ""
                except Exception:
                    return ""
        return ""

    def explain(self, principal, trace_id: str) -> dict:
        """T81 — the on-demand narrative for a prior answer, produced as a
        SEPARATE ledgered step (``purpose=explain``). Recovers the question from
        the trace, re-answers it at full machinery (which may run the model —
        ledgered — or the extractive core), and returns the working: the
        selector's reasoning, the direct answer, and the numbered evidence. Emits
        ``kf.explain.requested`` so Admin can see when the direct answer was
        insufficient."""
        from ..telemetry import api_ledger

        p, tenant = self.p, principal.tenant
        question = self._question_of_trace(trace_id, tenant)
        if not question:
            return {"trace_id": trace_id, "explanation": "", "error": "unknown or expired trace"}
        t0 = now_ms()
        a = self.ask(principal, question, allow_model=True, _nested=True)
        lines = []
        w = a.why or {}
        if w.get("explain"):
            lines.append(f"Answered by “{w.get('level_name', '')}” — {w['explain']}")
        if a.answer_text:
            lines.append(a.answer_text)
        if a.reasoning and (a.reasoning.get("steps") or []):
            for st in a.reasoning["steps"]:
                q = st.get("question") or st.get("id") or ""
                ans = st.get("answer_text") or st.get("reason") or ""
                lines.append(f"• {q} — {ans}")
        if a.citations:
            lines.append("Working — the evidence this rests on:")
            for i, c in enumerate(a.citations, 1):
                lines.append(f"[{i}] {c.document_title} · {c.coordinate.render()}: {c.snippet}")
        explanation = "\n".join(x for x in lines if x).strip()
        # A separate ledger row for the explain step ($0 on the extractive floor).
        api_ledger.record(
            purpose="explain",
            model=a.model_name or "extractive",
            usage={"input_tokens": a.tokens_in, "output_tokens": a.tokens_out},
            latency_ms=float(now_ms() - t0),
            question_hash=trace_id,
        )
        # A first-class signal: the share of answers a reader asked to explain.
        try:
            p.telemetry.record(
                "kf.explain.requested",
                {
                    "tenant": tenant,
                    "trace_id": trace_id,
                    "stage": "explain",
                    "subject": principal.subject,
                    "cost": a.cost,
                    "tokens": a.tokens,
                    "model_name": a.model_name or "extractive",
                },
            )
        except Exception:
            pass
        return {
            "trace_id": trace_id,
            "explanation": explanation,
            "model_name": a.model_name or "extractive",
            "cost": round(a.cost, 6),
            "tokens": a.tokens,
            "citations": [
                {"document_title": c.document_title, "document_id": c.document_id}
                for c in a.citations
            ],
        }

    def _persona_view(self, principal, answer) -> dict:
        """The designation-conditioned lens (T27): the same grounded answer,
        framed for the reader's organisational role. ``persona_for`` maps the
        many titles a company uses onto a small set of personas, each with a
        profile (depth + emphasis + note + lens). Every field is derived from the
        finished answer and the profile, so the browser engine computes the
        identical lens from a baked answer with no per-persona bake.

        - **builder** (developer / architect / devops) — implementation in full.
        - **quality** (tester / QE / SDET) — tests and verification in full.
        - **delivery** (manager / delivery head) — the status in brief.
        - **executive** (director / CxO) — the headline.
        - **curation** (curator) — the governance frame (grounding, gaps).
        - **operations** (platform admin) — the operations frame (level, cost).
        - **general** (no designation) — the clean answer, no adornment.
        """
        prof = personas.profile_for(principal.designation)
        lens = prof["lens"]
        view = {
            "lens": lens,
            "persona": personas.persona_for(principal.designation),
            "designation": principal.designation or "",
            "depth": prof["depth"],
            "emphasis": prof["emphasis"],
            "note": prof["note"],
        }
        answered = answer.kind == AnswerKind.ANSWER
        cited = len(answer.citations)
        if lens == "curation":
            gap = None
            if answered and cited <= 1:
                gap = "Rests on a single source — consider adding corroborating material."
            elif not answered:
                gap = "No grounded answer yet — a candidate gap for the backlog."
            view.update(
                {
                    "note": self._curation_note(answer, cited),
                    "grounding": round(answer.grounding_score, 3),
                    "sources": cited,
                    "authoritative": bool(answer.authoritative_source),
                    "gap_hint": gap,
                }
            )
        elif lens == "operations":
            view.update(
                {
                    "note": self._operations_note(answer),
                    "level": answer.level,
                    "model": answer.model_name,
                    "cost": round(answer.cost, 6),
                    "cost_saved": round(answer.cost_saved, 6),
                    "cache_hit": answer.cache_hit,
                    "tokens_in": answer.tokens_in,
                    "tokens_out": answer.tokens_out,
                }
            )
        return view

    @staticmethod
    def _curation_note(answer, cited) -> str:
        if answer.kind != AnswerKind.ANSWER:
            return "Declined — review whether the corpus should cover this."
        auth = "an authoritative source" if answer.authoritative_source else f"{cited} source(s)"
        return f"Grounded at {round(answer.grounding_score, 2)} on {auth}."

    @staticmethod
    def _operations_note(answer) -> str:
        model = answer.model_name or model_for_tier(answer.tier)
        cache = " · served from cache" if answer.cache_hit else ""
        return f"Level {answer.level} · {model} · ${answer.cost:.4f}{cache}."

    @staticmethod
    def _answer_scope(principal) -> list[str]:
        """The full-answer cache key scope: the accessible ACLs plus a persona
        sentinel, so each designation's persona-conditioned answer is cached
        distinctly. The sentinel lives only in the cache key — never in the ACLs
        used for retrieval — so personas still share the persona-agnostic
        retrieval cache and no persona ever widens what it can retrieve."""
        return principal.accessible_acls() + [
            f"@persona:{personas.persona_for(principal.designation)}"
        ]

    def _persona_boost(self, principal, tenant, fused):
        """Emphasis (T27): gently lift the grounded evidence a persona cares about
        — a developer's answer leads with code, a tester's with tests, a delivery
        lead's or a CxO's with the authoritative source. The bump is a soft
        tie-breaker (a fraction of the top score), so a strongly grounded passage
        still wins; only among comparable passages does the persona-relevant one
        lead. Facts never change — only which grounded passage is cited first."""
        emphasis = personas.profile_for(principal.designation)["emphasis"]
        if emphasis == "none" or not fused:
            return fused
        mx = max((s for _, s in fused), default=0.0) or 1.0
        bump = 0.5 * mx
        scores = dict(fused)
        for pid in list(scores):
            pas = self.p.passages.get(tenant, pid)
            if pas and self._persona_match(tenant, pas, emphasis):
                scores[pid] += bump
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def _persona_match(self, tenant, pas, emphasis) -> bool:
        is_code = pas.coordinate.kind.value == "symbol_line"
        loc = pas.coordinate.locator or {}
        path = (loc.get("path") or "").lower()
        if emphasis == "code":
            return is_code and "test" not in path  # implementation, not test code
        if emphasis == "test":
            if is_code and "test" in path:
                return True
            d = self.p.documents.get(tenant, pas.document_id) or {}
            hay = f"{d.get('title', '')} {d.get('uri', '')} {pas.text[:200]}".lower()
            return any(w in hay for w in ("test", " qa", "quality", "coverage", "automation"))
        if emphasis == "authority":
            try:
                from ..governance import authority

                return bool(authority.is_authoritative(self.p, tenant, pas.document_id))
            except Exception:
                return False
        return False

    def _resolve_context(self, principal, question, context):
        from . import context as convo

        tenant = principal.tenant
        _title_of, distinctive = self._title_index(tenant)
        subjects = {}
        for tok, did in distinctive.items():
            title = (self.p.documents.get(tenant, did) or {}).get("title", "")
            m = re.search(re.escape(tok), title, re.I)
            display = title[m.start() : m.end()] if m else tok
            # keep product/brand entities (internal capital: QMentisAI, ValidAIte)
            # — the same rule the showcase subject vocabulary uses — not every
            # single-doc title word, so coreference stays clean.
            if re.search(r"[a-z][A-Z]", display):
                subjects[tok] = display
        raw = context.get("turns") or [
            {"question": q} for q in (context.get("previous_questions") or [])
        ]
        turns = [
            convo.Turn(
                question=t.get("question", ""),
                subject=t.get("subject") or convo.subject_in(t.get("question", ""), subjects),
                answer_docs=t.get("answer_docs") or [],
                kind=t.get("kind", "answer"),
                options=t.get("options") or [],
            )
            for t in raw[-2:]
        ]
        return convo.resolve(question, turns, subjects, list(subjects.values()))

    def _context_clarify(self, principal, clarify):
        text = clarify.get("reason", "Which one do you mean?")
        return Answer(
            AnswerKind.CLARIFY,
            "",
            [],
            0.0,
            new_id("traj_"),
            0.0,
            0,
            "none",
            grounding_score=0.0,
            clarify_back=text,
            tenant=principal.tenant,
            level=0,
            why={"level_name": "clarify", "explain": text},
            lang="en",
            model_name=model_for_tier("none"),
            suggestions=clarify.get("chips", []),
        )

    def _answer(
        self,
        principal: Principal,
        question: str,
        k: int = 6,
        allow_model: bool = True,
        _nested: bool = False,
        context_resolved: bool = False,
    ) -> Answer:
        p = self.p
        tenant = principal.tenant
        trace_id = new_id("traj_")
        accessible = principal.accessible_acls()
        # T27 — the answer is persona-conditioned (emphasis + depth), so the full
        # answer cache must be keyed per persona (see _answer_scope), or the first
        # reader's framing would be served to every designation.
        answer_scope = self._answer_scope(principal)
        traj = {"selected": [], "graph_node_keys": []}
        qlang = langmod.detect(question)
        rq = langmod.translate_query_to_en(question, qlang, p.model, tenant)  # retrieve on English
        plan = reasoning.plan(rq)
        # T82 — a governed known question (the per-persona registry) is answered
        # on the fast path by design: it never decomposes and never escalates, so
        # its time-to-answer stays a commitment. The match is persona-agnostic (a
        # curated question is instant for anyone) and skipped for nested sub-asks.
        known_hit = None if _nested else known.Registry.load().match(rq)
        span_name = "answer.step" if _nested else "answer"

        with p.telemetry.span(
            span_name,
            {
                "tenant": tenant,
                "trace_id": trace_id,
                "stage": "answer",
                "subject": principal.subject,
                "roles": principal.roles,
                "lang": qlang,
                # T81 — the resolved question, so POST /api/explain can recover it
                # from the trace and produce the narrative as a separate step.
                "question": rq,
                # T30 — first-class telemetry for the answering features shipped
                # since the spine was last extended: the reader's persona and
                # designation (T27), the access scope, and whether a follow-up was
                # resolved from context (T26). Set on the parent answer span, so
                # every answer path inherits them into telemetry.events().
                "persona": personas.persona_for(principal.designation),
                "designation": principal.designation or "",
                "scope": "restricted" if "restricted" in (principal.scopes or []) else "public",
                "context_resolved": 1 if context_resolved else 0,
            },
        ) as span:
            # T56 — phase / active-idle stopwatch for this answer. Retrieval is
            # wall time (active=False → idle); model composition is active. The
            # collected block rides on the answer span and the Answer object.
            timer = timing.PhaseTimer()
            # A. policy + rate limit (the parent request already did this for steps)
            if not _nested:
                if not p.policy.rate_check(tenant, principal.subject):
                    self._audit(principal, "ask", "rate_limited", trace_id)
                    return self._plain(
                        AnswerKind.GAP,
                        "Rate limit exceeded — retry shortly.",
                        trace_id,
                        tenant,
                        span,
                        0.0,
                        qlang,
                        principal,
                    )
                pol = p.policy.check(principal, "ask", {})
                if pol.decision.value == "deny":
                    self._audit(principal, "ask", f"denied:{pol.reason}", trace_id)
                    return self._plain(
                        AnswerKind.GAP,
                        f"Request denied: {pol.reason}",
                        trace_id,
                        tenant,
                        span,
                        0.0,
                        qlang,
                        principal,
                    )

            # B. cache layer 1 — full answer cache (also serves repeated reasoned questions)
            cached = p.cache.get_answer(tenant, question, answer_scope)
            if cached:
                pay = cached["payload"]
                saved = cached["cost"]
                span.set(
                    kind="answer",
                    level="cache",
                    tier=pay["tier"],
                    cost=0.0,
                    tokens=0,
                    cache_hit=1,
                    cache_technique="answer_cache",
                    cost_saved=saved,
                    grounding=pay["grounding_score"],
                    citations_count=len(pay["citations"]),
                    sources=[c["document_title"] for c in pay["citations"]],
                    why=pay.get("why"),
                    model_name="cache",
                    complexity=pay.get("complexity", ""),
                    dataset_version=pay.get("dataset_version", 0),
                )
                if not _nested:
                    self._audit(principal, "ask", "answered:cache", trace_id)
                return self._from_cache(pay, trace_id, tenant, qlang, saved)

            # C. multistep / conditional / compare → decompose, run each step governed
            # (a known question stays single-shot on the fast path — T82).
            if not _nested and plan["mode"] != "single" and not known_hit:
                return self._ask_reasoned(
                    principal, question, plan, trace_id, span, qlang, k, allow_model
                )

            # C2. identifier tier (T25): a code question that names a symbol
            # answers directly from that function, before the prose machinery.
            if not _nested:
                coded = self._code_answer(
                    principal, question, rq, accessible, trace_id, span, qlang, dsv=0
                )
                if coded is not None:
                    return coded

            # C3. discovery intent (T24): "has anyone made auth code?", "find an
            # automation script for X" — return a ranked list of reusable assets.
            if not _nested and is_discovery(question):
                disc = self._discovery_answer(
                    principal, question, rq, accessible, trace_id, span, qlang, dsv=0
                )
                if disc is not None:
                    return disc

            qvec = p.cache.get_embedding(rq)
            if qvec is None:
                qvec = p.embedder.embed([rq])[0]
                p.cache.put_embedding(rq, qvec)

            # D. hybrid retrieval + RRF (retrieval cache) + authority boost
            with (
                p.telemetry.span(
                    "answer.retrieve",
                    {"tenant": tenant, "trace_id": trace_id, "stage": "retrieve"},
                ),
                timer.phase("retrieve", active=False),
            ):
                fused = p.cache.get_retrieval(tenant, rq, accessible)
                if fused is None:
                    vec_hits = p.vindex.search(tenant, qvec, 20, accessible)
                    lex_hits = p.lindex.search(tenant, rq, 20, accessible)
                    fused = self._rrf(vec_hits, lex_hits)
                    p.cache.put_retrieval(tenant, rq, accessible, fused)
                fused = self._authority_boost(tenant, fused)
                fused = self._subject_boost(tenant, rq, fused, accessible)
                fused = self._persona_boost(principal, tenant, fused)  # T27 emphasis

            # T53 — documents in manual-review are held out of every answer;
            # their passages must never reach an asker's trace.
            review = curation.review_doc_ids(p, tenant)
            candidates: list[Candidate] = []
            for pid, fscore in fused[: k * 3]:
                pas = p.passages.get(tenant, pid)
                if pas and pas.document_id not in review:
                    candidates.append(Candidate(passage=pas, fused_score=fscore))

            # E. graph expansion -----------------------------------------
            graph_before = len(candidates)
            with p.telemetry.span(
                "answer.graph", {"tenant": tenant, "trace_id": trace_id, "stage": "graph"}
            ):
                candidates = self._graph_expand(tenant, candidates, accessible, qvec, traj, rq)
            graph_used = len(candidates) > graph_before

            dsv = versioning.current_dataset(p, tenant)
            if not candidates:
                if not _nested:
                    self._audit(principal, "ask", "gap:no-evidence", trace_id)
                    p.curation.add(tenant, question, "gap", now_ms())
                span.set(level="gap", tier="none", dataset_version=dsv)
                return self._plain(
                    AnswerKind.GAP,
                    "No supporting evidence exists in your accessible corpus.",
                    trace_id,
                    tenant,
                    span,
                    0.0,
                    qlang,
                    principal,
                    dsv,
                )

            selected = self._mmr(qvec, candidates, k)
            selected = self._ensure_subject(tenant, rq, candidates, selected)
            traj["selected"] = [c.passage.id for c in selected]

            # F. grounding gate ------------------------------------------
            with p.telemetry.span(
                "answer.ground", {"tenant": tenant, "trace_id": trace_id, "stage": "ground"}
            ):
                signals, g = self._grounding(rq, qvec, selected)
            threshold = p.grounding_threshold
            # An EXACT signal the semantics-free embedder misses is still
            # high-confidence: a code identifier match (symbol/path) or a passage
            # from the document whose distinctive title the query names. Floor the
            # grounding score so such an answer is not wrongly declined (T25).
            if g < threshold and self._exact_signal(tenant, rq, selected):
                g = threshold + 0.05
                signals = {**signals, "resolvable": max(signals.get("resolvable", 0.0), 0.9)}
            span.set(grounding=g, signals=signals, dataset_version=dsv)

            # G. clarify-back / declared gap -----------------------------
            if g < threshold:
                if g >= threshold * 0.5:
                    ambiguous = self._ambiguous(selected)
                    cq = self._clarify_question(question, selected, ambiguous)
                    if not _nested:
                        self._audit(principal, "ask", "clarify", trace_id)
                    span.set(
                        kind="clarify",
                        level="clarify",
                        citations_count=0,
                        tier="none",
                        complexity=plan["complexity"],
                    )
                    return Answer(
                        AnswerKind.CLARIFY,
                        cq,
                        [],
                        round(g, 3),
                        trace_id,
                        0.0,
                        0,
                        "none",
                        grounding_score=g,
                        clarify_back=cq,
                        tenant=tenant,
                        lang=qlang,
                        complexity=plan["complexity"],
                        dataset_version=dsv,
                        model_name=model_for_tier("none"),
                        why={
                            "level_name": "clarify",
                            "explain": "Evidence too weak/ambiguous to answer.",
                        },
                    )
                if not _nested:
                    self._audit(principal, "ask", "gap:below-threshold", trace_id)
                    p.curation.add(tenant, question, "gap", now_ms())
                span.set(
                    kind="gap",
                    level="gap",
                    citations_count=0,
                    tier="none",
                    complexity=plan["complexity"],
                )
                return Answer(
                    AnswerKind.GAP,
                    "The corpus does not contain enough grounded evidence to answer this. "
                    "Logged to the gap backlog.",
                    [],
                    round(g, 3),
                    trace_id,
                    0.0,
                    0,
                    "none",
                    grounding_score=g,
                    tenant=tenant,
                    lang=qlang,
                    complexity=plan["complexity"],
                    dataset_version=dsv,
                    model_name=model_for_tier("none"),
                    why={"level_name": "gap", "explain": "Below the grounding threshold."},
                )

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
            # T82 — fast-path lock: a governed known question is capped at the
            # fast path (never reason/escalation), so a curated question is
            # instant by design. The reason rides on the why-card and telemetry,
            # so Admin can see the fast-path share these questions preserve.
            if known_hit:
                if decision["level"] > 2:
                    nm, tr = sel.LEVELS[2]
                    decision = {**decision, "level": 2, "level_name": nm, "tier": tr}
                decision["reasons"] = decision["reasons"] + [
                    {
                        "code": "known_question",
                        "detail": f"registry known question ({known_hit['id']})",
                        "signal": known_hit["pattern"],
                    }
                ]
                tier = decision["tier"]
            # complexity describes the QUERY (form + evidence spread), so it is fixed here from
            # the selector's level and never lowered by a later budget/model-off degradation
            complexity = _max_cx(plan["complexity"], _LEVEL_CX.get(decision["level"], "simple"))
            if tier != "none" and (not allow_model or not p.model.available()):
                tier = "none"
                decision = {
                    **decision,
                    "tier": "none",
                    "reasons": decision["reasons"]
                    + [
                        {
                            "code": "model_disabled",
                            "detail": "model unavailable — extractive core",
                            "signal": True,
                        }
                    ],
                }
            if tier != "none":
                est = self._estimate_cost(question, selected, tier)
                agent = principal.subject if principal.agent else None
                if not p.policy.try_spend(tenant, est, agent):
                    tier = "none"
                    decision = {
                        **decision,
                        "tier": "none",
                        "reasons": decision["reasons"]
                        + [
                            {
                                "code": "budget_degrade",
                                "detail": "tenant budget cap reached — degraded to extractive",
                                "signal": True,
                            }
                        ],
                    }

            # I. compose from passages only + citation post-check -------
            with (
                p.telemetry.span(
                    "answer.compose",
                    {"tenant": tenant, "trace_id": trace_id, "stage": "compose"},
                ),
                timer.phase("summarise"),
            ):
                text, citations, cost, tin, tout, saved, model_name = self._compose(
                    principal, rq, selected, tier
                )
            confidence = self._confidence(g, citations, selected)

            if (
                not known_hit  # T82 — a known question never escalates off the fast path
                and decision["level"] < 4
                and confidence < _ESCALATE_FLOOR
                and tier != "none"
                and p.model.available()
            ):
                decision = sel.escalate(decision, confidence, _ESCALATE_FLOOR)
                tier = decision["tier"]
                est = self._estimate_cost(question, selected, tier)
                if p.policy.try_spend(tenant, est, principal.subject if principal.agent else None):
                    with timer.phase("summarise"):
                        text, citations, cost, tin, tout, saved, model_name = self._compose(
                            principal, rq, selected, tier
                        )
                    confidence = self._confidence(g, citations, selected)

            # L2.3 — surface the five grounding signals (the Trust bars) and the
            # retrieved-vs-cited counts (Sources: found / cited) on the why-card.
            decision = {
                **decision,
                "complexity": complexity,
                "model_name": model_name,
                "signals": signals,
                "retrieved": len(selected),
            }

            if not citations:
                if not _nested:
                    self._audit(principal, "ask", "gap:post-check", trace_id)
                span.set(
                    kind="gap",
                    level="gap",
                    tier=tier,
                    cost=cost,
                    tokens=tin + tout,
                    citations_count=0,
                    model_name=model_name,
                    complexity=complexity,
                )
                return Answer(
                    AnswerKind.GAP,
                    "No claim could be grounded to a citation.",
                    [],
                    round(g, 3),
                    trace_id,
                    cost,
                    tin + tout,
                    tier,
                    grounding_score=g,
                    tenant=tenant,
                    lang=qlang,
                    model_name=model_name,
                    complexity=complexity,
                    dataset_version=dsv,
                )

            with timer.phase("translate", active=qlang not in ("", "en")):
                text = self._localize(text, qlang, principal, tier)
            sources = [c.document_title for c in citations]
            auth = self._authority_card(tenant, citations)
            tblock = timer.timing()  # T56 — phase / active-idle for this answer

            answer = Answer(
                AnswerKind.ANSWER,
                text,
                citations,
                confidence,
                trace_id,
                cost,
                tin + tout,
                tier,
                grounding_score=g,
                tenant=tenant,
                level=decision["level"],
                why=decision,
                lang=qlang,
                cost_saved=saved,
                tokens_in=tin,
                tokens_out=tout,
                model_name=model_name,
                complexity=complexity,
                authoritative_source=auth,
                dataset_version=dsv,
                timing=tblock,
            )
            p.cache.put_answer(tenant, question, answer_scope, answer.to_dict(), cost)

            if not _nested:
                self._audit(principal, "ask", "answered", trace_id)
            span.set(
                kind="answer",
                tier=tier,
                cost=cost,
                tokens=tin + tout,
                tokens_in=tin,
                tokens_out=tout,
                citations_count=len(citations),
                level=decision["level_name"],
                why=decision,
                cost_saved=saved,
                cache_technique="prompt_memory" if saved else "",
                cache_hit=1 if saved else 0,
                sources=sources,
                trajectory=traj,
                model_name=model_name,
                complexity=complexity,
                dataset_version=dsv,
                timing=tblock,
            )
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
        model_name = (
            models[0]
            if len(models) == 1
            else (", ".join(models) if models else model_for_tier("none"))
        )
        level = max((a.level for a in collected), default=0)
        grounding = min((a.grounding_score for a in answered), default=0.0)
        complexity = _max_cx(
            res.get("complexity", plan["complexity"]), *[a.complexity for a in collected]
        )
        dsv = versioning.current_dataset(p, tenant)
        surface = reasoning.to_surface(res)
        why = {
            "level_name": f"reasoning:{res['mode']}",
            "explain": res.get("explain", ""),
            "reasons": [
                {
                    "code": f"reasoning_{res['mode']}",
                    "detail": res.get("explain", ""),
                    "signal": len(res["steps"]),
                }
            ]
            + [r for a in answered for r in (a.why or {}).get("reasons", [])][:6],
            "complexity": complexity,
            "model_name": model_name,
            "level": level,
        }

        kind = res.get("kind", "answer")
        if kind == "clarify":
            cq = res.get("clarify_back") or "One step of this question needs clarification."
            self._audit(principal, "ask", "clarify", trace_id)
            span.set(
                kind="clarify",
                level="clarify",
                citations_count=0,
                tier=tier,
                cost=cost,
                tokens=tin + tout,
                tokens_in=tin,
                tokens_out=tout,
                model_name=model_name,
                complexity=complexity,
                dataset_version=dsv,
                reasoning=surface,
                why=why,
            )
            return Answer(
                AnswerKind.CLARIFY,
                cq,
                [],
                round(grounding, 3),
                trace_id,
                cost,
                tin + tout,
                tier,
                grounding_score=grounding,
                clarify_back=cq,
                tenant=tenant,
                lang=qlang,
                level=level,
                why=why,
                model_name=model_name,
                complexity=complexity,
                dataset_version=dsv,
                reasoning=surface,
                tokens_in=tin,
                tokens_out=tout,
                cost_saved=saved,
            )
        if kind == "gap" or not res.get("citations"):
            self._audit(principal, "ask", f"gap:step:{res.get('gap_step')}", trace_id)
            p.curation.add(tenant, question, "gap", now_ms())
            span.set(
                kind="gap",
                level="gap",
                citations_count=0,
                tier=tier,
                cost=cost,
                tokens=tin + tout,
                tokens_in=tin,
                tokens_out=tout,
                model_name=model_name,
                complexity=complexity,
                dataset_version=dsv,
                reasoning=surface,
                why=why,
            )
            return Answer(
                AnswerKind.GAP,
                res.get("final_text")
                or "A step of this question has no grounded evidence in the corpus.",
                [],
                round(grounding, 3),
                trace_id,
                cost,
                tin + tout,
                tier,
                grounding_score=grounding,
                tenant=tenant,
                lang=qlang,
                level=level,
                why=why,
                model_name=model_name,
                complexity=complexity,
                dataset_version=dsv,
                reasoning=surface,
                tokens_in=tin,
                tokens_out=tout,
                cost_saved=saved,
            )

        citations = res["citations"]
        text = self._localize(res["final_text"], qlang, principal, tier)
        auth = self._authority_card(tenant, citations)
        self._audit(principal, "ask", f"answered:{res['mode']}", trace_id)
        answer = Answer(
            AnswerKind.ANSWER,
            text,
            citations,
            round(res["confidence"], 3),
            trace_id,
            cost,
            tin + tout,
            tier,
            grounding_score=grounding,
            tenant=tenant,
            level=level,
            why=why,
            lang=qlang,
            cost_saved=saved,
            tokens_in=tin,
            tokens_out=tout,
            model_name=model_name,
            complexity=complexity,
            authoritative_source=auth,
            dataset_version=dsv,
            reasoning=surface,
        )
        p.cache.put_answer(tenant, question, self._answer_scope(principal), answer.to_dict(), cost)
        span.set(
            kind="answer",
            tier=tier,
            cost=cost,
            tokens=tin + tout,
            tokens_in=tin,
            tokens_out=tout,
            citations_count=len(citations),
            level=f"reasoning:{res['mode']}",
            why=why,
            cost_saved=saved,
            cache_hit=1 if saved else 0,
            cache_technique="prompt_memory" if saved else "",
            sources=[c.document_title for c in citations],
            grounding=grounding,
            model_name=model_name,
            complexity=complexity,
            dataset_version=dsv,
            reasoning=surface,
        )
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
                                (tenant, other),
                            )
                            if row:
                                node_keys.add(row["canonical_key"])
            node_keys = sorted(node_keys)
            self.p.cache.put_graph(tenant, question, accessible, node_keys)
        traj["graph_node_keys"] = node_keys
        review = curation.review_doc_ids(self.p, tenant)  # T53 hold-out
        for pas in self.p.passages.for_tenant(tenant):
            if pas.id in have:
                continue
            if pas.document_id in review:
                continue
            if not (set(self.p.passages.acl_of(tenant, pas.id)) & set(accessible)):
                continue
            low = pas.text.lower()
            if any(key in low for key in node_keys if len(key) > 3):
                v = self._vec_of(tenant, pas.id)
                candidates.append(
                    Candidate(
                        passage=pas,
                        fused_score=0.0,
                        vector_score=cosine(qvec, v) if v else 0.0,
                        graph_hops=1,
                        source_of="graph",
                    )
                )
        return candidates

    def _vec_of(self, tenant, pid):
        import json

        r = self.p.db.one(
            "SELECT vec FROM embeddings WHERE tenant=? AND passage_id=?", (tenant, pid)
        )
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
                red = max(
                    (
                        cosine(vecs[c.passage.id], vecs[s.passage.id])
                        for s in selected
                        if vecs[c.passage.id] and vecs[s.passage.id]
                    ),
                    default=0.0,
                )
                score = lam * rel - (1 - lam) * red
                if score > best_score:
                    best, best_score = c, score
            selected.append(best)
            pool.remove(best)
        return selected

    def _ensure_subject(self, tenant, question, candidates, selected):
        """MMR ranks by embedding similarity, which the semantics-free embedder
        gives poorly for a weakly-worded title. When the query names a
        distinctive subject document that IS a candidate but MMR did not select,
        swap it in (dropping the least relevant), so the answer is built from the
        document the question is about."""
        subj_docs = set(self._subject_of(tenant, question).values())
        if not subj_docs or any(c.passage.document_id in subj_docs for c in selected):
            return selected
        subj_cands = [c for c in candidates if c.passage.document_id in subj_docs]
        if not subj_cands:
            return selected
        return [subj_cands[0]] + (selected[:-1] if selected else [])

    def _grounding(self, question, qvec, selected):
        qtok = set(_qtokens(question))
        ceiling = 2.0 / (_RRF_K + 1)
        s1 = min(1.0, max((c.fused_score for c in selected), default=0.0) / ceiling)
        s2 = max(0.0, min(1.0, max((c.vector_score for c in selected), default=0.0)))
        covered = set()
        for c in selected:
            covered |= qtok & set(_TOKEN.findall(c.passage.text.lower()))
        s3 = (len(covered) / len(qtok)) if qtok else 0.5
        corroborating = sum(
            1 for c in selected if qtok & set(_TOKEN.findall(c.passage.text.lower()))
        )
        s4 = min(1.0, corroborating / min(3, max(1, len(selected)))) if qtok else s2
        s5 = sum(1 for c in selected if c.passage.coordinate.locator) / max(1, len(selected))
        signals = {
            "retrieval": s1,
            "semantic": s2,
            "coverage": s3,
            "agreement": s4,
            "resolvable": s5,
        }
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
            return (
                f"Your question could refer to more than one area — I found relevant material in "
                f"“{titles[0]}” and “{titles[1]}”. Which did you mean, or can you add a detail?"
            )
        return (
            "I don't have strongly grounded evidence for that yet. Could you narrow the question "
            "— e.g. name the specific document, release, or requirement you mean?"
        )

    def _estimate_cost(self, question, selected, tier):
        toks = len(question.split()) + sum(len(c.passage.text.split()) for c in selected)
        return toks * (5e-6 if tier in ("deep", "escalation") else 1e-6)

    def _confidence(self, g, citations, selected):
        return round(min(0.99, g * 0.9 + 0.05 * len(citations) / max(1, len(selected))), 3)

    def _title_index(self, tenant):
        """Per-document title tokens, plus the DISTINCTIVE ones — title tokens
        that name exactly one document (a product/brand entity like "qmentisai",
        never a generic word like "testing" shared across many titles). Returns
        (title_of: doc_id -> token set, distinctive: token -> owning doc_id)."""
        title_of, df, owner = {}, {}, {}
        for d in self.p.documents.list(tenant):
            tt = set(_TOKEN.findall((d.get("title") or "").lower()))
            title_of[d["id"]] = tt
            for t in tt:
                df[t] = df.get(t, 0) + 1
                owner.setdefault(t, d["id"])
        distinctive = {t: owner[t] for t in df if df[t] == 1}
        return title_of, distinctive

    def _subject_of(self, tenant, question):
        """The distinctive title tokens the question names, and the documents
        they own — the entity the question is ABOUT, when it names one."""
        qtok = set(_qtokens(question))
        _title_of, distinctive = self._title_index(tenant)
        subj = {t: distinctive[t] for t in qtok if t in distinctive}
        return subj  # token -> owning doc_id (empty for a non-entity query)

    @staticmethod
    def _ident_score(loc: dict, qtok) -> int:
        """How strongly a code passage's identifiers match the query: a hit on
        the symbol name outweighs the qualified name, which outweighs the file
        path — so the exact function ranks above others that merely share a
        common word."""
        sym = str(loc.get("symbol", "")).lower()
        qual = str(loc.get("qualified", "")).lower()
        path = str(loc.get("path", "")).lower()
        score = 0
        for t in qtok:
            if t in sym:
                score += 3
            elif t in qual:
                score += 2
            elif t in path:
                score += 1
        return score

    def _is_code_doc(self, tenant, doc_id) -> bool:
        d = self.p.documents.get(tenant, doc_id)
        if not d:
            return False
        if "code" in (d.get("type") or "").lower():
            return True
        ext = (d.get("uri") or "").rsplit(".", 1)[-1].lower()
        return ext in ("py", "js", "ts", "tsx", "go", "java", "rb", "cs", "sh")

    def _discovery_answer(
        self, principal, question, rq, accessible, trace_id, span, qlang, dsv, min_top=0.0
    ):
        """Asset/capability discovery (T24): return a ranked LIST of real
        organisation assets a person can reuse — code, tests, policies, learning
        material — searched across the fabric and (on a real backend) live
        GitHub. Never a single synthesised answer, and used both on explicit
        discovery intent (permissive) and as the fallback before a blind gap
        (``min_top`` guards against listing weak text overlaps). Returns None
        when nothing strong enough was found, so the caller declines honestly."""
        d = discover(self.p, principal.tenant, rq, accessible, k=6)
        hits = [h for h in d.hits if h.score > 0]
        if not hits or max(h.score for h in hits) < min_top:
            return None
        noun = "assets"
        lead = f"Found {len(hits)} {noun} in the fabric you can reuse — each links to its source:"
        parts, citations = [lead], []
        for i, h in enumerate(hits, 1):
            parts.append(f"• {h.title} ({h.kind}) — {h.snippet} [{i}]")
            coord = h.coordinate
            if coord is None or not (getattr(coord, "locator", {}) or {}).get("url"):
                loc = dict(getattr(coord, "locator", {}) or {})
                if h.url:
                    loc["url"] = h.url
                if h.path:
                    loc.setdefault("path", h.path)
                coord = Coordinate(
                    coord.kind if coord is not None else CoordinateKind.PAGE_PARAGRAPH, loc
                )
            citations.append(
                Citation(
                    document_id=h.document_id or h.path,
                    document_title=h.title,
                    coordinate=coord,
                    passage_id=h.passage_id or "",
                    snippet=h.snippet[:200],
                )
            )
        why = {
            "level_name": "discovery",
            "explain": "Searched "
            + (", ".join(d.searched) or "the fabric")
            + "; listed matching assets.",
            "reasons": [{"code": "discovery", "detail": "asset search", "signal": True}],
            "signals": {
                "retrieval": 1.0,
                "semantic": 0.7,
                "coverage": 0.8,
                "agreement": 0.8,
                "resolvable": 1.0,
            },
            "retrieved": len(hits),
            "complexity": "simple",
            "model_name": model_for_tier("none"),
            "discovery": [
                {
                    "title": h.title,
                    "kind": h.kind,
                    "url": h.url,
                    "path": h.path,
                    "snippet": h.snippet,
                }
                for h in hits
            ],
        }
        self._audit(principal, "ask", "answered:discovery", trace_id)
        span.set(
            kind="answer",
            level="discovery",
            tier="none",
            citations_count=len(citations),
            complexity="simple",
            dataset_version=dsv,
        )
        return Answer(
            AnswerKind.ANSWER,
            "\n".join(parts),
            citations,
            0.85,
            trace_id,
            0.0,
            0,
            "none",
            grounding_score=0.85,
            tenant=principal.tenant,
            level=1,
            why=why,
            lang=qlang,
            model_name=model_for_tier("none"),
            complexity="simple",
            dataset_version=dsv,
        )

    def _code_answer(self, principal, question, rq, accessible, trace_id, span, qlang, dsv):
        """Identifier tier (T25) as an EARLY, decisive path. A code question
        names a symbol, which the semantics-free embedder and text-lexical index
        routinely miss; when a code passage's symbol/path matches the query
        strongly (a symbol-name hit), answer directly from that function at
        Level 1 — the exact code, line-anchored — before the prose grounding and
        clarify machinery, which is tuned for prose and would wrongly decline or
        ask back. Returns None when no symbol matches, so prose composition runs.
        (A production build maintains an inverted symbol index; scanning the code
        passages suffices at showcase scale.)"""
        tenant = principal.tenant
        qtok = [t for t in _qtokens(rq) if len(t) >= 3]
        if not qtok:
            return None
        # A question that names a distinctive PROSE subject (a product/company
        # document) is prose, not code — bail so it answers as prose. A code-file
        # title ("converter.py") is not such a subject; those questions stay here.
        subj = self._subject_of(tenant, rq)
        if any(not self._is_code_doc(tenant, did) for did in subj.values()):
            return None
        review = curation.review_doc_ids(self.p, tenant)  # T53 hold-out
        scored = []
        for pas in self.p.passages.for_tenant(tenant):
            if pas.coordinate.kind.value != "symbol_line":
                continue
            if pas.document_id in review:
                continue
            if not (set(self.p.passages.acl_of(tenant, pas.id)) & set(accessible)):
                continue
            s = self._ident_score(pas.coordinate.locator or {}, qtok)
            if s > 0:
                scored.append((s, pas))
        if not scored or max(s for s, _ in scored) < 3:  # need a symbol-name hit
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        sel = [
            Candidate(passage=pas, vector_score=float(s), fused_score=float(s))
            for s, pas in scored[:4]
        ]
        # T27 — persona depth + emphasis reach the early code path too, so a
        # tester's code answer leads with the verifying test and a CxO's shows
        # one block.
        prof = personas.profile_for(principal.designation)
        text, citations, *_ = self._compose_code(
            rq, sel, personas.DEPTH_CAP[prof["depth"]], prof["emphasis"]
        )
        if not citations:
            return None
        text = self._localize(text, qlang, principal, "none")
        why = {
            "level_name": "lookup",
            "explain": "Matched a code symbol; cited the function directly.",
            "reasons": [{"code": "identifier_hit", "detail": "exact symbol match", "signal": True}],
            "signals": {
                "retrieval": 1.0,
                "semantic": 0.9,
                "coverage": 0.8,
                "agreement": 1.0,
                "resolvable": 1.0,
            },
            "retrieved": len(sel),
            "complexity": "simple",
            "model_name": model_for_tier("none"),
        }
        self._audit(principal, "ask", "answered:code", trace_id)
        span.set(
            kind="answer",
            level="lookup",
            tier="none",
            citations_count=len(citations),
            code_answer=True,
            complexity="simple",
            dataset_version=dsv,
        )
        return Answer(
            AnswerKind.ANSWER,
            text,
            citations,
            0.9,
            trace_id,
            0.0,
            0,
            "none",
            grounding_score=0.9,
            tenant=tenant,
            level=1,
            why=why,
            lang=qlang,
            model_name=model_for_tier("none"),
            complexity="simple",
            authoritative_source=self._authority_card(tenant, citations),
            dataset_version=dsv,
        )

    def _exact_signal(self, tenant, question, selected) -> bool:
        """True when a selected passage carries an EXACT match the grounding
        embedder cannot see: a code symbol/path identifier the query names, or a
        document whose distinctive title token the query names."""
        qtok = [t for t in _qtokens(question) if len(t) >= 3]
        if not qtok:
            return False
        for c in selected:
            if c.passage.coordinate.kind.value == "symbol_line" and self._ident_score(
                c.passage.coordinate.locator or {}, qtok
            ):
                return True
        subj_docs = set(self._subject_of(tenant, question).values())
        return any(c.passage.document_id in subj_docs for c in selected)

    def _subject_boost(self, tenant, question, fused, accessible=None):
        """Lift passages from the document the question is actually ABOUT.

        The hashing embedder has no semantics, so a short "what is QMentisAI?"
        can retrieve co-occurrence noise (company history, mission) above the
        product's own brief. Deterministic, model-free correction: when the query
        names a distinctive entity, lift that document's passages to the top so
        the compose leads from it — and INJECT the subject document's passages
        when retrieval missed them entirely (a weakly-worded title the embedder
        never surfaced). Generic queries are left untouched, so an analytical
        question still draws connected cross-document evidence."""
        subj_docs = set(self._subject_of(tenant, question).values())
        if not subj_docs:
            return fused
        mx = max((s for _, s in fused), default=0.0) or 1.0
        scores = dict(fused)
        for pid in list(scores):
            pas = self.p.passages.get(tenant, pid)
            if pas and pas.document_id in subj_docs:
                scores[pid] += mx
        present = {
            self.p.passages.get(tenant, pid).document_id
            for pid in scores
            if self.p.passages.get(tenant, pid)
        }
        for did in subj_docs - present:  # retrieval missed this subject doc — inject it
            for pas in self.p.passages.by_document(tenant, did)[:3]:
                if accessible is None or (
                    set(self.p.passages.acl_of(tenant, pas.id)) & set(accessible)
                ):
                    scores[pas.id] = mx
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    @staticmethod
    def _is_heading(s: str) -> bool:
        """A section label / list header / URL line, not a descriptive sentence —
        the kind of fragment ("The Five Pillars of ValidAIte", "QMentisAI Product
        Page: https://…") that reads as gibberish when stitched into an answer."""
        st = s.strip()
        if len(st) < 25 or "http" in st.lower() or "://" in st:
            return True
        if st.endswith(":"):  # a lead-in label introducing a list, not a statement
            return True
        return st[-1] not in ".!?" and len(st.split()) <= 6

    @classmethod
    def _clean_sentence(cls, s: str) -> str:
        """Strip a leading section label ("QualiSec Module Page:") and any inline
        URL so a chosen sentence reads as prose, not a link dump."""
        s = _URL.sub("", s).strip()
        s = re.sub(r"^[A-Z][A-Za-z0-9 .&/'’-]{0,40}:\s+", "", s).strip()
        return re.sub(r"\s{2,}", " ", s)

    @classmethod
    def _sentence_score(cls, s: str, qtok: set) -> float:
        """Score a passage sentence for use as answer text: it must share query
        content-tokens, and a real sentence (ends in punctuation, enough words)
        outranks a heading fragment."""
        ov = len(qtok & (set(_TOKEN.findall(s.lower())) - _STOP))
        if ov == 0:
            return 0.0
        substantive = 1.0 if (s.strip()[-1:] in ".!?" and len(s.split()) >= 6) else 0.35
        return ov * substantive

    def _doc_lead(self, tenant, doc_id, qtok, n):
        """Up to ``n`` (passage, sentence) pairs from a document's OWN opening
        prose, in document order — reliably a product/service definition,
        regardless of what the noisy hashing retrieval surfaced for the query.
        Sentences that name the subject are preferred, then the earliest
        substantive lines."""
        passes = self.p.passages.by_document(tenant, doc_id)

        def order(p):
            loc = getattr(p.coordinate, "locator", None)
            return (
                loc if isinstance(loc, list) and all(isinstance(x, int) for x in loc) else [1 << 30]
            )

        passes.sort(key=order)
        sents = []
        for p in passes:
            for raw in _sentences(p.text):
                s = self._clean_sentence(raw)
                if not self._is_heading(s):
                    sents.append((p, s))
        named = [(p, s) for p, s in sents if qtok & (set(_TOKEN.findall(s.lower())) - _STOP)]

        def defn_rank(ps):
            low = ps[1].lower()
            starts = any(low.startswith(t) for t in qtok)  # "QualiCentral is …"
            copula = bool(re.search(r"\b(is|are|provides|enables|delivers|helps)\b", low))
            return (starts and copula, copula)  # a copula definition should lead

        named = [
            ps
            for _, ps in sorted(
                enumerate(named), key=lambda ip: (defn_rank(ip[1]), -ip[0]), reverse=True
            )
        ]
        return (named or sents)[:n]

    def _compose_code(self, question, selected, cap=2, emphasis="none"):
        """Code answer shape (T25): a one-line deterministic summary then the
        cited function verbatim in a fenced block, GitHub line-anchored. Never
        paraphrases code, so the model is bypassed entirely. Returns None when
        this is not a code question (no code passage carries a query identifier),
        so the caller falls back to prose composition. ``cap`` bounds how many
        code blocks a persona sees (T27 depth): an executive gets one, a builder
        up to two."""
        qtok = [t for t in _qtokens(question) if len(t) >= 3]
        ranked = []
        for c in selected:
            if c.passage.coordinate.kind.value != "symbol_line":
                continue
            loc = c.passage.coordinate.locator or {}
            ranked.append((self._ident_score(loc, qtok), c, loc))

        # T27 emphasis — among code passages that match the identifier, a quality
        # persona (tester/QE) leads with the TEST that verifies the symbol, a
        # builder with the IMPLEMENTATION. A tiebreaker only: it reorders passages
        # that already matched, never surfaces an unmatched one. `none` (default)
        # leaves the identifier ranking untouched, so non-persona callers (T25)
        # are unaffected.
        def _pbias(loc):
            is_test = "test" in (loc.get("path") or "").lower()
            if emphasis == "test":
                return 1 if is_test else 0
            if emphasis == "code":
                return 1 if not is_test else 0
            return 0

        ranked = [r for r in ranked if r[0] > 0]  # only real identifier hits qualify
        if not ranked:
            return None  # no identifier hit → let prose compose handle it
        ranked.sort(reverse=True, key=lambda x: (_pbias(x[2]), x[0], x[1].vector_score))

        citations, parts = [], []
        for hits, c, loc in ranked[: max(1, min(2, cap))]:  # 1..2 blocks by persona depth
            if hits <= 0:
                break
            pas = c.passage
            d = self.p.documents.get(pas.tenant, pas.document_id)
            title = d["title"] if d else (loc.get("path") or "code")
            n = len(citations) + 1
            citations.append(
                Citation(
                    document_id=pas.document_id,
                    document_title=title,
                    coordinate=pas.coordinate,
                    passage_id=pas.id,
                    snippet=(loc.get("summary_line") or "")[:200],
                )
            )
            summary = loc.get("summary_line") or loc.get("qualified") or ""
            body = pas.text
            if len(body) > 1800:  # keep the bubble readable; link goes to the full source
                body = body[:1800].rstrip() + "\n# … (truncated — open on GitHub)"
            parts.append(f"{summary} [{n}]\n\n```{loc.get('language', '')}\n{body}\n```")
        return "\n\n".join(parts), citations, 0, 0, 0, 0.0, model_for_tier("none")

    def _compose(self, principal, question, selected, tier):
        # T27 depth: how many sentences this designation's answer may carry —
        # headline (1) for an executive, brief (2) for delivery, full (3) for a
        # builder/quality/curator/admin/general reader. Facts stay grounded; the
        # persona only decides how much of the grounded answer to surface.
        cap = personas.depth_cap(principal.designation)
        emphasis = personas.profile_for(principal.designation)["emphasis"]
        code = self._compose_code(question, selected, cap, emphasis)
        if code is not None:
            return code
        qtok = set(_qtokens(question))
        subj_docs = set(self._subject_of(principal.tenant, question).values())

        chosen: list = []  # (passage, sentence) — up to `cap` distinct sentences
        seen_txt: set = set()

        # 1) Definition lead. When the question names a distinctive entity, open
        #    with that document's own first substantive sentences — its
        #    definition — rather than whatever co-occurrence the embedder ranked.
        lead_cap = min(2, cap)
        for did in subj_docs:
            for pas, s in self._doc_lead(principal.tenant, did, qtok, 2):
                if len(chosen) < lead_cap and s not in seen_txt:
                    chosen.append((pas, s))
                    seen_txt.add(s)
            if chosen:
                break

        # 2) Supporting / synthesis evidence pooled from the retrieved passages,
        #    each cleaned of URLs and heading labels. A subject-document sentence
        #    counts even without query overlap (a brief rarely repeats its own
        #    name); an unrelated document must actually share a query token.
        pool = []  # (score, sentence, passage, is_subject_doc)
        for c in selected:
            # Code is answered by the identifier tier and discovery, never
            # stitched into a prose answer — so a factual question is not
            # "answered" from a string that merely appears inside a test (T24).
            if c.passage.coordinate.kind.value == "symbol_line":
                continue
            is_subj = c.passage.document_id in subj_docs
            for raw in _sentences(c.passage.text):
                s = self._clean_sentence(raw)
                if self._is_heading(s):
                    continue
                base = self._sentence_score(s, qtok)
                if base <= 0 and not is_subj:
                    continue
                pool.append((base + 1.5 * is_subj + 1e-4 * c.vector_score, s, c.passage, is_subj))
        pool.sort(reverse=True, key=lambda x: x[0])

        per_doc: dict = {}
        for pas, _s in chosen:
            per_doc[pas.document_id] = per_doc.get(pas.document_id, 0) + 1
        for _score, s, pas, is_subj in pool:
            if len(chosen) >= cap:
                break
            did = pas.document_id
            doc_cap = 2 if (is_subj and subj_docs) else 1  # breadth unless it's the subject doc
            if s in seen_txt or per_doc.get(did, 0) >= doc_cap:
                continue
            chosen.append((pas, s))
            seen_txt.add(s)
            per_doc[did] = per_doc.get(did, 0) + 1

        cite_map, citations, parts = {}, [], []
        for pas, sent in chosen:
            d = self.p.documents.get(pas.tenant, pas.document_id)
            title = d["title"] if d else "document"
            if pas.id not in cite_map:
                cite_map[pas.id] = len(citations) + 1
                citations.append(
                    Citation(
                        document_id=pas.document_id,
                        document_title=title,
                        coordinate=pas.coordinate,
                        passage_id=pas.id,
                        snippet=sent[:200],
                    )
                )
            parts.append(f"{sent} [{cite_map[pas.id]}]")
        extractive = " ".join(parts)

        cost = tin = tout = 0
        saved = 0.0
        text = extractive
        model_name = model_for_tier("none")
        if tier != "none" and self.p.model.available():
            msg = [
                {"role": "system", "content": _SYSTEM_PREAMBLE},
                {"role": "user", "content": extractive},
            ]
            # T35: a provider error is LOUD — it propagates out of ask() so a
            # bake or a queued answer fails visibly instead of silently serving
            # the extractive draft under a real model's name. The keyless
            # extractive path is `available() == False`, decided above, never a
            # catch-all here.
            out = self.p.model.complete(
                principal.tenant,
                tier,
                msg,
                {
                    "temperature": 0.0,
                    "purpose": os.environ.get("KF_LEDGER_PURPOSE", "answer_bake"),
                    "question_hash": hashlib.sha1(question.encode("utf-8")).hexdigest()[:16],
                },
            )
            cost = out["cost"]
            usage = out.get("usage", {})
            tin = int(usage.get("in") or (len(_SYSTEM_PREAMBLE.split()) + len(extractive.split())))
            tout = int(usage.get("out") or max(1, len(out.get("text", "").split())))
            model_name = out.get("model_name") or model_for_tier(tier)
            input_cost = tin * (5e-6 if tier in ("deep", "escalation") else 1e-6)
            saved = self.p.cache.prompt_discount(_SYSTEM_PREAMBLE, input_cost)
            cost = max(0.0, cost - saved)
            self.p.policy.try_spend(
                principal.tenant, cost, principal.subject if principal.agent else None
            )
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
                kept.append(s)
                continue
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
        self.p.audit.write(
            principal.tenant,
            principal.subject,
            principal.agent,
            action,
            "answer",
            decision,
            trace_id,
            now_ms(),
        )

    def _from_cache(self, pay, trace_id, tenant, qlang, saved):
        from ..contracts.types import Coordinate, CoordinateKind

        cites = [
            Citation(
                c["document_id"],
                c["document_title"],
                Coordinate(CoordinateKind(c["coordinate"]["kind"]), c["coordinate"]["locator"]),
                c["passage_id"],
                c["snippet"],
            )
            for c in pay["citations"]
        ]
        return Answer(
            AnswerKind.ANSWER,
            pay["answer_text"],
            cites,
            pay["confidence"],
            trace_id,
            0.0,
            0,
            pay["tier"],
            grounding_score=pay["grounding_score"],
            tenant=tenant,
            level=pay.get("level", 0),
            why=pay.get("why"),
            lang=qlang,
            cache_hit=True,
            cost_saved=saved,
            model_name=pay.get("model_name", "cache"),
            complexity=pay.get("complexity", ""),
            authoritative_source=pay.get("authoritative_source"),
            dataset_version=pay.get("dataset_version", 0),
            reasoning=pay.get("reasoning"),
        )

    def _plain(self, kind, text, trace_id, tenant, span, g, qlang, principal, dsv=0):
        span.set(
            kind=kind.value,
            grounding=g,
            citations_count=0,
            tier="none",
            subject=principal.subject,
            roles=principal.roles,
            lang=qlang,
            dataset_version=dsv,
        )
        return Answer(
            kind,
            text,
            [],
            round(g, 3),
            trace_id,
            0.0,
            0,
            "none",
            grounding_score=g,
            tenant=tenant,
            lang=qlang,
            dataset_version=dsv,
            model_name=model_for_tier("none"),
        )
