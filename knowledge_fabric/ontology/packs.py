"""Per-domain ontology packs (Section 11), versioned.

A pack defines entity types, relation types, typed-fact schemas and a
domain-salient vocabulary used for salience scoring (never raw frequency).
A new tenant selects or extends a pack; it is never a blank page. Packs ship
generic and client-agnostic (I: multi-tenancy & genericity).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OntologyPack:
    name: str
    version: int
    entity_types: list[str]
    relation_types: list[str]
    typed_facts: dict[str, list[str]]         # fact_type -> ordered role names
    salient_vocab: dict[str, float]           # term -> salience weight
    entity_lexicon: dict[str, str] = field(default_factory=dict)  # keyword -> entity_type


QUALITY_ASSURANCE = OntologyPack(
    name="quality-assurance", version=1,
    entity_types=["TestPlan", "TestCase", "Requirement", "Defect", "Release", "Component", "Standard"],
    relation_types=["verifies", "covers", "depends_on", "blocks", "belongs_to", "complies_with"],
    typed_facts={"coverage": ["TestCase", "Requirement", "Release"]},
    salient_vocab={
        "requirement": 1.0, "traceability": 1.0, "coverage": 0.9, "defect": 0.9,
        "regression": 0.8, "release": 0.7, "acceptance": 0.9, "verification": 0.9,
        "test": 0.6, "plan": 0.5, "risk": 0.8, "compliance": 0.9,
    },
    entity_lexicon={
        "requirement": "Requirement", "test case": "TestCase", "test plan": "TestPlan",
        "defect": "Defect", "release": "Release", "component": "Component", "standard": "Standard",
    },
)

# The QualiZeal fabric ships the quality-assurance pack. Additional
# vertical packs (aviation, health, …) are registered at load time by
# whatever seeds documents in those domains — the test fixtures do this
# (L0.2), so no vertical-domain vocabulary lives in the shipped package.
PACKS = {p.name: p for p in (QUALITY_ASSURANCE,)}


def register(pack: OntologyPack) -> None:
    """Register an additional ontology pack (used by test fixtures that seed
    documents in a non-QA domain). Idempotent."""
    PACKS[pack.name] = pack


def get_pack(name: str) -> OntologyPack:
    return PACKS.get(name, QUALITY_ASSURANCE)
