"""T53 — two curation modes: manual review gate vs automated auto-keep."""

import unittest

from knowledge_fabric import curation
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from tests.util import seeded

T = "test-fabric"
OTHER = "isolation-check"

BODY = (
    "The regression suite runs nightly against the staging environment. "
    "A failure opens a ticket and pages the on-call engineer. "
    "Each run publishes a coverage report to the release dashboard."
)


class Base(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T, OTHER])
        self.intake = Intake(self.p)

    def _ingest(self, source: str, uri: str, title: str, body: str = BODY) -> dict:
        """Ingest one fresh document through the pipeline and return its row."""
        raw = self.intake.canonical(T, source, uri, title, body.encode())
        self.intake.submit(raw)
        results = IngestWorker(self.p, self.intake).drain()
        res = results[-1]
        self.assertIn(res["status"], ("ok", "updated"), res)
        return self.p.documents.get(T, res["document_id"])

    def _live_ids(self) -> set[str]:
        return {d["id"] for d in curation.live_filter(self.p.documents.list(T))}

    def _queued(self) -> set[str]:
        return {q["review_id"] for q in curation.review_queue(self.p, T)}


class TestModeResolution(Base):
    def test_source_override_then_default_then_automated(self):
        self.assertEqual(curation.get_mode(self.p, T, "notes"), "automated")  # nothing set
        curation.set_default_mode(self.p, T, "manual")
        self.assertEqual(curation.get_mode(self.p, T, "notes"), "manual")  # tenant default
        curation.set_mode(self.p, T, "notes", "automated")
        self.assertEqual(curation.get_mode(self.p, T, "notes"), "automated")  # source override
        self.assertEqual(curation.get_mode(self.p, T, "other"), "manual")  # default still applies

    def test_validation_and_tenant_guard(self):
        with self.assertRaises(ValueError):
            curation.set_mode(self.p, T, "notes", "bogus")
        with self.assertRaises(PermissionError):
            curation.get_mode(self.p, "", "notes")
        with self.assertRaises(PermissionError):
            curation.set_mode(self.p, "", "notes", "manual")
        # modes are tenant-scoped
        curation.set_default_mode(self.p, T, "manual")
        self.assertEqual(curation.get_mode(self.p, OTHER, "notes"), "automated")


class TestManualMode(Base):
    def test_manual_ingest_waits_in_review_and_is_not_answerable(self):
        curation.set_mode(self.p, T, "notes", "manual")
        doc = self._ingest("notes", "notes://q1/plan", "Quarter Plan")
        res = curation.on_ingest(self.p, T, doc, "notes")

        self.assertEqual(res["state"], "review")
        self.assertIsNotNone(res["log_id"])
        rid = res["review_id"]

        # held back from answers: not live, absent from the live set
        held = self.p.documents.get(T, doc["id"])
        self.assertFalse(curation.is_live(held))
        self.assertNotIn(doc["id"], self._live_ids())
        # but visible to a curator in the review queue
        self.assertIn(rid, self._queued())

    def test_accept_makes_a_review_item_live(self):
        curation.set_mode(self.p, T, "notes", "manual")
        doc = self._ingest("notes", "notes://q1/plan", "Quarter Plan")
        rid = curation.on_ingest(self.p, T, doc, "notes")["review_id"]
        self.assertNotIn(doc["id"], self._live_ids())

        curation.accept(self.p, T, rid, actor="curator")

        live = self.p.documents.get(T, doc["id"])
        self.assertTrue(curation.is_live(live))
        self.assertIn(doc["id"], self._live_ids())
        self.assertNotIn(rid, self._queued())  # resolved, so no longer queued

    def test_review_item_never_appears_in_the_live_set(self):
        curation.set_mode(self.p, T, "notes", "manual")
        base_live = self._live_ids()
        doc = self._ingest("notes", "notes://q1/plan", "Quarter Plan")
        curation.on_ingest(self.p, T, doc, "notes")
        # the newly ingested review item is not answerable to anyone
        self.assertEqual(self._live_ids(), base_live)
        self.assertNotIn(doc["id"], self._live_ids())

    def test_rejected_review_id_stays_resolved_on_reingest(self):
        curation.set_mode(self.p, T, "notes", "manual")
        doc = self._ingest("notes", "notes://q3/plan", "Third Plan")
        rid = curation.on_ingest(self.p, T, doc, "notes")["review_id"]
        curation.reject(self.p, T, rid, actor="curator", reason="out of scope")
        self.assertNotIn(rid, self._queued())

        # the same logical document arrives again (same type + title -> same id)
        doc2 = self._ingest("notes", "notes://q3/plan-again", "Third Plan")
        self.assertEqual(curation.review_id(doc2), rid)
        res2 = curation.on_ingest(self.p, T, doc2, "notes")

        self.assertIsNone(res2["log_id"], "a resolved review id is not re-logged")
        self.assertEqual(res2["state"], "rejected")
        self.assertNotIn(rid, self._queued())  # does not reopen the queue
        self.assertFalse(curation.is_live(self.p.documents.get(T, doc2["id"])))
        # exactly one 'ingested' event was ever recorded for this review id
        rows = curation.timeline(self.p, T)["rows"]
        ingested = [r for r in rows if r["review_id"] == rid and r["action"] == "ingested"]
        self.assertEqual(len(ingested), 1)


class TestAutomatedMode(Base):
    def test_automated_ingest_stays_live_and_logs_auto_kept(self):
        curation.set_mode(self.p, T, "notes", "automated")
        doc = self._ingest("notes", "notes://q2/plan", "Second Plan")
        res = curation.on_ingest(self.p, T, doc, "notes")

        self.assertEqual(res["state"], "live")
        self.assertTrue(curation.is_live(self.p.documents.get(T, doc["id"])))
        self.assertIn(doc["id"], self._live_ids())
        self.assertNotIn(res["review_id"], self._queued())  # never queued

        actions = {r["action"] for r in curation.timeline(self.p, T)["rows"]}
        self.assertIn("auto_kept", actions)

    def test_default_mode_is_automated(self):
        # no mode set anywhere -> automated: the item is live immediately
        doc = self._ingest("notes", "notes://q4/plan", "Fourth Plan")
        res = curation.on_ingest(self.p, T, doc, "notes")
        self.assertEqual(res["state"], "live")
        self.assertIn(doc["id"], self._live_ids())


class TestScoreAndRecommendation(Base):
    def test_score_shape_and_recommendation(self):
        doc = self._ingest("notes", "notes://q5/plan", "Fifth Plan")
        sc = curation.score(self.p, T, doc)
        self.assertEqual(
            set(sc),
            {
                "depth",
                "connectedness",
                "traceability",
                "readability",
                "currency",
                "duplicate_pct",
                "contradiction",
                "overall",
            },
        )
        for k in ("depth", "connectedness", "readability", "currency", "duplicate_pct", "overall"):
            self.assertGreaterEqual(sc[k], 0.0)
            self.assertLessEqual(sc[k], 1.0)
        rec, reasons = curation.recommendation(sc)
        self.assertIn(rec, ("Keep", "Delete"))
        self.assertTrue(reasons and all(isinstance(r, str) for r in reasons))
        # deterministic
        self.assertEqual(sc, curation.score(self.p, T, doc))

    def test_mostly_duplicate_is_delete(self):
        rec, reasons = curation.recommendation(
            {
                "depth": 0.5,
                "connectedness": 0.5,
                "traceability": 1.0,
                "readability": 0.8,
                "currency": 0.9,
                "duplicate_pct": 0.8,
                "contradiction": 0,
                "overall": 0.6,
            }
        )
        self.assertEqual(rec, "Delete")
        self.assertTrue(any("duplicate" in r.lower() for r in reasons))


if __name__ == "__main__":
    unittest.main()
