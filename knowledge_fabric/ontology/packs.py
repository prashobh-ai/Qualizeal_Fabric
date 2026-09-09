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

AVIATION_OPS = OntologyPack(
    name="aviation-ops", version=1,
    entity_types=["Aircraft", "Procedure", "Checklist", "System", "Regulation", "Airport"],
    relation_types=["applies_to", "requires", "precedes", "governed_by", "located_at"],
    typed_facts={"procedure_step": ["Procedure", "System", "Aircraft"]},
    salient_vocab={
        "procedure": 1.0, "checklist": 0.9, "airworthiness": 1.0, "inspection": 0.9,
        "maintenance": 0.9, "regulation": 0.9, "clearance": 0.7, "turnaround": 0.8,
        "boarding": 0.6, "taxi": 0.6, "runway": 0.7, "compliance": 0.9,
    },
    entity_lexicon={
        "aircraft": "Aircraft", "procedure": "Procedure", "checklist": "Checklist",
        "system": "System", "regulation": "Regulation", "airport": "Airport",
    },
)

HEALTH = OntologyPack(
    name="health", version=1,
    entity_types=["Policy", "Procedure", "Patient", "Medication", "Guideline", "Department"],
    relation_types=["indicated_for", "contraindicated_with", "governed_by", "administered_by"],
    typed_facts={"dosage": ["Medication", "Patient", "Guideline"]},
    salient_vocab={
        "protocol": 1.0, "dosage": 1.0, "contraindication": 1.0, "guideline": 0.9,
        "consent": 0.9, "triage": 0.8, "discharge": 0.7, "medication": 0.9,
        "policy": 0.7, "procedure": 0.7, "compliance": 0.8,
    },
    entity_lexicon={
        "policy": "Policy", "procedure": "Procedure", "medication": "Medication",
        "guideline": "Guideline", "department": "Department",
    },
)

PACKS = {p.name: p for p in (QUALITY_ASSURANCE, AVIATION_OPS, HEALTH)}


def get_pack(name: str) -> OntologyPack:
    return PACKS.get(name, QUALITY_ASSURANCE)
