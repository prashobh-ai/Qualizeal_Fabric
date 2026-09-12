"""Core domain types shared across the platform.

These are plain, serialisable data structures. They deliberately carry no
behaviour and no engine-specific detail so that every layer (ingestion,
answer, surfaces, governance) can depend on them without depending on any
concrete adapter. Every type that touches stored data carries ``tenant``
so tenant isolation (invariant I5) is expressible everywhere.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------
# Provenance & coordinates (invariant I2: every citation resolves to a place)
# --------------------------------------------------------------------------
class CoordinateKind(StrEnum):
    PAGE_PARAGRAPH = "page_paragraph"  # text documents
    BBOX = "bbox"  # scans / images
    TIMESTAMP = "timestamp"  # audio / video
    CELL = "cell"  # tables
    SYMBOL_LINE = "symbol_line"  # code


@dataclass
class Coordinate:
    """A precise, resolvable location inside a source document."""

    kind: CoordinateKind
    # A free-form locator payload interpreted by surfaces to open the exact place.
    # e.g. {"page": 3, "paragraph": 2} or {"start_s": 12.4, "end_s": 18.0}
    locator: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        if self.kind == CoordinateKind.PAGE_PARAGRAPH:
            return f"p.{self.locator.get('page')} ¶{self.locator.get('paragraph')}"
        if self.kind == CoordinateKind.TIMESTAMP:
            return f"@{self.locator.get('start_s')}s–{self.locator.get('end_s')}s"
        if self.kind == CoordinateKind.CELL:
            return f"cell[{self.locator.get('row')},{self.locator.get('col')}]"
        if self.kind == CoordinateKind.SYMBOL_LINE:
            return f"{self.locator.get('symbol')}:{self.locator.get('line')}"
        if self.kind == CoordinateKind.BBOX:
            return f"p.{self.locator.get('page')} bbox{self.locator.get('bbox')}"
        return str(self.locator)


@dataclass
class Provenance:
    """Immutable link from a derived artefact back to its origin (I8)."""

    content_hash: str
    source: str
    source_version: str
    coordinate: Coordinate | None = None


# --------------------------------------------------------------------------
# Raw intake & converted documents
# --------------------------------------------------------------------------
@dataclass
class RawItem:
    """A canonical record emitted by any connector or intake door."""

    tenant: str
    source: str
    source_version: str
    uri: str
    mime: str
    title: str
    bytes_: bytes
    language: str = "en"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Region:
    """A converted, addressable region of a document."""

    text: str
    coordinate: Coordinate
    media_ref: str | None = None


@dataclass
class ConvertedDocument:
    language: str
    regions: list[Region]
    media_refs: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Passages (the retrieval unit) — Section 8
# --------------------------------------------------------------------------
@dataclass
class Passage:
    id: str
    tenant: str
    document_id: str
    text: str
    abstract: str  # one sentence (tiered retrieval)
    overview: str  # one paragraph
    coordinate: Coordinate
    provenance: Provenance
    version: int = 1
    superseded_by: str | None = None
    embedding_ref: str | None = None
    lexical_ref: str | None = None


@dataclass
class Document:
    id: str
    tenant: str
    source: str
    source_version: str
    content_hash: str
    type: str
    language: str
    title: str
    uri: str
    ingested_at: int
    status: str = "active"
    current_version: int = 1
    # scope label used by permission-before-ranking (I6). A passage inherits it.
    acl: list[str] = field(default_factory=lambda: ["public"])
    # T41 — persisted intake metadata: ``source_kind`` (document|table|image|jira|
    # confluence|code|analysis), ``citation_url``, ``acl``, ``arrived_at`` plus any
    # connector-specific keys (Jira fields, Confluence page version, …).
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Graph — Section 11
# --------------------------------------------------------------------------
@dataclass
class GraphNode:
    id: str
    tenant: str
    canonical_key: str
    type: str
    labels: list[str] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)


@dataclass
class GraphEdge:
    id: str
    tenant: str
    src: str
    dst: str
    relation: str
    typed_fact: dict[str, Any] | None = None
    weight: float = 1.0
    contextual_weight: float = 0.0
    provenance: list[Provenance] = field(default_factory=list)
    conflict_flag: bool = False


# --------------------------------------------------------------------------
# Retrieval & answers — Section 10
# --------------------------------------------------------------------------
@dataclass
class Candidate:
    passage: Passage
    lexical_score: float = 0.0
    vector_score: float = 0.0
    fused_score: float = 0.0
    graph_hops: int = 0  # 0 = came from direct retrieval
    source_of: str = "hybrid"  # hybrid | graph


@dataclass
class Citation:
    document_id: str
    document_title: str
    coordinate: Coordinate
    passage_id: str
    snippet: str

    def render(self) -> str:
        return f"{self.document_title} ({self.coordinate.render()})"


class AnswerKind(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    GAP = "gap"


@dataclass
class Answer:
    """The single output contract of the answer service (Section 10)."""

    kind: AnswerKind
    answer_text: str
    citations: list[Citation]
    confidence: float
    trajectory_id: str
    cost: float
    tokens: int
    tier: str
    grounding_score: float = 0.0
    clarify_back: str | None = None
    tenant: str = ""
    level: int = 0  # model-selector level (1..4)
    why: dict | None = None  # the why-card (selector decision)
    lang: str = "en"
    cache_hit: bool = False
    cost_saved: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    model_name: str = ""  # which model ran (multi-model gateway)
    complexity: str = ""  # simple | medium | complex
    authoritative_source: dict | None = None
    dataset_version: int = 0
    reasoning: dict | None = None  # multistep/conditional trace (Section A)
    understood_as: str | None = None  # T26 — the rewritten question, when context resolved one
    suggestions: list[str] | None = None  # T26 — clarify-back chips
    role_view: dict | None = None  # T27 — role-conditioned lens (asker/curator/admin/agent)
    timing: dict | None = None  # T56 — phase / active-idle block ({phase_ms, active_ms, idle_ms…})

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "answer_text": self.answer_text,
            "understood_as": self.understood_as,
            "suggestions": self.suggestions or [],
            "role_view": self.role_view,
            "level": self.level,
            "why": self.why,
            "lang": self.lang,
            "cache_hit": self.cache_hit,
            "cost_saved": round(self.cost_saved, 6),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "model_name": self.model_name,
            "complexity": self.complexity,
            "authoritative_source": self.authoritative_source,
            "dataset_version": self.dataset_version,
            "reasoning": self.reasoning,
            "timing": self.timing,
            "citations": [
                {
                    "document_id": c.document_id,
                    "document_title": c.document_title,
                    "coordinate": {
                        "kind": c.coordinate.kind.value,
                        "locator": c.coordinate.locator,
                    },
                    "coordinate_render": c.coordinate.render(),
                    "passage_id": c.passage_id,
                    "snippet": c.snippet,
                }
                for c in self.citations
            ],
            "confidence": round(self.confidence, 4),
            "grounding_score": round(self.grounding_score, 4),
            "trajectory_id": self.trajectory_id,
            "cost": round(self.cost, 6),
            "tokens": self.tokens,
            "tier": self.tier,
            "clarify_back": self.clarify_back,
        }


# --------------------------------------------------------------------------
# Identity & policy — Section 12
# --------------------------------------------------------------------------
@dataclass
class Principal:
    subject: str
    tenant: str
    roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)  # accessible ACL labels
    agent: bool = False
    # T27 — the user's organisational designation (developer, tester, delivery
    # head, CTO, …), captured by the admin at access-grant time. It conditions
    # how an answer is framed and pitched; it never widens what is retrievable
    # (that is `scopes`/ACL, enforced before ranking).
    designation: str = ""

    def accessible_acls(self) -> list[str]:
        acls = set(self.scopes) | {"public"}
        return sorted(acls)


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    CLARIFY = "clarify"


@dataclass
class PolicyResult:
    decision: Decision
    reason: str = ""


# --------------------------------------------------------------------------
# Jobs / queue — Section 7 & 8
# --------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    tenant: str
    kind: str
    payload: dict[str, Any]
    state: str = "pending"
    attempts: int = 0
    lease: float | None = None
    dead_letter_reason: str | None = None
