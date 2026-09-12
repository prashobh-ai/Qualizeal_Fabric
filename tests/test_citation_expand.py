"""T93 — a citation expands to the paragraph in context.

The data was already on every ``Citation`` (snippet, coordinate, passage_id,
document_title); this proves the server side that lets the Workspace expand it:
``passages.context`` returns the cited passage plus its in-document neighbours in
reading order, and ``GET /api/passage/{id}`` serves that, ACL-gated, so a citation
can show the paragraph with the cited sentence highlighted.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.surfaces import http_api
from tests.util import T, seeded


class TestPassageContext(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T], model_mode="extractive")

    def _multi_passage_doc(self):
        for d in self.p.documents.list(T):
            ps = [x for x in self.p.passages.by_document(T, d["id"]) if x.superseded_by is None]
            if len(ps) >= 3:
                return d["id"], ps
        self.skipTest("no multi-passage document in the corpus")

    def test_context_returns_ordered_neighbours(self):
        _doc_id, ps = self._multi_passage_doc()
        ordered = sorted(ps, key=self.p.passages._reading_order)
        mid = ordered[len(ordered) // 2]
        ctx = self.p.passages.context(T, mid.id, radius=1)
        self.assertEqual(ctx["passage"].id, mid.id)
        self.assertTrue(ctx["before"] or ctx["after"])
        # neighbours are the immediate siblings in reading order, never the target
        ids = [x.id for x in ctx["before"]] + [x.id for x in ctx["after"]]
        self.assertNotIn(mid.id, ids)
        self.assertTrue(set(ids).issubset({x.id for x in ordered}))

    def test_context_none_for_unknown(self):
        self.assertIsNone(self.p.passages.context(T, "pas_does_not_exist"))


class TestPassageEndpoint(unittest.TestCase):
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
        cls.tok = cls._login("asker.public")

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
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    @classmethod
    def _login(cls, subject):
        code, body = cls._call("POST", "/login", {"tenant": T, "subject": subject})
        assert code == 200, body
        return body["token"]

    def test_endpoint_returns_cited_passage_and_neighbours(self):
        # ask a real question, then expand its first citation
        code, a = self._call(
            "POST", "/ask", {"question": "what must a release achieve before promotion?"}, self.tok
        )
        self.assertEqual(code, 200, a)
        self.assertTrue(a["citations"], a)
        pid = a["citations"][0]["passage_id"]
        code, ctx = self._call("GET", f"/api/passage/{pid}", token=self.tok)
        self.assertEqual(code, 200, ctx)
        self.assertEqual(ctx["passage"]["passage_id"], pid)
        self.assertTrue(ctx["passage"]["text"].strip())
        self.assertEqual(ctx["document_title"], a["citations"][0]["document_title"])
        self.assertIn("before", ctx)
        self.assertIn("after", ctx)

    def test_unknown_passage_is_404(self):
        code, _ = self._call("GET", "/api/passage/pas_nope", token=self.tok)
        self.assertEqual(code, 404)

    def test_requires_auth(self):
        code, _ = self._call("GET", "/api/passage/pas_anything")
        self.assertEqual(code, 401)


if __name__ == "__main__":
    unittest.main()
