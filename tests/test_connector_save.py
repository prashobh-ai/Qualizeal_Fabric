"""T115 — the connector Save/Sync crash is fixed.

One source key per connector everywhere; the endpoint never raises to the client
for a known-shape request (it returns a shaped body with a `status`); an unknown
source is a shaped error, not a 500; and every card key is one of the five
canonical keys with no `jira_live`/`github_live` duplicates.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.connectors import admin, registry
from knowledge_fabric.connectors.github import GitHubConnector
from knowledge_fabric.connectors.github_live import GitHubLiveConnector
from knowledge_fabric.connectors.jira import JiraConnector
from knowledge_fabric.connectors.jira_live import JiraLiveConnector
from knowledge_fabric.surfaces import http_api
from tests.util import seeded

T = "test-fabric"


class TestSourceKeys(unittest.TestCase):
    def test_five_canonical_keys_only(self):
        self.assertEqual(
            sorted(registry.available()), ["confluence", "files", "github", "jira", "website"]
        )
        self.assertEqual(admin.KNOWN_SOURCES, ("website", "files", "github", "jira", "confluence"))
        self.assertTrue(admin.is_known("github") and admin.is_known("jira"))
        self.assertFalse(admin.is_known("github_live") or admin.is_known("jira_live"))

    def test_github_jira_resolve_to_live_for_real_sync(self):
        self.assertIsInstance(registry.build("github", "t", {}), GitHubLiveConnector)
        self.assertIsInstance(registry.build("jira", "t", {}), JiraLiveConnector)

    def test_records_injection_selects_replay(self):
        self.assertIsInstance(registry.build("github", "t", {}, records=[]), GitHubConnector)
        self.assertIsInstance(registry.build("jira", "t", {}, records=[]), JiraConnector)


class TestSaveEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = seeded([T])
        http_api._platform = cls.p
        http_api._svc = AnswerService(cls.p)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = cls._login("admin")

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

    def _post(self, path, body):
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self.tok}"},
        )
        try:
            with urlopen(req) as r:
                return r.status, json.load(r)
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def _get(self, path):
        req = Request(
            f"http://127.0.0.1:{self.port}{path}", headers={"Authorization": f"Bearer {self.tok}"}
        )
        with urlopen(req) as r:
            return r.status, json.load(r)

    def test_save_github_returns_shaped_ok_with_enabled(self):
        code, out = self._post(
            "/admin/connectors",
            {"source": "github", "enabled": True, "allow": ["acme/app"], "interval_s": 300},
        )
        self.assertEqual(code, 200)
        self.assertEqual(out["status"], "ok")
        # the row the client reads .enabled off is always present
        self.assertIn("enabled", out["connector"])
        self.assertTrue(out["connector"]["enabled"])

    def test_save_jira_without_secrets_saves_enabled(self):
        code, out = self._post(
            "/admin/connectors", {"source": "jira", "enabled": True, "allow": ["V1"]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["connector"]["enabled"])

    def test_unknown_source_is_shaped_error_not_500(self):
        code, out = self._post("/admin/connectors", {"source": "jira_live", "enabled": True})
        self.assertEqual(code, 200)  # never a 500 / bare body
        self.assertEqual(out["status"], "error")
        self.assertIn("unknown source", out["message"])
        # the connector row still exists so the client never reads off undefined
        self.assertIn("enabled", out["connector"])

    def test_one_card_per_source_no_live_duplicates(self):
        _, out = self._get("/admin/connectors")
        sources = [c["source"] for c in out["connectors"]]
        self.assertNotIn("jira_live", sources)
        self.assertNotIn("github_live", sources)
        # every card key is one of the five canonical keys
        for s in sources:
            self.assertIn(s, admin.KNOWN_SOURCES)


if __name__ == "__main__":
    unittest.main()
