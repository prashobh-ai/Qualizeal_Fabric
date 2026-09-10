"""Entity / relationship / typed-fact extraction (pipeline Step 4).

Deliberately simple and deterministic — pattern + ontology-lexicon based —
but it produces exactly the shapes the graph needs:
  * concept & entity mentions (typed against the ontology pack),
  * stated relations from light patterns (X <verb> Y),
  * a confidence per extraction; low-confidence items are flagged for the
    curator queue rather than trusted or dropped (Section 7 Step 4).

Salience uses the pack's domain-salient vocabulary, never raw frequency
(Section 11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..ontology.packs import OntologyPack

_CAP = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,3})\b")
_REL_PATTERNS = [
    (re.compile(r"(.+?)\s+(?:requires|depends on)\s+(.+)", re.I), "depends_on"),
    (re.compile(r"(.+?)\s+(?:verifies|validates|covers)\s+(.+)", re.I), "verifies"),
    (re.compile(r"(.+?)\s+(?:complies with|conforms to)\s+(.+)", re.I), "complies_with"),
    (re.compile(r"(.+?)\s+(?:belongs to|is part of)\s+(.+)", re.I), "belongs_to"),
]
_STOP = {"The", "This", "That", "These", "Those", "It", "A", "An", "In", "On", "For", "To", "And"}


@dataclass
class Mention:
    text: str
    type: str
    confidence: float


@dataclass
class Relation:
    src: str
    dst: str
    relation: str
    confidence: float


def extract(text: str, pack: OntologyPack) -> tuple[list[Mention], list[Relation]]:
    mentions: dict[str, Mention] = {}
    low = text.lower()

    # ontology-lexicon keyword hits -> typed entities (high confidence)
    for kw, etype in pack.entity_lexicon.items():
        if kw in low:
            mentions.setdefault(kw, Mention(kw, etype, 0.9))

    # capitalised phrases -> concept nodes (lower confidence, curator-reviewable)
    for m in _CAP.findall(text):
        phrase = m.strip()
        if phrase in _STOP or len(phrase) < 3:
            continue
        key = phrase.lower()
        if key not in mentions:
            mentions.setdefault(key, Mention(key, "Concept", 0.5))

    relations: list[Relation] = []
    for sentence in re.split(r"[.\n]", text):
        for pat, rel in _REL_PATTERNS:
            m = pat.match(sentence.strip())
            if m:
                src = _norm(m.group(1))
                dst = _norm(m.group(2))
                if src and dst:
                    relations.append(Relation(src, dst, rel, 0.7))
    return list(mentions.values()), relations


def _norm(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 ]", " ", s).strip().lower()
    return " ".join(s.split()[:5])


def salience(text: str, pack: OntologyPack) -> dict[str, float]:
    """Domain-salient scoring: weight terms by the pack vocab, not raw count."""
    words = re.findall(r"[a-z]+", text.lower())
    scores: dict[str, float] = {}
    for w in words:
        if w in pack.salient_vocab:
            scores[w] = scores.get(w, 0.0) + pack.salient_vocab[w]
    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
