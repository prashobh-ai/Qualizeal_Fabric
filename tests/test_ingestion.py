"""Ingestion & connectors checklist (Section 20) + Runbook 8 resilience."""
import time
import unittest

from knowledge_fabric.app import Platform
from knowledge_fabric.contracts.types import Job, CoordinateKind, new_id
from knowledge_fabric.ingestion.intake import Intake, IngestWorker
from tests.util import seeded


class TestIngestion(unittest.TestCase):
    def setUp(self):
        self.p = Platform(db_path=":memory:", blob_root="./data/test-blobs")
        self.intake = Intake(self.p)
        self.worker = IngestWorker(self.p, self.intake)

    def _submit(self, body, uri="file://a.md", mime="text/markdown", acl=None):
        raw = self.intake.canonical("t1", "files", uri, "A", body.encode(), mime=mime, acl=acl)
        return self.intake.submit(raw)

    def test_seven_step_pipeline_produces_passages(self):
        self._submit("# Title\n\nA requirement is verified by a test case.\n\nCoverage is 95 percent.")
        self.worker.drain()
        self.assertGreater(self.p.passages.count("t1"), 0)

    def test_idempotent_by_hash(self):
        self._submit("same content here about coverage")
        self.worker.drain()
        n1 = self.p.passages.count("t1")
        self._submit("same content here about coverage")   # identical -> no-op
        res = self.worker.drain()
        self.assertEqual(res[0]["status"], "noop")
        self.assertEqual(self.p.passages.count("t1"), n1)

    def test_incremental_supersede_on_change(self):
        self._submit("version one content about defects")
        self.worker.drain()
        self._submit("version two content about defects and regression")
        res = self.worker.drain()
        self.assertEqual(res[0]["status"], "updated")
        self.assertEqual(res[0]["version"], 2)

    def test_all_modalities_have_resolvable_coordinates(self):
        self._submit("a,b\n1,2\n", uri="file://t.csv", mime="text/csv")
        self._submit("[00:05] hello world timestamped", uri="file://t.transcript", mime="audio/transcript")
        self._submit("def foo():\n    return 1\n", uri="file://c.py", mime="text/x-python")
        self._submit("Scanned paragraph one.\n\nScanned paragraph two.", uri="file://s.ocr.txt")
        self.worker.drain()
        kinds = {p.coordinate.kind for p in self.p.passages.for_tenant("t1")}
        self.assertIn(CoordinateKind.CELL, kinds)
        self.assertIn(CoordinateKind.TIMESTAMP, kinds)
        self.assertIn(CoordinateKind.SYMBOL_LINE, kinds)
        self.assertIn(CoordinateKind.BBOX, kinds)
        for p in self.p.passages.for_tenant("t1"):
            self.assertTrue(p.coordinate.locator, "every coordinate must resolve (I2)")

    def test_three_intake_doors_same_canonical_record(self):
        drop = self.intake.canonical("t1", "files", "file://d.md", "D", b"drop folder doc coverage")
        up = self.intake.canonical("t1", "upload", "upload://u.md", "U", b"upload doc coverage")
        cli = self.intake.canonical("t1", "cli", "cli://c.md", "C", b"cli doc coverage")
        for r in (drop, up, cli):
            self.assertEqual(r.tenant, "t1")
            self.assertIn("acl", r.meta)

    def test_tombstone_on_source_deletion(self):
        self._submit("doc to be deleted about coverage")
        self.worker.drain()
        doc = self.p.documents.list("t1")[0]
        ids = self.p.passages.delete_document_passages("t1", doc["id"])
        self.p.vindex.delete("t1", ids)
        self.p.documents.tombstone("t1", doc["id"])
        self.assertEqual(self.p.passages.by_document("t1", doc["id"]), [])
        self.assertEqual(self.p.documents.list("t1"), [])

    def test_queue_durable_retry_then_deadletter(self):
        job = Job(id=new_id("job_"), tenant="t1", kind="ingest", payload={})
        self.p.queue.enqueue("t1", job)
        for _ in range(6):
            leased = self.p.queue.lease("w")
            if leased:
                self.p.queue.nack(leased.id, "boom")
        row = self.p.db.one("SELECT state,dead_letter_reason FROM jobs WHERE id=?", (job.id,))
        self.assertEqual(row["state"], "dead")
        self.assertEqual(self.p.queue.depth(), 0)

    def test_worker_kill_lease_expiry_reclaims_job(self):
        job = Job(id=new_id("job_"), tenant="t1", kind="ingest", payload={})
        self.p.queue.enqueue("t1", job)
        leased = self.p.queue.lease("w1", ttl_s=0.05)     # worker dies without ack
        self.assertIsNotNone(leased)
        time.sleep(0.08)
        reclaimed = self.p.queue.lease("w2", ttl_s=5)      # another worker picks it up
        self.assertEqual(reclaimed.id, job.id)

    def test_duplicate_storm_processes_once(self):
        for _ in range(20):
            self._submit("identical duplicate document about coverage")
        res = self.worker.drain()
        oks = [r for r in res if r["status"] == "ok"]
        noops = [r for r in res if r["status"] == "noop"]
        self.assertEqual(len(oks), 1)
        self.assertEqual(len(noops), 19)


if __name__ == "__main__":
    unittest.main()
