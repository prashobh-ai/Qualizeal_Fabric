"""T116 — the connectors admin is restored: five cards always render (default
rows even when never synced), each row carries enabled/allow/interval_s/config/
scopes + a health join, and a non-admin gets a clean 403 (not a "not found")."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.connectors import admin
from knowledge_fabric.surfaces import http_api
from tests.util import seeded

T = "test-fabric"


class TestConnectorsAdmin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = seeded([T])
        http_api._platform = cls.p
        http_api._svc = AnswerService(cls.p)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = {u: cls._login(u) for u in ("admin", "asker.public")}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        http_api._platform = None
        http_api._svc = None

    @classmethod
    def _login(cls, subject):
        r = urlopen(
            Request(
                f"http://127.0.0.1:{cls.port}/login",
                data=json.dumps({"tenant": T, "subject": subject}).encode(),
                method="POST",
            )
        )
        return json.load(r)["token"]

    def _get(self, path, user):
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Authorization": f"Bearer {self.tok[user]}"},
        )
        try:
            with urlopen(req) as r:
                return r.status, json.load(r)
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_five_cards_always_render_with_full_rows(self):
        code, out = self._get("/admin/connectors", "admin")
        self.assertEqual(code, 200)
        sources = {c["source"] for c in out["connectors"]}
        self.assertEqual(sources, set(admin.KNOWN_SOURCES))
        for c in out["connectors"]:
            for k in ("enabled", "allow", "config", "scopes", "interval_s", "health"):
                self.assertIn(k, c, f"{c['source']} row missing {k}")
        self.assertIn("schedules", out)

    def test_non_admin_gets_clean_403_not_not_found(self):
        code, out = self._get("/admin/connectors", "asker.public")
        self.assertEqual(code, 403)
        # a JSON message the client renders as "sign in as admin", never a bare
        # route-not-found the client surfaces as a red "Request failed" banner
        self.assertTrue(out.get("error"))
        self.assertNotIn("not found", str(out.get("error", "")).lower())


if __name__ == "__main__":
    unittest.main()
