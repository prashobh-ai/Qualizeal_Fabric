"""L0.2 — the product fabric ships no documents; synthetic corpus is test-only."""

from __future__ import annotations

import os
import unittest

from knowledge_fabric.app import Platform
from knowledge_fabric.tenants import demo
from tests.fixtures import synthetic_corpus


class TestSeedIsDocumentFree(unittest.TestCase):
    def setUp(self):
        os.environ["KF_MODEL_MODE"] = "mock"
        self.p = Platform(db_path=":memory:", blob_root="./data/test-blobs")

    def test_seed_sets_budget_but_no_documents(self):
        summary = demo.seed(self.p)
        self.assertEqual(self.p.documents.list("qualizeal"), [])
        self.assertEqual(self.p.passages.count("qualizeal"), 0)
        self.assertEqual(summary["qualizeal"]["documents"], 0)
        # budget is configured so the answer path's caps work once docs load
        self.assertGreater(self.p.policy.budget_remaining("qualizeal"), 0.0)

    def test_question_bank_empty_until_corpus_loads(self):
        demo.seed(self.p)
        rows = self.p.db.query(
            "SELECT COUNT(*) c FROM question_bank WHERE tenant=?", ("qualizeal",)
        )
        self.assertEqual(rows[0]["c"], 0)

    def test_fixture_loads_into_test_fabric_only(self):
        demo.seed(self.p)
        summary = synthetic_corpus.load_into(self.p, synthetic_corpus.TEST_FABRIC)
        self.assertEqual(summary["documents"], len(synthetic_corpus.CORPORA))
        # the corpus went to the test fabric, never the product fabric
        self.assertGreater(len(self.p.documents.list(synthetic_corpus.TEST_FABRIC)), 0)
        self.assertEqual(self.p.documents.list("qualizeal"), [])

    def test_fixture_corpus_is_identifier_safe(self):
        self.assertEqual(synthetic_corpus.validate_identifiers(), [])

    def test_product_validate_identifiers_is_empty(self):
        # No shipped corpus → nothing to validate.
        self.assertEqual(demo.validate_identifiers(), [])


if __name__ == "__main__":
    unittest.main()
