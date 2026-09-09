"""L0.3 — question bank generated from the loaded corpus (D2)."""
from __future__ import annotations

import os
import unittest

from knowledge_fabric.app import Platform
from knowledge_fabric.evaluation import bank
from knowledge_fabric.tenants import demo
from tests.fixtures import synthetic_corpus


class TestBankGeneration(unittest.TestCase):
    def setUp(self):
        os.environ["KF_MODEL_MODE"] = "mock"
        self.p = Platform(db_path=":memory:", blob_root="./data/test-blobs")
        demo.seed(self.p)
        synthetic_corpus.load_into(self.p, "bank-fabric")

    def _bank(self):
        return self.p.db.query(
            "SELECT question, expected_docs, family FROM question_bank WHERE tenant=?",
            ("bank-fabric",))

    def test_subject_strips_code_and_generic_words(self):
        self.assertEqual(bank._subject("03 Product ValidAIte"), "ValidAIte")
        self.assertEqual(bank._subject("Test Strategy v3"), "Test Strategy v3")
        self.assertEqual(bank._subject("06 Company Mission Purpose"), "Mission Purpose")

    def test_generate_produces_gated_entries(self):
        kept = bank.generate(self.p, "bank-fabric")
        rows = self._bank()
        self.assertEqual(len(rows), kept)
        # every generated question references at least two real documents
        loaded_uris = {(d.get("uri") or "").replace("file://", "").replace("upload://", "")
                       for d in self.p.documents.list("bank-fabric")}
        for r in rows:
            uris = [u for u in (r["expected_docs"] or "").split(",") if u]
            self.assertGreaterEqual(len(uris), bank.MIN_CITED_DOCS)
            for u in uris:
                self.assertIn(u, loaded_uris,
                              f"suggestion cites {u!r} which is not a loaded document")

    def test_generate_replaces_prior_bank(self):
        bank.generate(self.p, "bank-fabric")
        first = len(self._bank())
        bank.generate(self.p, "bank-fabric")
        self.assertEqual(len(self._bank()), first)  # idempotent, not doubled

    def test_no_synthetic_bank_leaks_from_seed(self):
        # A fresh product fabric has no bank until a corpus loads.
        self.assertEqual(
            self.p.db.query("SELECT COUNT(*) c FROM question_bank WHERE tenant=?",
                            ("qualizeal",))[0]["c"], 0)


class TestGoldenQuestions(unittest.TestCase):
    def test_golden_file_has_six_curated_questions(self):
        import json
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "eval", "demo_questions.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["tenant"], "qualizeal")
        qs = data["questions"]
        self.assertEqual(len(qs), 6)
        families = {q["family"] for q in qs}
        # definition, comparison, service, company, procedure, out-of-fabric decline
        self.assertIn("definition", families)
        self.assertIn("comparison", families)
        self.assertIn("out-of-fabric", families)
        for q in qs:
            for k in ("id", "question", "family", "expected_level"):
                self.assertIn(k, q)


if __name__ == "__main__":
    unittest.main()
