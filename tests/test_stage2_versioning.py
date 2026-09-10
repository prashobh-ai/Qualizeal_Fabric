"""Stage-2 Section B: document history / diff / rollback, dataset versions, lineage."""

import unittest

from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.stores import versioning as ver
from tests.util import seeded

T = "test-fabric"
V1 = (
    "# Release Gate\n\nA release needs zero open critical defects before promotion.\n\n"
    "Coverage of priority-1 requirements must reach 95 percent.\n"
)
V2 = (
    "# Release Gate\n\nA release needs zero open critical defects before promotion.\n\n"
    "Coverage of priority-1 requirements must reach 97 percent.\n\n"
    "Security scan findings above medium block the release.\n"
)


class VersioningBase(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T])
        self.intake = Intake(self.p)
        self.worker = IngestWorker(self.p, self.intake)

    def _ingest(self, body: str, source_version: str) -> dict:
        raw = self.intake.canonical(
            T,
            "files",
            "file://qa/release-gate.md",
            "Release Gate",
            body.encode(),
            mime="text/markdown",
            source_version=source_version,
        )
        self.intake.submit(raw)
        res = self.worker.drain()
        self.assertEqual(len(res), 1)
        return res[0]

    def _record(self, res: dict) -> None:
        """What the pipeline hook does after chunking: record the version in the ledger."""
        doc = self.p.documents.get(T, res["document_id"])
        live = [p for p in self.p.passages.by_document(T, doc["id"]) if p.version == res["version"]]
        ver.record_version(
            self.p,
            T,
            doc["id"],
            res["version"],
            doc["content_hash"],
            [p.id for p in live],
            doc["source_version"],
        )

    def _two_versions(self):
        r1 = self._ingest(V1, "1")
        self.assertEqual(r1["status"], "ok")
        self._record(r1)
        r2 = self._ingest(V2, "2")
        self.assertEqual(r2["status"], "updated")
        self.assertEqual(r2["version"], 2)
        self._record(r2)
        return r1["document_id"]

    def _live(self, doc_id):
        return [p for p in self.p.passages.by_document(T, doc_id) if p.superseded_by is None]


class TestHistoryAndDiff(VersioningBase):
    def test_history_lists_both_versions_oldest_first(self):
        doc_id = self._two_versions()
        h = ver.history(self.p, T, doc_id)
        self.assertEqual([x["version"] for x in h], [1, 2])
        self.assertEqual([x["source_version"] for x in h], ["1", "2"])
        self.assertNotEqual(h[0]["content_hash"], h[1]["content_hash"])
        for x in h:
            self.assertGreater(x["passages"], 0)
            self.assertIsNotNone(x["created_at"])
            self.assertTrue(x["recorded"])

    def test_record_version_is_idempotent(self):
        doc_id = self._two_versions()
        h = ver.history(self.p, T, doc_id)
        ver.record_version(self.p, T, doc_id, 2, h[1]["content_hash"], ["x"], "2")
        h2 = ver.history(self.p, T, doc_id)
        self.assertEqual(len(h2), 2)
        self.assertEqual(h2[1]["passages"], 1)  # replaced, not duplicated

    def test_diff_reports_added_removed_unchanged(self):
        doc_id = self._two_versions()
        d = ver.diff(self.p, T, doc_id, 1, 2)
        self.assertGreater(d["unchanged"], 0)
        self.assertTrue(any("97 percent" in t for t in d["added"]))
        self.assertTrue(any("Security scan" in t for t in d["added"]))
        self.assertTrue(any("95 percent" in t for t in d["removed"]))
        self.assertFalse(any("Security scan" in t for t in d["removed"]))
        # reverse direction mirrors
        r = ver.diff(self.p, T, doc_id, 2, 1)
        self.assertEqual(sorted(r["added"]), sorted(d["removed"]))
        self.assertEqual(sorted(r["removed"]), sorted(d["added"]))
        self.assertEqual(r["unchanged"], d["unchanged"])

    def test_diff_same_version_is_empty(self):
        doc_id = self._two_versions()
        d = ver.diff(self.p, T, doc_id, 2, 2)
        self.assertEqual(d["added"], [])
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["unchanged"], len(self._live(doc_id)))

    def test_diff_unknown_version_raises(self):
        doc_id = self._two_versions()
        with self.assertRaises(KeyError):
            ver.diff(self.p, T, doc_id, 1, 9)

    def test_backfill_reconstructs_ledger_for_pre_hook_documents(self):
        r1 = self._ingest(V1, "1")
        r2 = self._ingest(V2, "2")
        doc_id = r2["document_id"]
        # simulate documents ingested BEFORE the ledger hook existed: drop their rows
        self.p.db.execute(
            "DELETE FROM document_versions WHERE tenant=? AND document_id=?", (T, doc_id)
        )
        h = ver.history(self.p, T, doc_id)
        self.assertEqual([x["version"] for x in h], [1, 2])
        self.assertFalse(h[0]["recorded"])
        self.assertEqual(ver.backfill(self.p, T, doc_id), 2)
        self.assertEqual(ver.backfill(self.p, T, doc_id), 0)
        h = ver.history(self.p, T, doc_id)
        self.assertTrue(all(x["recorded"] for x in h))
        self.assertEqual(
            h[0]["content_hash"], self.p.passages.by_document(T, doc_id)[0].provenance.content_hash
        )
        self.assertEqual(r1["document_id"], doc_id)

    def test_history_is_tenant_scoped(self):
        doc_id = self._two_versions()
        self.assertEqual(ver.history(self.p, "isolation-check", doc_id), [])
        with self.assertRaises(PermissionError):
            ver.history(self.p, "", doc_id)


class TestRollback(VersioningBase):
    def test_rollback_restores_v1_and_keeps_store_consistent(self):
        doc_id = self._two_versions()
        v1_ids = {p.id for p in self.p.passages.by_document(T, doc_id) if p.version == 1}
        before = self.p.passages.count(T)
        audits_before = len(self.p.audit.for_tenant(T, 500))

        out = ver.rollback(self.p, T, doc_id, 1, by_subject="curator")
        self.assertEqual(out["new_version"], 3)
        self.assertEqual(out["reactivated"], len(v1_ids))
        self.assertEqual(out["restored_version"], 1)

        # exactly one live version for the document, and it is v1's passages
        live = self._live(doc_id)
        self.assertEqual({p.id for p in live}, v1_ids)
        self.assertEqual({p.version for p in live}, {1})
        superseded = [
            p for p in self.p.passages.by_document(T, doc_id) if p.superseded_by is not None
        ]
        self.assertTrue(all(p.superseded_by == "v3" for p in superseded if p.version == 2))

        # document row points at restored content
        doc = self.p.documents.get(T, doc_id)
        self.assertEqual(doc["current_version"], 3)
        self.assertEqual(doc["source_version"], "1")
        self.assertEqual(doc["content_hash"], ver.history(self.p, T, doc_id)[0]["content_hash"])

        # ledger row for the restore reuses v1's passage set
        h = ver.history(self.p, T, doc_id)
        self.assertEqual([x["version"] for x in h], [1, 2, 3])
        self.assertEqual(h[2]["content_hash"], h[0]["content_hash"])
        self.assertEqual(
            ver.diff(self.p, T, doc_id, 1, 3),
            {**ver.diff(self.p, T, doc_id, 1, 3), "added": [], "removed": []},
        )

        # tenant-wide live count: v2's passages left, v1's came back
        n_v2 = len([p for p in superseded if p.version == 2])
        self.assertEqual(self.p.passages.count(T), before - n_v2 + len(v1_ids))
        # retrieval only sees the restored passages
        hits = self.p.lindex.search(T, "security scan findings block release", 20, ["public"])
        self.assertFalse(any(pid in {p.id for p in superseded} for pid, _ in hits))
        self.assertTrue(
            any(
                pid in v1_ids
                for pid, _ in self.p.lindex.search(
                    T, "coverage 95 percent priority", 20, ["public"]
                )
            )
        )

        # audited
        entries = self.p.audit.for_tenant(T, 500)
        self.assertEqual(len(entries), audits_before + 1)
        top = entries[0]
        self.assertEqual(top["action"], "version.rollback")
        self.assertEqual(top["subject"], "curator")
        self.assertIn(doc_id, top["resource"])

    def test_rollback_reingest_of_restored_content_is_noop(self):
        doc_id = self._two_versions()
        ver.rollback(self.p, T, doc_id, 1, by_subject="curator")
        res = self._ingest(V1, "1")
        self.assertEqual(res["status"], "noop")  # idempotent-by-hash after restore
        res = self._ingest(V2, "3")
        self.assertEqual(res["status"], "updated")
        self.assertEqual(res["version"], 4)
        live = self._live(doc_id)
        self.assertEqual({p.version for p in live}, {4})

    def test_rollback_forward_again(self):
        doc_id = self._two_versions()
        ver.rollback(self.p, T, doc_id, 1, by_subject="curator")
        out = ver.rollback(self.p, T, doc_id, 2, by_subject="curator")
        self.assertEqual(out["new_version"], 4)
        live = self._live(doc_id)
        self.assertEqual({p.version for p in live}, {2})
        self.assertEqual(len(ver.history(self.p, T, doc_id)), 4)

    def test_rollback_works_without_prior_ledger_rows(self):
        self._ingest(V1, "1")
        r2 = self._ingest(V2, "2")
        doc_id = r2["document_id"]
        out = ver.rollback(self.p, T, doc_id, 1, by_subject="curator")
        self.assertEqual(out["new_version"], 3)
        self.assertEqual({p.version for p in self._live(doc_id)}, {1})

    def test_rollback_reembeds_when_vectors_missing(self):
        doc_id = self._two_versions()
        v1_ids = [p.id for p in self.p.passages.by_document(T, doc_id) if p.version == 1]
        self.p.vindex.delete(T, v1_ids)
        ver.rollback(self.p, T, doc_id, 1, by_subject="curator")
        for pid in v1_ids:
            r = self.p.db.one(
                "SELECT COUNT(*) c FROM embeddings WHERE tenant=? AND passage_id=?", (T, pid)
            )
            self.assertEqual(r["c"], 1)

    def test_rollback_errors(self):
        doc_id = self._two_versions()
        with self.assertRaises(KeyError):
            ver.rollback(self.p, T, doc_id, 7, by_subject="curator")
        with self.assertRaises(KeyError):
            ver.rollback(self.p, T, "doc_missing", 1, by_subject="curator")
        with self.assertRaises(KeyError):  # wrong tenant cannot touch it
            ver.rollback(self.p, "isolation-check", doc_id, 1, by_subject="asker.public")
        with self.assertRaises(ValueError):
            ver.rollback(self.p, T, doc_id, 1, by_subject="")
        # nothing changed
        self.assertEqual({p.version for p in self._live(doc_id)}, {2})
        self.assertEqual(self.p.documents.get(T, doc_id)["current_version"], 2)


class TestDatasetAndLineage(VersioningBase):
    def test_dataset_version_bumps_and_counts(self):
        v0 = ver.current_dataset(self.p, T)  # seeding already versioned the corpus
        v1 = ver.bump_dataset(self.p, T, "initial seed")
        self.assertEqual(v1, v0 + 1)
        self._two_versions()  # each changed batch bumps the dataset
        v_mid = ver.current_dataset(self.p, T)
        v2 = ver.bump_dataset(self.p, T, "release-gate updated")
        self.assertEqual(v2, v_mid + 1)
        self.assertEqual(ver.current_dataset(self.p, T), v2)
        rows = ver.list_dataset_versions(self.p, T)
        self.assertEqual(rows[0]["version"], v2)
        self.assertEqual([r["version"] for r in rows][:2], [v2, v2 - 1])
        self.assertEqual(rows[0]["reason"], "release-gate updated")
        self.assertEqual(rows[0]["doc_count"], len(self.p.documents.list(T)))
        self.assertEqual(rows[0]["passage_count"], self.p.passages.count(T))
        self.assertEqual(rows[0]["doc_count"], len(self.p.documents.list(T)))
        # other tenant is untouched
        self.assertEqual(ver.current_dataset(self.p, "isolation-check"), 0)

    def test_lineage_resolves_passage_to_origin(self):
        doc_id = self._two_versions()
        live = self._live(doc_id)[0]
        lin = ver.lineage(self.p, T, live.id)
        self.assertEqual(lin["passage_id"], live.id)
        self.assertEqual(lin["document_id"], doc_id)
        self.assertEqual(lin["document_title"], "Release Gate")
        self.assertEqual(lin["version"], 2)
        self.assertEqual(lin["source"], "files")
        self.assertEqual(lin["source_version"], "2")
        self.assertEqual(lin["content_hash"], self.p.documents.get(T, doc_id)["content_hash"])
        self.assertTrue(lin["live"])
        self.assertEqual(lin["coordinate"]["kind"], live.coordinate.kind.value)
        self.assertEqual(lin["coordinate"]["render"], live.coordinate.render())
        old = [p for p in self.p.passages.by_document(T, doc_id) if p.version == 1][0]
        lin_old = ver.lineage(self.p, T, old.id)
        self.assertFalse(lin_old["live"])
        self.assertEqual(lin_old["superseded_by"], "v2")
        self.assertEqual(lin_old["source_version"], "1")
        # tenant isolation: another tenant cannot resolve it
        self.assertIsNone(ver.lineage(self.p, "isolation-check", live.id))
        self.assertIsNone(ver.lineage(self.p, T, "pas_nope"))


if __name__ == "__main__":
    unittest.main()
