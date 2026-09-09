"""Stage-2 integration: the critic-proven fixes stay fixed."""
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from knowledge_fabric.adapters.identity import build_identity, LocalIdP, OIDCIdentity, OIDCNotReady
from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.contracts.types import AnswerKind
from knowledge_fabric.ingestion import scheduler
from knowledge_fabric.ingestion.intake import Intake, IngestWorker
from knowledge_fabric.surfaces import http_api
from knowledge_fabric.tenants import demo
from tests.util import seeded

T = "acme-assurance"


class TestAnswerPathFixes(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T]); self.svc = AnswerService(self.p)
        self.asker = demo.principal_for(self.p, T, "asha.asker")

    def test_ingestion_invalidates_answer_cache(self):
        q = "what is the acceptance criteria for coverage?"
        a1 = self.svc.ask(self.asker, q)
        intake, worker = Intake(self.p), IngestWorker(self.p, None); worker.intake = intake
        intake.upload(T, "new-policy.md", b"# Policy\n\nCoverage acceptance criteria were revised to 98 percent.")
        worker.drain()
        a2 = self.svc.ask(self.asker, q)
        self.assertFalse(a2.cache_hit, "an ingest must drop cached answers")
        self.assertGreater(a2.dataset_version, a1.dataset_version)

    def test_complexity_not_lowered_by_model_off(self):
        from knowledge_fabric.adapters.model import DisabledModelClient
        q = "why does a component with an open defect block its dependent releases?"
        on = self.svc.ask(self.asker, q); self.p.cache.invalidate(T)
        self.p.model = DisabledModelClient()
        off = self.svc.ask(self.asker, q)
        self.assertEqual(off.tier, "none")
        self.assertEqual(off.complexity, on.complexity, "complexity describes the query, not the tier")

    def test_reasoned_answer_is_cached(self):
        q = "what must a release achieve before promotion and which requirement has a traceability gap?"
        a1 = self.svc.ask(self.asker, q); self.assertEqual((a1.reasoning or {}).get("mode"), "multistep")
        a2 = self.svc.ask(self.asker, q)
        self.assertTrue(a2.cache_hit); self.assertEqual((a2.reasoning or {}).get("mode"), "multistep")


class TestIdentitySelection(unittest.TestCase):
    def test_default_local_and_oidc_selection(self):
        self.assertIsInstance(build_identity({}, "s"), LocalIdP)
        o = build_identity({"KF_IDENTITY": "oidc", "KF_OIDC_ISSUER": "https://idp.example.com/x",
                            "KF_OIDC_AUDIENCE": "kf"}, "s")
        self.assertIsInstance(o, OIDCIdentity)
        self.assertEqual(o.jwks_url, "https://idp.example.com/x/.well-known/jwks.json")
        with self.assertRaises(ValueError):
            build_identity({"KF_IDENTITY": "oidc"}, "s")

    def test_oidc_fails_closed_without_verifier_or_valid_token(self):
        o = OIDCIdentity("https://idp.example.com/x", "kf")
        with self.assertRaises((OIDCNotReady, PermissionError, Exception)):
            o.authenticate({"token": "a.b.c"})
        with self.assertRaises(PermissionError):
            o.authenticate({})


class TestHttpFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = seeded([T]); http_api._platform = cls.p; http_api._svc = AnswerService(cls.p)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = {u: cls._login(u) for u in ("adar.admin", "carl.curator", "asha.asker")}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); http_api._platform = None; http_api._svc = None

    @classmethod
    def _login(cls, subject):
        r = urlopen(Request(f"http://127.0.0.1:{cls.port}/login", data=json.dumps(
            {"tenant": T, "subject": subject}).encode(), method="POST"))
        return json.load(r)["token"]

    def _call(self, method, path, user, body=None):
        req = Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": f"Bearer {self.tok[user]}"})
        try:
            with urlopen(req) as r:
                return r.status, json.load(r)
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_rollback_unknown_version_is_404_not_crash(self):
        code, docs = self._call("GET", "/curator/documents", "carl.curator")
        doc_id = docs["documents"][0]["document_id"]
        code, out = self._call("POST", "/curator/decision", "carl.curator",
                               {"document_id": doc_id, "decision": "rollback", "to_version": 99})
        self.assertEqual(code, 404)
        code, out = self._call("POST", "/curator/decision", "carl.curator",
                               {"document_id": doc_id, "decision": "rollback", "to_version": "x"})
        self.assertEqual(code, 400)

    def test_sync_unknown_source_404_and_files_default_folder(self):
        code, out = self._call("POST", "/admin/sync", "adar.admin", {"source": "confluence"})
        self.assertEqual(code, 404)
        code, out = self._call("POST", "/admin/sync", "adar.admin", {"source": "files"})
        self.assertEqual(code, 200)
        self.assertNotIn("KeyError", json.dumps(out))

    def test_interval_change_keeps_schedule_config(self):
        self._call("POST", "/admin/connectors", "adar.admin",
                   {"source": "jira", "interval_s": 120, "config": {"projects": ["REL"]}})
        self._call("POST", "/admin/connectors", "adar.admin", {"source": "jira", "interval_s": 300})
        sched = scheduler.get_schedule(self.p, T, "jira")
        self.assertEqual(sched["config"].get("projects"), ["REL"])
        self.assertEqual(sched["interval_s"], 300)

    def test_doctor_route_in_process(self):
        code, out = self._call("GET", "/admin/doctor?target=aws", "adar.admin")
        self.assertEqual(code, 200); self.assertIn("rendered", out); self.assertIn("selection", out)
        code, _ = self._call("GET", "/admin/doctor?target=mars", "adar.admin")
        self.assertEqual(code, 400)

    def test_versions_diff_route(self):
        code, docs = self._call("GET", "/curator/documents", "carl.curator")
        doc_id = docs["documents"][0]["document_id"]
        code, out = self._call("GET", f"/curator/versions?document_id={doc_id}&from=1&to=1", "carl.curator")
        self.assertEqual(code, 200); self.assertIn("unchanged", out["diff"])

    def test_otlp_dry_run_route(self):
        code, out = self._call("GET", "/admin/otlp", "adar.admin")
        self.assertEqual(code, 200); self.assertIn("resourceSpans", json.dumps(out))


class TestRefreshLoops(unittest.TestCase):
    def test_start_and_stop_loops(self):
        p = seeded([T])
        os.environ["KF_REFRESH_TICK_S"] = "0.2"
        loops = http_api.start_refresh_loops(p)
        try:
            self.assertTrue(loops and all(l.running for l in loops))
        finally:
            for l in loops:
                l.stop()
            os.environ.pop("KF_REFRESH_TICK_S", None)
        self.assertFalse(any(l.running for l in loops))


if __name__ == "__main__":
    unittest.main()
