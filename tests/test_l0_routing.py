"""L0.4 — routing sanity for definitions (D3).

A 'what is X' question whose subject matches the top document's title
resolves to Level 1 (Quote it), and document spread never escalates a
plain lookup past Level 2. Verified at the selector level (pure, no model)
and end-to-end through the answer service.
"""

from __future__ import annotations

import os
import unittest

from knowledge_fabric.answer import selector as sel
from knowledge_fabric.app import Platform
from knowledge_fabric.tenants import demo


class _Cand:
    """Minimal stand-in for answer.Candidate (only .passage.document_id used)."""

    def __init__(self, document_id):
        self.passage = type("P", (), {"document_id": document_id})()


class TestSelectorDefinitionRule(unittest.TestCase):
    def test_definition_matches_title_is_level_1(self):
        selected = [
            _Cand("doc-validaite"),
            _Cand("doc-a"),
            _Cand("doc-b"),
            _Cand("doc-c"),
            _Cand("doc-d"),
        ]
        titles = {"doc-validaite": "ValidAIte Product Overview"}
        d = sel.classify(
            "What is ValidAIte?", selected, grounding=0.8, graph_used=False, doc_titles=titles
        )
        self.assertEqual(d["level"], 1)
        self.assertEqual(d["level_name"], "lookup")
        self.assertTrue(any(r["code"] == "definition" for r in d["reasons"]))
        # document spread did NOT escalate the definition
        self.assertFalse(any(r["code"] == "multi_document" for r in d["reasons"]))

    def test_lookup_spread_capped_at_level_2(self):
        # a plain lookup (not a definition, not reasoning) spanning many docs
        selected = [_Cand(f"doc-{i}") for i in range(6)]
        d = sel.classify(
            "which requirement has a traceability gap?",
            selected,
            grounding=0.6,
            graph_used=False,
            doc_titles={},
        )
        self.assertLessEqual(d["level"], 2, d["reasons"])

    def test_comparison_still_reaches_level_3(self):
        selected = [_Cand(f"doc-{i}") for i in range(4)]
        d = sel.classify(
            "compare the test strategy and the release runbook",
            selected,
            grounding=0.6,
            graph_used=False,
            doc_titles={},
        )
        self.assertEqual(d["level"], 3)
        self.assertTrue(
            any(r["code"] in ("reasoning_intent", "multi_document") for r in d["reasons"])
        )

    def test_doc_spread_measured_over_top_five(self):
        # 3 distinct docs in the top-5 but a long tail of others → still counted
        # only over the top-5. Here the top-5 are all the same doc, so a lookup
        # stays Level 1-ish (no multi_document).
        selected = [_Cand("same")] * 5 + [_Cand("x"), _Cand("y"), _Cand("z")]
        d = sel.classify(
            "what is the acceptance criteria for coverage?",
            selected,
            grounding=0.7,
            graph_used=False,
            doc_titles={},
        )
        self.assertFalse(any(r["code"] == "multi_document" for r in d["reasons"]))


class TestDefinitionEndToEnd(unittest.TestCase):
    def setUp(self):
        os.environ["KF_MODEL_MODE"] = "mock"
        self.p = Platform(db_path=":memory:", blob_root="./data/test-blobs")
        demo.seed(self.p)
        # a single authoritative definition document whose title carries the term
        from knowledge_fabric.ingestion.intake import IngestWorker, Intake

        intake = Intake(self.p)
        worker = IngestWorker(self.p, intake)
        intake.submit(
            intake.canonical(
                "def-fabric",
                "files",
                "file://products/validaite.md",
                "ValidAIte Product Overview",
                b"# ValidAIte\n\nValidAIte delivers exactly what is promised: scope, schedule "
                b"and quality with no surprises. It is a maturity baseline, a gap analysis, and "
                b"a prioritised roadmap with quantified ROI for each recommendation.\n",
                mime="text/markdown",
                acl=["public"],
                ontology="quality-assurance",
            )
        )
        worker.drain()
        from knowledge_fabric.answer.service import AnswerService

        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, "def-fabric", "asker.public")

    def test_what_is_validaite_is_level_1(self):
        a = self.svc.ask(self.asker, "What is ValidAIte?")
        self.assertEqual(a.level, 1, a.why)
        self.assertTrue(a.citations)
        # the ValidAIte document is cited first
        self.assertIn("ValidAIte", a.citations[0].document_title)


if __name__ == "__main__":
    unittest.main()
