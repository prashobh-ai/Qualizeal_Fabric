"""Answer service checklist (Section 20) + Runbook 3.2 golden path."""
import os
import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind
from knowledge_fabric.tenants import demo
from tests.util import seeded


class TestAnswer(unittest.TestCase):
    def setUp(self):
        self.p = seeded(["qualizeal"])
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, "qualizeal", "asker.public")

    def test_grounded_answer_has_citations_that_resolve(self):
        a = self.svc.ask(self.asker, "what must a release achieve before promotion?")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertTrue(a.citations)
        for c in a.citations:
            self.assertTrue(c.coordinate.locator, "citation opens an exact place (I2)")

    def test_multi_document_citations(self):
        a = self.svc.ask(self.asker, "which requirement has a traceability gap?")
        docs = {c.document_id for c in a.citations}
        self.assertGreaterEqual(len(docs), 2, "graph/hybrid pulls connected cross-doc evidence")

    def test_unsupported_question_refuses(self):
        a = self.svc.ask(self.asker, "what is the capital of France?")
        self.assertIn(a.kind, (AnswerKind.GAP, AnswerKind.CLARIFY))
        self.assertEqual(a.citations, [])

    def test_grounding_gate_five_signals(self):
        qvec = self.p.embedder.embed(["coverage acceptance criteria"])[0]
        from knowledge_fabric.contracts.types import Candidate
        cands = [Candidate(passage=p, vector_score=0.5, fused_score=0.02)
                 for p in self.p.passages.for_tenant("qualizeal")[:4]]
        signals, g = self.svc._grounding("coverage acceptance criteria", qvec, cands)
        self.assertEqual(set(signals), {"retrieval", "semantic", "coverage", "agreement", "resolvable"})
        self.assertTrue(0.0 <= g <= 1.0)

    def test_trajectory_id_present_and_traced(self):
        a = self.svc.ask(self.asker, "what is the acceptance criteria for coverage?")
        self.assertTrue(a.trajectory_id)
        spans = self.p.telemetry.trace(a.trajectory_id)
        names = {s["name"] for s in spans}
        self.assertIn("answer", names)
        self.assertIn("answer.retrieve", names)
        self.assertIn("answer.ground", names)

    def test_one_trace_per_answer(self):
        a = self.svc.ask(self.asker, "what is the acceptance criteria for coverage?")
        roots = [s for s in self.p.telemetry.trace(a.trajectory_id) if s["name"] == "answer"]
        self.assertEqual(len(roots), 1)

    def test_model_off_still_returns_cited_answer(self):
        os.environ["KF_MODEL_MODE"] = "off"
        p = seeded(["qualizeal"], model_mode="off")
        svc = AnswerService(p)
        self.assertFalse(p.model_available())
        asker = demo.principal_for(p, "qualizeal", "asker.public")
        a = svc.ask(asker, "what must a release achieve before promotion?")
        self.assertEqual(a.kind, AnswerKind.ANSWER)
        self.assertTrue(a.citations)
        self.assertEqual(a.tier, "none")
        os.environ["KF_MODEL_MODE"] = "mock"

    def test_post_check_drops_unsupported_sentences(self):
        from knowledge_fabric.contracts.types import Candidate
        sel = [Candidate(passage=p) for p in self.p.passages.for_tenant("qualizeal")[:3]]
        text = "Coverage is ninety five percent. Elephants live in the Arctic tundra always."
        kept = self.svc._postcheck(text, sel)
        self.assertNotIn("Elephants", kept)

    def test_hybrid_rrf_fuses_both_lists(self):
        vec = [("p1", 0.9), ("p2", 0.5)]
        lex = [("p2", 3.0), ("p3", 1.0)]
        fused = dict(self.svc._rrf(vec, lex))
        self.assertGreater(fused["p2"], fused["p1"])   # appears in both -> boosted


if __name__ == "__main__":
    unittest.main()
