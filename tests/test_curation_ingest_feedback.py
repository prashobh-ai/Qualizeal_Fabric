"""Product-quality fixes for the deployed Admin/Curator flows.

These lock in the behaviour behind three reported defects:

1. **The curation mode is now applied at ingest.** ``curation.on_ingest`` was
   implemented and unit-tested but never wired into any ingest door, so a source
   set to *manual* held nothing and the review queue was always empty. The new
   ``curation.settle_ingested`` hook is called by the admin/curator upload
   (``/admin/upload``) and the connector sync (``SyncManager.sync``): a manual
   source now holds each freshly-ingested document in the review queue (out of
   answers) until a curator accepts it, while automated stays a live-by-default
   no-op that never floods the queue or the timeline.

2. **The mode switch has a real, visible effect.** Switching the tenant default
   (or a source) to manual and then uploading demonstrably lands the document in
   ``/curator/review`` and holds it out of the answer path; accepting it makes
   the same content answerable.

3. **Negative feedback captures the full chat context.** ``POST /feedback`` now
   stores what the AI answered, the sources it cited, the persona/level and the
   turns before it — not just the bare question — and ``/curator/feedback``
   returns them for the curator.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from knowledge_fabric import curation
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.ingestion.intake import IngestWorker, Intake
from knowledge_fabric.ingestion.sync import SyncManager
from knowledge_fabric.surfaces import http_api
from tests.util import seeded

T = "test-fabric"


# ==========================================================================
# unit — the settle_ingested hook the entry points share
# ==========================================================================
class TestSettleIngested(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")
        self.intake = Intake(self.p)
        self.worker = IngestWorker(self.p, self.intake)

    def _upload_one(self, title="Nimbus Ledger Reconciler"):
        body = (
            f"The {title} closes the books nightly and flags every variance "
            f"over a configured threshold to the finance controller."
        )
        self.intake.upload(T, f"qa/{title}.md", body.encode(), acl=["public"])
        return self.worker.drain()

    def test_automated_is_a_noop(self):
        # the authored/seeded corpus and the default automated mode leave a
        # freshly-uploaded document live and unlogged — no review, no timeline row
        before = len(curation.timeline(self.p, T)["rows"])
        res = self._upload_one()
        settled = curation.settle_ingested(self.p, T, res)
        self.assertEqual((settled["held"], settled["live"]), (0, 1))
        self.assertEqual(len(curation.review_queue(self.p, T)), 0)
        self.assertEqual(len(curation.timeline(self.p, T)["rows"]), before)

    def test_manual_holds_and_logs(self):
        curation.set_default_mode(self.p, T, "manual")
        res = self._upload_one()
        settled = curation.settle_ingested(self.p, T, res)
        self.assertEqual(settled["held"], 1)
        q = curation.review_queue(self.p, T)
        self.assertEqual(len(q), 1)
        doc_id = [r for r in res if r.get("status") in ("ok", "updated")][0]["document_id"]
        held = self.p.documents.get(T, doc_id)
        self.assertFalse(curation.is_live(held))  # held out of retrieval
        # accepting it clears the queue and makes it live
        curation.accept(self.p, T, q[0]["review_id"], actor="curator")
        self.assertEqual(len(curation.review_queue(self.p, T)), 0)
        self.assertTrue(curation.is_live(self.p.documents.get(T, doc_id)))

    def test_sync_obeys_a_manual_source(self):
        # a connector whose source is on manual review holds its synced items.
        # Fresh keys (the seed already synced JIRA_RECORDS, which are noop-by-hash).
        fresh = [
            {
                "project": "REL",
                "key": "REL-777",
                "summary": "Manual-review probe issue",
                "status": "Open",
                "updated": 9001,
                "acl": ["public"],
                "description": (
                    "A synthetic Jira issue used to prove that a source on manual "
                    "curation review holds its synced items in the review queue."
                ),
            }
        ]
        curation.set_mode(self.p, T, "jira", "manual")
        summary = SyncManager(self.p).sync(T, "jira", {"projects": ["REL"]}, records=fresh)
        self.assertGreater(summary["ingested"], 0)
        self.assertEqual(summary["held_for_review"], summary["ingested"])
        self.assertGreaterEqual(len(curation.review_queue(self.p, T)), summary["ingested"])


# ==========================================================================
# served — the real handlers behind the Admin/Curator consoles
# ==========================================================================
class ServedBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform = seeded([T], model_mode="extractive")
        cls._saved = (http_api._platform, http_api._svc)
        http_api._platform = cls.platform
        http_api._svc = AnswerService(cls.platform)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.tokens = {s: cls._login(s) for s in ("asker.public", "curator", "admin")}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        http_api._platform, http_api._svc = cls._saved

    @classmethod
    def _call(cls, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(cls.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    @classmethod
    def _login(cls, subject):
        code, raw = cls._call("POST", "/login", {"tenant": T, "subject": subject})
        assert code == 200, raw
        return json.loads(raw)["token"]

    def _json(self, method, path, body=None, who=None):
        code, raw = self._call(method, path, body, self.tokens.get(who))
        return code, (json.loads(raw) if raw else {})


class TestAdminUploadObeysMode(ServedBase):
    def test_manual_upload_lands_in_review_then_accepts(self):
        # switch the tenant default to manual through the curator switch
        code, out = self._json(
            "POST", "/curator/curation-mode", {"source": "*", "mode": "manual"}, "curator"
        )
        self.assertEqual(code, 200, out)

        subject = "Aurora Settlement Gateway"
        text = (
            f"The {subject} reconciles interbank settlements and raises an alert "
            f"whenever a batch misses its cut-off window."
        )
        code, up = self._json(
            "POST",
            "/admin/upload",
            {"files": [{"filename": f"qa/{subject}.md", "text": text}]},
            "admin",
        )
        self.assertEqual(code, 200, up)
        self.assertEqual(up["ingested"], 1)
        self.assertEqual(up["held_for_review"], 1)  # the switch had a real effect

        # the item is a genuine review-queue entry
        code, rv = self._json("GET", "/curator/review", who="curator")
        self.assertEqual(code, 200, rv)
        items = rv["items"]
        self.assertTrue(any(subject in (i.get("title") or "") for i in items), items)
        rid = next(i["review_id"] for i in items if subject in (i.get("title") or ""))

        # held out of the answer path while in review
        code, a = self._json(
            "POST", "/ask", {"question": f"what is the {subject}?"}, "asker.public"
        )
        self.assertEqual(code, 200, a)
        self.assertFalse(
            any(subject.lower() in (c["document_title"] or "").lower() for c in a["citations"])
        )

        # accepting it makes the same content answerable
        code, dec = self._json(
            "POST", "/curator/review-decision", {"review_id": rid, "action": "accept"}, "curator"
        )
        self.assertEqual(code, 200, dec)
        code, rv2 = self._json("GET", "/curator/review", who="curator")
        self.assertFalse(any(subject in (i.get("title") or "") for i in rv2["items"]))


class TestNegativeFeedbackCapturesContext(ServedBase):
    def test_feedback_stores_answer_citations_and_context(self):
        q = "what must a release achieve before promotion?"
        code, a = self._json("POST", "/ask", {"question": q}, "asker.public")
        self.assertEqual(code, 200, a)

        body = {
            "verdict": "down",
            "question": q,
            "trace_id": a["trajectory_id"],
            "level": (a.get("why") or {}).get("level_name", ""),
            "note": "answer was incomplete",
            "answer": a.get("result") or a.get("answer_text") or "",
            "kind": a.get("kind", ""),
            "persona": (a.get("role_view") or {}).get("persona", ""),
            "citations": [
                {
                    "title": c["document_title"],
                    "where": c.get("coordinate_render", ""),
                    "snippet": c.get("snippet", ""),
                }
                for c in a["citations"]
            ],
            "context": [{"q": "earlier question", "a": "earlier answer"}],
        }
        code, ok = self._json("POST", "/feedback", body, "asker.public")
        self.assertEqual(code, 200, ok)

        code, fb = self._json("GET", "/curator/feedback", who="curator")
        self.assertEqual(code, 200, fb)
        rows = [r for r in fb["feedback"] if r.get("trace_id") == a["trajectory_id"]]
        self.assertEqual(len(rows), 1, fb)
        row = rows[0]
        # the curator can see what actually happened in that chat
        self.assertTrue(row["answer"], "the AI's answer text is captured")
        self.assertEqual(row["question"], q)
        self.assertEqual(row["note"], "answer was incomplete")
        self.assertEqual(row["context"], [{"q": "earlier question", "a": "earlier answer"}])
        if a["citations"]:
            self.assertTrue(row["citations"], "the cited sources are captured")
            self.assertIn("title", row["citations"][0])


if __name__ == "__main__":
    unittest.main()
