"""WS1 · CONNECT — Jira connector, registry, auto-sync, DOCX, bulk upload."""

import io
import unittest
import zipfile

from knowledge_fabric.app import Platform
from knowledge_fabric.connectors import registry
from knowledge_fabric.connectors.jira import JiraConnector
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.ingestion.sync import SyncManager


def _docx(paras):
    buf = io.BytesIO()
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    doc = '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>' + body + "</w:body></w:document>"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


class TestConnect(unittest.TestCase):
    def setUp(self):
        self.p = Platform(db_path=":memory:", blob_root="./data/test-blobs")

    def test_registry_known_and_unknown(self):
        self.assertIn("jira", registry.available())
        self.assertIn("github", registry.available())
        with self.assertRaises(KeyError):
            registry.build("salesforce", "t1", {})

    def test_jira_read_only_scoped(self):
        c = JiraConnector("t1", {"projects": ["REL"]})
        self.assertTrue(c.read_only)
        self.assertEqual(c.scopes(), ["jira:read"])
        with self.assertRaises(PermissionError):
            c.write_back()

    def test_jira_backfill_incremental_tombstone(self):
        recs = [
            {
                "project": "REL",
                "key": "REL-1",
                "summary": "a",
                "status": "Open",
                "updated": 10,
                "description": "first",
            },
            {
                "project": "REL",
                "key": "REL-2",
                "summary": "b",
                "status": "Open",
                "updated": 20,
                "description": "second",
            },
        ]
        c = JiraConnector("t1", {"projects": ["REL"]}, records=recs)
        items, cur = c.pull(None)
        self.assertEqual(len(items), 2)
        recs.append(
            {
                "project": "REL",
                "key": "REL-3",
                "summary": "c",
                "status": "Open",
                "updated": 30,
                "description": "third",
            }
        )
        items2, cur2 = c.pull(cur)
        self.assertEqual(len(items2), 1)
        # allow-list excludes other projects
        c2 = JiraConnector(
            "t1",
            {"projects": ["REL"]},
            records=[
                {
                    "project": "OTHER",
                    "key": "X-1",
                    "summary": "s",
                    "status": "Open",
                    "updated": 40,
                    "description": "d",
                }
            ],
        )
        self.assertEqual(c2.pull(None)[0], [])

    def test_sync_ingests_and_tracks_freshness(self):
        recs = [
            {
                "project": "REL",
                "key": "REL-1",
                "summary": "release blocker",
                "status": "Open",
                "updated": 10,
                "description": "traceability gap on requirement",
            }
        ]
        sm = SyncManager(self.p)
        res = sm.sync("t1", "jira", {"projects": ["REL"]}, records=recs)
        self.assertEqual(res["ingested"], 1)
        self.assertTrue(res["read_only"])
        health = sm.source_health("t1")
        self.assertEqual(health[0]["source"], "jira")

    def test_sync_incremental_then_tombstone(self):
        sm = SyncManager(self.p)
        recs = [
            {
                "project": "REL",
                "key": "REL-1",
                "summary": "s",
                "status": "Open",
                "updated": 10,
                "description": "content about coverage",
            }
        ]
        sm.sync("t1", "jira", {"projects": ["REL"]}, records=recs)
        n1 = self.p.passages.count("t1")
        self.assertGreater(n1, 0)
        # delete the issue -> tombstone removes its passages
        recs2 = recs + [
            {
                "project": "REL",
                "key": "REL-1",
                "summary": "s",
                "status": "Deleted",
                "updated": 20,
                "description": "",
                "deleted": True,
            }
        ]
        sm2 = SyncManager(self.p)
        sm2.sync("t1", "jira", {"projects": ["REL"]}, records=recs2)
        self.assertEqual(self.p.documents.list("t1"), [])

    def test_docx_conversion_has_coordinates(self):
        intake, worker = Intake(self.p), IngestWorker(self.p, None)
        worker.intake = intake
        data = _docx(
            [
                "Requirement traceability must be complete.",
                "Coverage must reach ninety five percent.",
            ]
        )
        intake.upload("t1", "policy.docx", data)
        worker.drain()
        ps = self.p.passages.for_tenant("t1")
        self.assertGreaterEqual(len(ps), 2)
        self.assertTrue(all(p.coordinate.locator for p in ps))


if __name__ == "__main__":
    unittest.main()
