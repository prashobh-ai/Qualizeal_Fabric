"""Known-question registry per persona (T82).

The business slice depends on a curated set of instant, governed questions — the
questions a reader *already knows the fabric answers*. This module generalises
that to every audience: a governed list of question patterns, each tagged with
the personas it serves, the answer kind, its source, a freshness target and a
few concrete examples.

The registry is **metadata, not a new engine**. Every entry still resolves
through the existing facts and retrieval paths. What the registry adds is:

  (a) per-persona *suggested questions* on Home (``suggestions``),
  (b) a *fast-path lock* the answer service honours — a matched known question
      is answered on the fast path by design, never escalated (``match``), and
  (c) a governed list the Curator maintains — view, add, edit, disable, each
      change audited (``entries`` / ``upsert`` / ``set_enabled``).

Layout follows the repo convention for authored governed data (like
``telemetry/prices.json``): the seed ships in the package at
``answer/known_questions.json``; a curator's edits persist to the fabric-data
runtime file at ``data/known_questions.json`` (honouring ``KF_DATA_ROOT``) and,
once written, that file is the whole registry. Reads overlay the runtime file on
the packaged seed, so a fresh checkout has the full starter set and an edited
fabric keeps the curator's version.
"""

from __future__ import annotations

import os
import re

from .. import fabric_data

_SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_questions.json")
_RUNTIME_NAME = "known_questions.json"

_TOKEN = re.compile(r"[a-z0-9]+")
_SLOT = re.compile(r"<[^>]+>")
# The question-word / filler tokens a pattern's *fixed* words never include, so
# a match keys off real content words ("projects", "coverage") not "how"/"what".
_STOP = {
    "how",
    "what",
    "which",
    "who",
    "when",
    "where",
    "why",
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
    "do",
    "does",
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
    "many",
    "much",
    "me",
    "tell",
    "about",
    "show",
    "list",
    "give",
}

# --------------------------------------------------------------------------
# Persona → the registry *audiences* it should see. The answer-conditioning
# personas (``personas.persona_for``) are a small set; the registry's audience
# labels are richer (a developer and an architect share the ``developer``
# persona but want different instant questions). This map fans one persona out
# to the audience labels whose entries it should be offered.
# --------------------------------------------------------------------------
AUDIENCES_FOR: dict[str, list[str]] = {
    "developer": ["developer", "architect"],
    "quality": ["tester", "quality"],
    "delivery": ["business", "sales", "delivery"],
    "executive": ["business", "delivery"],
    "curation": ["curator", "admin"],
    "operations": ["admin", "curator"],
    "general": ["business", "hr", "developer"],
}


def audiences_for(persona: str) -> list[str]:
    """The registry audiences a persona is offered questions from."""
    return AUDIENCES_FOR.get(persona, AUDIENCES_FOR["general"])


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", (s or "").lower())).strip()


def _content_tokens(s: str) -> set[str]:
    return {t for t in _TOKEN.findall((s or "").lower()) if t not in _STOP}


def _fixed_tokens(pattern: str) -> set[str]:
    """The pattern's fixed content tokens — its literal words with the ``<slot>``
    placeholders and question-word filler removed. These are what a free-text
    question must contain (all of them) to match this pattern."""
    return _content_tokens(_SLOT.sub(" ", pattern or ""))


class Registry:
    """An in-memory view of the known-question registry: the ordered entries plus
    the derived lookups. Cheap to build (two small JSON files), so callers load
    it per request and always see the current curator state."""

    def __init__(self, entries: list[dict]):
        self.entries: list[dict] = entries
        for e in self.entries:
            e.setdefault("enabled", True)
            e["_fixed"] = _fixed_tokens(e.get("pattern", ""))
            e["_examples_norm"] = {_norm(x) for x in (e.get("examples") or [])}

    # ---- loading -----------------------------------------------------
    @classmethod
    def load(cls) -> Registry:
        """The seed overlaid by the fabric-data runtime file (curator edits).
        The runtime file, once written, is the whole registry."""
        runtime = fabric_data.read_json(fabric_data.data_path(_RUNTIME_NAME))
        if isinstance(runtime, dict) and isinstance(runtime.get("entries"), list):
            return cls([dict(e) for e in runtime["entries"]])
        seed = fabric_data.read_json(_SEED_PATH, {"entries": []}) or {"entries": []}
        return cls([dict(e) for e in (seed.get("entries") or [])])

    # ---- reads -------------------------------------------------------
    @staticmethod
    def _public(e: dict) -> dict:
        """An entry without the internal derived fields (safe to serialise)."""
        return {k: v for k, v in e.items() if not k.startswith("_")}

    def all(self) -> list[dict]:
        """Every entry, enabled and disabled — the Curator's governed list."""
        return [self._public(e) for e in self.entries]

    def enabled(self) -> list[dict]:
        return [e for e in self.entries if e.get("enabled", True)]

    def for_audiences(self, audiences) -> list[dict]:
        aud = set(audiences or [])
        return [e for e in self.enabled() if aud & set(e.get("persona") or [])]

    def suggestions(self, persona: str, limit: int = 6) -> list[dict]:
        """The persona's instant questions for Home: one example per entry across
        the persona's audiences, in registry order, deduplicated."""
        out, seen = [], set()
        for e in self.for_audiences(audiences_for(persona)):
            ex = (e.get("examples") or [None])[0] or e.get("pattern", "")
            key = _norm(ex)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "question": ex,
                    "kind": e.get("answer_kind", ""),
                    "family": (e.get("persona") or [""])[0],
                    "id": e.get("id", ""),
                    "freshness_target_s": e.get("freshness_target_s"),
                }
            )
            if len(out) >= limit:
                break
        return out

    def match(self, question: str, persona: str | None = None) -> dict | None:
        """The most specific enabled entry a free-text question matches, or None.

        A match is an exact example (highest priority) or a pattern whose fixed
        content tokens are all present in the question; the entry with the most
        fixed tokens wins, so a specific pattern is not shadowed by a broad one.
        ``persona`` narrows to that reader's audiences when given; the fast-path
        lock passes it None (a governed question is instant for anyone)."""
        qn = _norm(question)
        qtok = _content_tokens(question)
        pool = self.enabled()
        if persona:
            aud = set(audiences_for(persona))
            pool = [e for e in pool if aud & set(e.get("persona") or [])] or pool
        best, best_rank = None, (-1, -1)
        for e in pool:
            if qn in e["_examples_norm"]:
                rank = (2, len(e["_fixed"]))
            elif e["_fixed"] and e["_fixed"] <= qtok:
                rank = (1, len(e["_fixed"]))
            else:
                continue
            if rank > best_rank:
                best, best_rank = e, rank
        return self._public(best) if best else None

    # ---- writes (curator) -------------------------------------------
    def _persist(self) -> str:
        payload = {"version": 1, "entries": [self._public(e) for e in self.entries]}
        return fabric_data.write_json(fabric_data.data_path(_RUNTIME_NAME, mkdir=True), payload)

    def upsert(self, entry: dict) -> dict:
        """Add a new known question or replace an existing one (by ``id``), then
        persist. Returns the stored public entry. Raises ValueError on a missing
        id/pattern so the caller can 400."""
        eid = (entry.get("id") or "").strip()
        pattern = (entry.get("pattern") or "").strip()
        if not eid or not pattern:
            raise ValueError("a known question needs an id and a pattern")
        stored = {
            "id": eid,
            "pattern": pattern,
            "persona": [str(x) for x in (entry.get("persona") or [])] or ["business"],
            "answer_kind": entry.get("answer_kind") or "facts",
            "source": entry.get("source") or "corpus",
            "template": entry.get("template") or pattern,
            "freshness_target_s": entry.get("freshness_target_s", 3),
            "examples": [str(x) for x in (entry.get("examples") or [])] or [pattern],
            "enabled": bool(entry.get("enabled", True)),
        }
        materialised = dict(stored)
        materialised["_fixed"] = _fixed_tokens(pattern)
        materialised["_examples_norm"] = {_norm(x) for x in stored["examples"]}
        idx = next((i for i, e in enumerate(self.entries) if e.get("id") == eid), None)
        if idx is None:
            self.entries.append(materialised)
        else:
            self.entries[idx] = materialised
        self._persist()
        return stored

    def set_enabled(self, entry_id: str, enabled: bool) -> dict:
        """Enable or disable one entry (the governed way to retire a question
        without losing it), then persist. Raises KeyError if unknown."""
        for e in self.entries:
            if e.get("id") == entry_id:
                e["enabled"] = bool(enabled)
                self._persist()
                return self._public(e)
        raise KeyError(entry_id)
