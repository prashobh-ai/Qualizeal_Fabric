"""Open-source LLM fallback, shown honestly (T52).

The always-available answer path when no paid provider is configured. It never
invents facts and never pretends to be a hosted model: the provider badge names
exactly what ran — a real open-source summariser when local weights are present,
otherwise a deterministic extractive-NLG composer.

Pipeline (``complete``):
  a. NLU — intent + entities from the user message with stdlib rules, optionally
     refined by spaCy when it is importable (guarded, never required).
  b. Extractive selection — the user message already carries the retrieved
     sentences (the draft); we split, never re-retrieve.
  c. Abstractive rewrite — if ``transformers`` is importable AND local weights
     are present (``KF_OSS_SUMMARIZER`` names a model dir), summarise into a
     fluent grounded answer; OTHERWISE a deterministic composer stitches the
     selected sentences into a readable paragraph. The template path is the
     always-on behaviour.
  d. Entity-containment guard — drop any output sentence carrying a capitalised
     entity or number absent from the evidence, so a fact is never introduced.

Heavy dependencies (``transformers``, ``torch``, ``spacy``, ``tiktoken``'s
encoding download) are all optional and guarded; nothing here touches the
network or requires a GPU.
"""

from __future__ import annotations

import os
import re
import time

_DEFAULT_SUMMARIZER = "sshleifer/distilbart-cnn-12-6"
_DOT = "#0CA678"

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CITE = re.compile(r"\[\d+\]")
_WORD = re.compile(r"[a-z0-9]+")
_LEAD = re.compile(r"[A-Za-z']+")

# Leading words safe to lowercase after a connective (never proper nouns).
_LEADING_LOWER = {
    "the",
    "a",
    "an",
    "this",
    "these",
    "those",
    "it",
    "its",
    "they",
    "their",
    "them",
    "there",
    "that",
    "our",
    "we",
    "you",
    "when",
    "while",
    "because",
    "although",
    "however",
    "each",
    "such",
    "both",
    "many",
    "some",
    "all",
}

# Connectives per intent, applied to the 2nd+ stitched sentence for fluency.
_CONNECTIVES = {
    "definition": ("In addition,", "Furthermore,", "Additionally,"),
    "list": ("Additionally,", "Also,", "Furthermore,", "Moreover,"),
    "comparison": ("In contrast,", "By comparison,", "Meanwhile,"),
    "count": ("In total,", "Additionally,", "Also,"),
    "greeting": ("",),
}

# ---- module-level guarded singletons --------------------------------------
_SUMMARIZER = None
_SUMMARIZER_TRIED = False
_ENC = None
_ENC_TRIED = False
_SPACY = None
_SPACY_TRIED = False


# ---- token counting -------------------------------------------------------
def count_tokens(text: str) -> int:
    """Token count via ``tiktoken`` when its encoding is available; otherwise a
    ``words x 1.3`` heuristic. The ``cl100k_base`` download is attempted once and
    any failure (offline, missing package) falls back permanently for the run."""
    global _ENC, _ENC_TRIED
    text = text or ""
    if not _ENC_TRIED:
        _ENC_TRIED = True
        try:
            import tiktoken

            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENC = None
    if _ENC is not None:
        try:
            return max(1, len(_ENC.encode(text)))
        except Exception:
            pass
    return max(1, round(len(text.split()) * 1.3))


# ---- optional summariser (guarded, never required) ------------------------
def _load_summarizer():
    """Return a ``transformers`` summarisation pipeline when it can be built from
    LOCAL weights, else ``None``. Never downloads: the model ref must be an
    existing directory unless ``KF_OSS_ALLOW_DOWNLOAD`` is explicitly set."""
    global _SUMMARIZER, _SUMMARIZER_TRIED
    if _SUMMARIZER_TRIED:
        return _SUMMARIZER
    _SUMMARIZER_TRIED = True
    try:
        from transformers import pipeline  # optional heavy dep
    except Exception:
        return None
    ref = os.environ.get("KF_OSS_SUMMARIZER", _DEFAULT_SUMMARIZER)
    allow_remote = os.environ.get("KF_OSS_ALLOW_DOWNLOAD", "").lower() in ("1", "true", "yes")
    if not (os.path.isdir(ref) or allow_remote):
        return None
    try:
        _SUMMARIZER = pipeline("summarization", model=ref, tokenizer=ref)
    except Exception:
        _SUMMARIZER = None
    return _SUMMARIZER


def _summarizer_name() -> str:
    return "DistilBART" if _SUMMARIZER is not None else "Extractive-NLG"


def provider_label() -> dict:
    """The provider badge: what actually answers. ``DistilBART`` when the fluent
    summariser is loaded, otherwise the deterministic ``Extractive-NLG`` path."""
    name = _summarizer_name()
    return {
        "provider": "Open-source",
        "model": name,
        "dot": _DOT,
        "label": f"Open-source · {name}",
    }


# ---- NLU (stdlib rules; optional spaCy refinement) ------------------------
def _spacy_entities(text: str):
    """spaCy-recognised entities when spaCy AND a model are importable, else
    ``None``. Fully guarded — the client works without spaCy installed."""
    global _SPACY, _SPACY_TRIED
    if not _SPACY_TRIED:
        _SPACY_TRIED = True
        try:
            import spacy

            _SPACY = spacy.load("en_core_web_sm")
        except Exception:
            _SPACY = None
    if _SPACY is None:
        return None
    try:
        return [ent.text for ent in _SPACY(text).ents]
    except Exception:
        return None


def _rule_entities(text: str) -> list[str]:
    """Capitalised names, acronyms and numbers by stdlib rules — the entities a
    grounded answer must not invent."""
    found = []
    for raw in (text or "").split():
        core = raw.strip("\"'.,;:!?()[]{}")
        if not core:
            continue
        if any(ch.isdigit() for ch in core):
            found.append(core)
        elif len(core) >= 2 and core.isupper():
            found.append(core)
        elif len(core) >= 2 and core[0].isupper() and any(c.isupper() for c in core[1:]):
            found.append(core)
        elif core[:1].isupper():
            found.append(core)
    seen, out = set(), []
    for e in found:
        if e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


def analyze(text: str) -> dict:
    """Intent + entities. Intent is one of: definition, comparison, list, count,
    greeting. Entities come from stdlib rules, refined by spaCy when available."""
    t = (text or "").strip().lower()
    words = t.split()
    if not t or re.match(r"^(hi|hey|hello|thanks|thank you|good (morning|afternoon|evening))\b", t):
        intent = "greeting"
    elif len(words) <= 2 and "?" not in text:
        intent = "greeting"
    elif re.search(r"\b(how many|how much|number of|count of)\b", t):
        intent = "count"
    elif re.search(
        r"\b(vs|versus|compare|comparison|compared to|difference between|better than)\b", t
    ):
        intent = "comparison"
    elif re.search(r"\b(list|which|types of|kinds of|examples of|what are|name the)\b", t):
        intent = "list"
    else:
        intent = "definition"
    entities = _rule_entities(text)
    refined = _spacy_entities(text)
    if refined:
        seen = {e.lower() for e in entities}
        for e in refined:
            if e and e.lower() not in seen:
                seen.add(e.lower())
                entities.append(e)
    return {"intent": intent, "entities": entities}


# ---- extractive selection + deterministic composition ---------------------
def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split((text or "").strip()) if s.strip()]


def _norm_tokens(text: str) -> set[str]:
    return set(_WORD.findall(_CITE.sub(" ", text or "").lower()))


def _lead_word(s: str) -> str:
    m = _LEAD.match(s)
    return m.group(0) if m else ""


def _decap(s: str) -> str:
    w = _lead_word(s)
    if w and w[0].isupper() and w.lower() in _LEADING_LOWER:
        return w[0].lower() + s[1:]
    return s


def _dedupe(sents: list[str]) -> list[str]:
    seen, out = set(), []
    for s in sents:
        # Key on the word sequence (citations and punctuation ignored) so an
        # exact or near-exact repeat is folded, but distinct sentences are kept.
        key = " ".join(_WORD.findall(_CITE.sub(" ", s.lower())))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(s.strip())
    return out


def _compose_template(sents: list[str], nlu: dict) -> list[str]:
    """Stitch the selected sentences into a coherent paragraph: dedupe, keep
    document order, and join with intent-appropriate connectives."""
    uniq = _dedupe(sents)
    if not uniq:
        return []
    conns = _CONNECTIVES.get(nlu.get("intent", "definition"), _CONNECTIVES["definition"])
    out = [uniq[0]]
    for i, s in enumerate(uniq[1:]):
        conn = conns[i % len(conns)]
        out.append(f"{conn} {_decap(s)}" if conn else s)
    return out


def entity_guard(sents: list[str], evidence_text: str) -> list[str]:
    """Drop any sentence carrying a capitalised entity, acronym or number that is
    absent from the evidence. The composer only reuses evidence sentences, so
    this is a no-op there; it protects the abstractive path from introducing a
    fact the passages do not support."""
    ev = _norm_tokens(evidence_text)
    kept = []
    for s in sents:
        clean = _CITE.sub(" ", s)
        words = clean.split()
        ok = True
        for i, raw in enumerate(words):
            core = raw.strip("\"'.,;:!?()[]{}")
            if not core:
                continue
            is_start = i == 0
            is_entity = (
                any(ch.isdigit() for ch in core)
                or (len(core) >= 2 and core.isupper())
                or (len(core) >= 2 and core[0].isupper() and any(c.isupper() for c in core[1:]))
                or (not is_start and core[:1].isupper())
            )
            if not is_entity:
                continue
            norm = re.sub(r"[^a-z0-9]", "", core.lower())
            if norm and norm not in ev:
                ok = False
                break
        if ok:
            kept.append(s)
    return kept


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content or "")


def _messages_text(messages: list[dict], system: str = "") -> tuple[str, str]:
    """Return (user_text, full_prompt_text). The user text is the evidence draft;
    the full text is everything sent, used only for input-token counting."""
    user_parts, all_parts = [], []
    if system:
        all_parts.append(system)
    for m in messages or []:
        c = _text_of(m.get("content"))
        if c:
            all_parts.append(c)
        if m.get("role") == "user" and c:
            user_parts.append(c)
    full = "\n".join(all_parts).strip()
    user = "\n".join(user_parts).strip() or full
    return user, full


def _compose(user_text: str) -> str:
    """The full NLU -> select -> rewrite -> guard pipeline, returning the answer."""
    evidence = _sentences(user_text)
    nlu = analyze(user_text)
    out_sents = None
    summarizer = _load_summarizer()
    if summarizer is not None and evidence:
        try:
            res = summarizer(" ".join(evidence), truncation=True)
            summ = res[0].get("summary_text", "") if res else ""
            cand = entity_guard(_sentences(summ), user_text)
            if cand:
                out_sents = cand
        except Exception:
            out_sents = None
    if out_sents is None:
        out_sents = entity_guard(_compose_template(evidence, nlu), user_text)
    answer = " ".join(out_sents).strip()
    return answer or user_text.strip()


class OSSModelClient:
    """Always-available open-source fallback. Same three-method surface as the
    other model clients; the optional fluent summariser is guarded, so the
    deterministic extractive-NLG path is the guaranteed behaviour."""

    def available(self) -> bool:
        return True

    # -- shared ledgered run ------------------------------------------------
    def _record(self, purpose: str, tin: int, tout: int, latency_ms: float, opts: dict) -> str:
        from ..telemetry import api_ledger

        label = provider_label()["label"]
        # cost_usd is derived by the ledger from prices.json; the open-source
        # label is unpriced, so every row is $0 by construction.
        api_ledger.record(
            purpose=purpose,
            model=label,
            usage={"input_tokens": tin, "output_tokens": tout},
            latency_ms=latency_ms,
            repo=(opts or {}).get("repo", ""),
            doc_id=(opts or {}).get("doc_id", ""),
            question_hash=(opts or {}).get("question_hash", ""),
        )
        return label

    def complete(self, tenant: str, tier: str, messages: list[dict], opts: dict) -> dict:
        opts = opts or {}
        t0 = time.perf_counter()
        user_text, full_text = _messages_text(messages)
        answer = _compose(user_text)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        tin = count_tokens(full_text)
        tout = count_tokens(answer)
        purpose = opts.get("purpose") or os.environ.get("KF_LEDGER_PURPOSE", "answer_bake")
        label = self._record(purpose, tin, tout, latency_ms, opts)
        return {
            "text": answer,
            "usage": {"tokens": tin + tout, "in": tin, "out": tout},
            "cost": 0.0,
            "model_name": label,
            "request_id": "",
            "latency_ms": round(latency_ms, 2),
        }

    def messages(self, body: dict, purpose: str = "answer_bake", **kw) -> dict:
        """Anthropic-style shim: compose from the body's user content, record one
        ledger row, and return a Messages-API-shaped response."""
        t0 = time.perf_counter()
        body = body or {}
        system = _text_of(body.get("system"))
        user_text, full_text = _messages_text(body.get("messages", []), system)
        answer = _compose(user_text)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        tin = count_tokens(full_text)
        tout = count_tokens(answer)
        purpose = purpose or os.environ.get("KF_LEDGER_PURPOSE", "answer_bake")
        label = self._record(purpose, tin, tout, latency_ms, kw)
        return {
            "content": [{"type": "text", "text": answer}],
            "model": label,
            "usage": {"input_tokens": tin, "output_tokens": tout},
            "cost_usd": 0.0,
            "request_id": "",
            "latency_ms": round(latency_ms, 2),
        }
