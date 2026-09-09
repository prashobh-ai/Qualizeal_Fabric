"""Stage-2 Section D: knowledge-base evaluation -> curator suggestions + data quality."""
import unittest

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.health import kb_eval
from knowledge_fabric.ingestion.intake import Intake, IngestWorker
from knowledge_fabric.tenants import demo
from tests.util import seeded

T = "q-quality"
OTHER = "q-airlines"
DAY_MS = 86_400_000


class KbEvalBase(unittest.TestCase):
    """Seed two tenants, ask three questions in acme, keep handles to the docs."""

    def setUp(self):
        self.p = seeded([T, OTHER])
        self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, T, "asker.public")
        self.answers = [self.svc.ask(self.asker, q) for q, _, _ in demo.QUESTION_BANK[T][:3]]
        self.docs = {d["uri"]: d for d in self.p.documents.list(T)}
        self.strategy = self.docs["file://qa/test-strategy.md"]
        self.restricted = self.docs["file://qa/defect-policy.md"]   # asker cannot see it -> uncited

    def _upload_duplicate(self, filename="test-strategy-copy.md", acl=None) -> str:
        """Upload the Test Strategy body again through the upload door; returns the new doc id."""
        body = next(b for uri, _, _, _, b in demo.CORPORA[T] if uri == "qa/test-strategy.md")
        intake = Intake(self.p)
        intake.upload(T, filename, body.encode(), acl=acl)
        results = IngestWorker(self.p, intake).drain()
        self.assertEqual(results[-1]["status"], "ok", results)
        return results[-1]["document_id"]

    def _set_authoritative(self, doc_id: str, flag: bool = True) -> None:
        self.p.db.execute("UPDATE documents SET authoritative=? WHERE tenant=? AND id=?",
                          (1 if flag else 0, T, doc_id))

    def _quality_of(self, doc_id: str, **kw) -> dict:
        return next(d for d in kb_eval.document_quality(self.p, T, **kw) if d["document_id"] == doc_id)


class TestCitationUsage(KbEvalBase):
    def test_usage_maps_titles_and_passages_to_document_ids(self):
        self.assertTrue(all(a.citations for a in self.answers), "questions must be answered with citations")
        usage = kb_eval.citation_usage(self.p, T)
        doc_ids = {d["id"] for d in self.p.documents.list(T)}
        self.assertTrue(usage, "three cited answers must produce usage")
        self.assertTrue(set(usage) <= doc_ids, "every key resolves to a tenant document")
        self.assertTrue(all(v >= 1 for v in usage.values()))
        # every cited title resolves to a counted document
        for a in self.answers:
            for c in a.citations:
                self.assertIn(c.document_id, usage, c.document_title)
        # a document is counted at most once per trace
        self.assertLessEqual(max(usage.values()), len(self.answers))
        # the restricted policy was never visible to the asker, so never cited
        self.assertNotIn(self.restricted["id"], usage)

    def test_usage_is_tenant_scoped(self):
        acme_ids = {d["id"] for d in self.p.documents.list(T)}
        other = kb_eval.citation_usage(self.p, OTHER)
        self.assertFalse(set(other) & acme_ids)
        with self.assertRaises(PermissionError):
            kb_eval.citation_usage(self.p, "")


class TestDuplicates(KbEvalBase):
    def test_no_cross_document_duplicates_in_clean_corpus(self):
        self.assertEqual(kb_eval.duplicates(self.p, T), [])

    def test_uploaded_copy_is_detected_cross_document_only(self):
        copy_id = self._upload_duplicate()
        dups = kb_eval.duplicates(self.p, T)
        self.assertTrue(dups)
        for d in dups:
            self.assertEqual(set(d), {"passage_id", "dup_of", "document_id", "dup_document_id", "cosine"})
            self.assertNotEqual(d["document_id"], d["dup_document_id"], "cross-document only")
            self.assertGreaterEqual(d["cosine"], 0.92)
            self.assertLessEqual(d["cosine"], 1.0)
        # the newer copy is the duplicate, the original (older) document is dup_of
        self.assertEqual({d["document_id"] for d in dups}, {copy_id})
        self.assertEqual({d["dup_document_id"] for d in dups}, {self.strategy["id"]})
        n_copy_passages = len([p for p in self.p.passages.by_document(T, copy_id) if p.superseded_by is None])
        self.assertEqual(len({d["passage_id"] for d in dups}), n_copy_passages)
        # deterministic ordering and a stricter threshold still finds exact copies
        self.assertEqual(dups, kb_eval.duplicates(self.p, T))
        self.assertEqual(len(kb_eval.duplicates(self.p, T, threshold=0.999)), len(dups))

    def test_orientation_is_by_ingestion_age_not_authority(self):
        copy_id = self._upload_duplicate()
        self._set_authoritative(copy_id, True)
        dups = kb_eval.duplicates(self.p, T)
        self.assertEqual({d["document_id"] for d in dups}, {copy_id}, "newer copy stays the duplicate")
        self.assertEqual({d["dup_document_id"] for d in dups}, {self.strategy["id"]})

    def test_duplicates_are_tenant_scoped(self):
        self._upload_duplicate()
        self.assertEqual(kb_eval.duplicates(self.p, OTHER), [])
        with self.assertRaises(PermissionError):
            kb_eval.duplicates(self.p, "")


class TestReadability(unittest.TestCase):
    def test_bounds_and_edge_cases(self):
        self.assertEqual(kb_eval.readability(""), 0.0)
        self.assertEqual(kb_eval.readability("   \n "), 0.0)
        self.assertEqual(kb_eval.readability("!!! ... ???"), 0.0)
        for txt in ("The cat sat.", "a " * 500, "x" * 300):
            r = kb_eval.readability(txt)
            self.assertGreaterEqual(r, 0.0)
            self.assertLessEqual(r, 1.0)

    def test_simple_text_scores_higher_than_dense_jargon(self):
        simple = "The test passed. The build is green. We can ship today."
        dense = ("Notwithstanding aforementioned considerations regarding interdepartmental "
                 "accountability frameworks, organisational stakeholders systematically "
                 "underestimated implementation complexities associated with heterogeneous "
                 "infrastructural modernisation initiatives spanning multiple jurisdictions "
                 "whilst simultaneously renegotiating contractual obligations")
        self.assertEqual(kb_eval.readability(simple), 1.0)
        self.assertLess(kb_eval.readability(dense), kb_eval.readability(simple))
        self.assertLess(kb_eval.readability(dense), kb_eval.READABILITY_REVIEW + 0.2)

    def test_deterministic(self):
        txt = "Critical defects must be triaged within four business hours."
        self.assertEqual(kb_eval.readability(txt), kb_eval.readability(txt))


class TestDocumentQuality(KbEvalBase):
    REQUIRED = {"document_id", "title", "source", "uri", "ingested_at", "passages", "authoritative",
                "signals", "score", "suggestion", "reasons"}
    SIGNALS = {"citation_uses", "age_days", "duplicate_passages", "contradiction_flags", "readability",
               "coverage_contribution", "orphan_ratio", "gap_hits"}

    def test_shape_and_ranges(self):
        rows = kb_eval.document_quality(self.p, T)
        self.assertEqual(len(rows), len(self.p.documents.list(T)))
        for r in rows:
            self.assertEqual(set(r), self.REQUIRED)
            self.assertEqual(set(r["signals"]), self.SIGNALS)
            self.assertIn(r["suggestion"], ("keep", "review", "delete"))
            self.assertTrue(r["reasons"] and all(isinstance(x, str) for x in r["reasons"]))
            self.assertGreaterEqual(r["score"], 0.0)
            self.assertLessEqual(r["score"], 1.0)
            for k in ("readability", "coverage_contribution", "orphan_ratio"):
                self.assertGreaterEqual(r["signals"][k], 0.0)
                self.assertLessEqual(r["signals"][k], 1.0)
        # worst first: delete < review < keep, then ascending score
        order = [kb_eval._SUGGESTION_RANK[r["suggestion"]] for r in rows]
        self.assertEqual(order, sorted(order))

    def test_cited_document_kept_uncited_reviewed(self):
        strat = self._quality_of(self.strategy["id"])
        self.assertGreaterEqual(strat["signals"]["citation_uses"], 1)
        self.assertEqual(strat["suggestion"], "keep")
        restricted = self._quality_of(self.restricted["id"])
        self.assertEqual(restricted["signals"]["citation_uses"], 0)
        self.assertEqual(restricted["suggestion"], "review")
        self.assertTrue(any("never cited" in r for r in restricted["reasons"]))

    def test_duplicate_upload_is_suggested_delete_with_reason(self):
        copy_id = self._upload_duplicate()
        copy = self._quality_of(copy_id)
        self.assertEqual(copy["source"], "upload")
        self.assertEqual(copy["signals"]["duplicate_passages"], copy["passages"])
        self.assertEqual(copy["suggestion"], "delete")
        self.assertTrue(any("duplicate" in r.lower() for r in copy["reasons"]), copy["reasons"])
        # the original stays and is not itself considered a duplicate
        original = self._quality_of(self.strategy["id"])
        self.assertEqual(original["signals"]["duplicate_passages"], 0)
        self.assertNotEqual(original["suggestion"], "delete")
        self.assertTrue(any("duplicated by a newer document" in r for r in original["reasons"]))
        # the copy scores below the original
        self.assertLess(copy["score"], original["score"])

    def test_authoritative_duplicate_is_review_never_delete(self):
        copy_id = self._upload_duplicate()
        self._set_authoritative(copy_id, True)
        rows = kb_eval.document_quality(self.p, T)
        copy = next(r for r in rows if r["document_id"] == copy_id)
        self.assertTrue(copy["authoritative"])
        self.assertEqual(copy["suggestion"], "review")
        self.assertTrue(any("duplicate" in r.lower() for r in copy["reasons"]))
        self.assertTrue(any("authoritative" in r.lower() for r in copy["reasons"]))
        for r in rows:
            if r["authoritative"]:
                self.assertNotEqual(r["suggestion"], "delete")

    def test_stale_uncited_is_delete_unless_authoritative(self):
        now = self.restricted["ingested_at"] + 400 * DAY_MS
        row = self._quality_of(self.restricted["id"], now_ms=now)
        self.assertGreater(row["signals"]["age_days"], 365)
        self.assertEqual(row["suggestion"], "delete")
        self.assertTrue(any("stale" in r for r in row["reasons"]))
        # a cited document of the same age is only "keep"/"review", never deleted for age
        strat = self._quality_of(self.strategy["id"], now_ms=now)
        self.assertNotEqual(strat["suggestion"], "delete")
        self._set_authoritative(self.restricted["id"], True)
        row = self._quality_of(self.restricted["id"], now_ms=now)
        self.assertEqual(row["suggestion"], "review")
        self.assertTrue(any("authoritative" in r for r in row["reasons"]))

    def test_now_ms_is_reproducible_and_never_negative(self):
        fixed = self.strategy["ingested_at"] - 10 * DAY_MS     # clock earlier than ingest
        row = self._quality_of(self.strategy["id"], now_ms=fixed)
        self.assertEqual(row["signals"]["age_days"], 0.0)
        a = kb_eval.document_quality(self.p, T, now_ms=fixed)
        b = kb_eval.document_quality(self.p, T, now_ms=fixed)
        self.assertEqual(a, b)

    def test_gap_hits_attributed_by_subject(self):
        self.p.curation.add(T, "what does the test strategy say about impact analysis?", "gap", 1)
        row = self._quality_of(self.strategy["id"])
        self.assertGreaterEqual(row["signals"]["gap_hits"], 1)
        self.assertTrue(any("unanswered" in r for r in row["reasons"]))

    def test_tenant_scoped(self):
        acme_ids = {d["id"] for d in self.p.documents.list(T)}
        other = kb_eval.document_quality(self.p, OTHER)
        self.assertTrue(other)
        self.assertFalse({r["document_id"] for r in other} & acme_ids)
        with self.assertRaises(PermissionError):
            kb_eval.document_quality(self.p, "")


class TestDataQuality(KbEvalBase):
    KEYS = {"coverage", "freshness", "contradictions", "gaps", "connectedness", "traceability",
            "readability_avg", "duplicate_rate", "citation_coverage", "documents", "passages",
            "suggestions", "risk_register"}

    def test_shape_and_consistency(self):
        dq = kb_eval.data_quality(self.p, T)
        self.assertTrue(self.KEYS <= set(dq))
        self.assertEqual(dq["documents"], len(self.p.documents.list(T)))
        self.assertEqual(dq["passages"], self.p.passages.count(T))
        self.assertEqual(sum(dq["suggestions"].values()), dq["documents"])
        self.assertEqual(set(dq["suggestions"]), {"keep", "review", "delete"})
        for k in ("coverage", "freshness", "connectedness", "traceability", "readability_avg",
                  "duplicate_rate", "citation_coverage"):
            self.assertGreaterEqual(dq[k], 0.0)
            self.assertLessEqual(dq[k], 1.0)
        self.assertEqual(dq["duplicate_rate"], 0.0)
        self.assertGreater(dq["citation_coverage"], 0.0)
        self.assertIsInstance(dq["risk_register"], list)
        for risk in dq["risk_register"]:
            self.assertEqual(set(risk), {"risk", "value", "severity"})

    def test_reuses_health_snapshot_and_flags_duplicates(self):
        from knowledge_fabric.health import metrics
        self._upload_duplicate()
        snap = metrics.latest(self.p, T)
        base_risks = metrics.risk_register(self.p, T)
        dq = kb_eval.data_quality(self.p, T)
        self.assertAlmostEqual(dq["coverage"], round(snap["coverage"], 4))
        self.assertEqual(dq["contradictions"], snap["contradictions"])
        self.assertGreater(dq["duplicate_rate"], 0.0)
        self.assertEqual(dq["suggestions"]["delete"], 1)
        names = [r["risk"] for r in dq["risk_register"]]
        for r in base_risks:
            self.assertIn(r["risk"], names)
        self.assertIn("duplicate content", names)
        self.assertIn("documents suggested for deletion", names)

    def test_empty_tenant_is_safe(self):
        dq = kb_eval.data_quality(self.p, "ghost-tenant")
        self.assertEqual((dq["documents"], dq["passages"]), (0, 0))
        self.assertEqual(dq["suggestions"], {"keep": 0, "review": 0, "delete": 0})
        self.assertEqual(dq["citation_coverage"], 0.0)


if __name__ == "__main__":
    unittest.main()
