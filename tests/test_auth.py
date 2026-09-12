"""T117 — real login: PBKDF2 credentials, the /api/auth endpoints, and the
OIDC/OAuth2 authorization-code-with-PKCE flow (offline).

Gate checks:
  * a seeded user logs in with a real role; an unknown account
    (``donkey@qualizeal.com``) and a wrong password are 401 — never a free entry.
  * the passwordless ``{tenant, subject}`` ``/login`` is 403 unless KF_DEV_LOGIN=1.
  * ``/api/auth/config`` reports the mode the sign-in page renders from.
  * the OIDC flow builds a valid PKCE authorize URL, exchanges the code through
    an injected transport, and maps verified group claims to internal roles.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from knowledge_fabric.answer.service import AnswerService
from knowledge_fabric.auth import users
from knowledge_fabric.auth.oidc import OIDCFlow, pkce_pair
from knowledge_fabric.surfaces import http_api
from tests.util import seeded

T = "test-fabric"


class TestPasswordHashing(unittest.TestCase):
    def test_hash_roundtrip_and_wrong_password(self):
        stored = users.hash_password("s3cret!")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertTrue(users.verify_password(stored, "s3cret!"))
        self.assertFalse(users.verify_password(stored, "wrong"))

    def test_hash_is_salted_per_call(self):
        self.assertNotEqual(users.hash_password("x"), users.hash_password("x"))

    def test_verify_rejects_garbage(self):
        self.assertFalse(users.verify_password("not-a-hash", "x"))
        self.assertFalse(users.verify_password("", "x"))

    def test_scopes_for_roles(self):
        self.assertEqual(users.scopes_for(["asker"]), ["public"])
        self.assertEqual(users.scopes_for(["curator"]), ["public", "restricted"])
        self.assertEqual(users.scopes_for(["admin"]), ["public", "restricted"])


class TestUserStore(unittest.TestCase):
    def setUp(self):
        self.p = seeded([T])

    def test_create_lowercases_and_verifies(self):
        self.p.users.create("Alice@Qualizeal.com", "pw12345", ["curator"], tenant=T)
        u = self.p.users.verify("alice@qualizeal.com", "pw12345")
        self.assertIsNotNone(u)
        self.assertEqual(u["email"], "alice@qualizeal.com")
        self.assertEqual(u["roles"], ["curator"])
        self.assertEqual(u["scopes"], ["public", "restricted"])

    def test_wrong_password_returns_none(self):
        self.p.users.create("bob@qualizeal.com", "right", ["asker"], tenant=T)
        self.assertIsNone(self.p.users.verify("bob@qualizeal.com", "nope"))

    def test_unknown_user_returns_none(self):
        self.assertIsNone(self.p.users.verify("donkey@qualizeal.com", "anything"))

    def test_inactive_user_cannot_log_in(self):
        self.p.users.create("carol@qualizeal.com", "pw", ["asker"], tenant=T, status="disabled")
        self.assertIsNone(self.p.users.verify("carol@qualizeal.com", "pw"))

    def test_seed_from_env(self):
        payload = json.dumps(
            [{"email": "seed@qualizeal.com", "password": "seeded1", "roles": ["admin"]}]
        )
        os.environ["KF_SEED_USERS_TEST"] = payload
        try:
            n = users.seed_from_env(self.p.users, tenant=T, env="KF_SEED_USERS_TEST")
        finally:
            del os.environ["KF_SEED_USERS_TEST"]
        self.assertEqual(n, 1)
        u = self.p.users.verify("seed@qualizeal.com", "seeded1")
        self.assertIsNotNone(u)
        self.assertEqual(u["roles"], ["admin"])

    def test_seed_from_env_absent_is_zero(self):
        self.assertEqual(
            users.seed_from_env(self.p.users, tenant=T, env="KF_DEFINITELY_UNSET_XYZ"), 0
        )


class TestOIDCFlow(unittest.TestCase):
    ENV = {
        "OIDC_ISSUER": "https://idp.example.com",
        "OIDC_CLIENT_ID": "kf-client",
        "OIDC_CLIENT_SECRET": "shhh",
        "OIDC_REDIRECT_URI": "https://kf.example.com/api/auth/sso/callback",
        "OIDC_AUTH_URL": "https://idp.example.com/authorize",
        "OIDC_TOKEN_URL": "https://idp.example.com/token",
    }

    def test_configured(self):
        self.assertTrue(OIDCFlow(self.ENV).configured())
        self.assertFalse(OIDCFlow({}).configured())

    def test_pkce_pair_is_s256(self):
        verifier, challenge = pkce_pair()
        expect = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        self.assertEqual(challenge, expect)

    def test_authorize_url_has_pkce_and_state(self):
        flow = OIDCFlow(self.ENV)
        _, challenge = pkce_pair()
        url = flow.authorize_url("st8", challenge)
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["client_id"], ["kf-client"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["code_challenge"], [challenge])
        self.assertEqual(q["state"], ["st8"])

    def test_exchange_code_uses_injected_transport(self):
        captured = {}

        def transport(url, data, headers):
            captured["url"] = url
            captured["body"] = parse_qs(data.decode())
            return json.dumps({"id_token": "the.jwt.token", "access_token": "a"}).encode()

        flow = OIDCFlow(self.ENV, transport=transport)
        out = flow.exchange_code("auth-code", "the-verifier")
        self.assertEqual(out["id_token"], "the.jwt.token")
        self.assertEqual(captured["url"], "https://idp.example.com/token")
        self.assertEqual(captured["body"]["grant_type"], ["authorization_code"])
        self.assertEqual(captured["body"]["code_verifier"], ["the-verifier"])

    def test_map_roles_and_identity(self):
        flow = OIDCFlow(self.ENV)
        claims = {
            "email": "Eng@qualizeal.com",
            "groups": ["AI-CoE-Admins"],
            "title": "Principal Engineer",
        }
        self.assertEqual(flow.map_roles(claims), ["admin"])
        ident = flow.identity(claims)
        self.assertEqual(ident["email"], "eng@qualizeal.com")
        self.assertEqual(ident["roles"], ["admin"])
        self.assertEqual(ident["scopes"], ["public", "restricted"])
        self.assertEqual(ident["designation"], "Principal Engineer")

    def test_unknown_group_defaults_to_asker(self):
        flow = OIDCFlow(self.ENV)
        self.assertEqual(flow.map_roles({"groups": ["Randoms"]}), ["asker"])


class TestAuthEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = seeded([T])
        cls.p.users.create(
            "curator@qualizeal.com", "curate-me", ["curator"], tenant=T, designation="Lead Curator"
        )
        http_api._platform = cls.p
        http_api._svc = AnswerService(cls.p)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), http_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        http_api._platform = None
        http_api._svc = None

    def _post(self, path, body, headers=None):
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode(),
            method="POST",
            headers=headers or {},
        )
        try:
            with urlopen(req) as r:
                return r.status, json.load(r)
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def _get(self, path, headers=None):
        req = Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urlopen(req) as r:
                return r.status, json.load(r)
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_login_accepts_seeded_user(self):
        code, out = self._post(
            "/api/auth/login", {"email": "curator@qualizeal.com", "password": "curate-me"}
        )
        self.assertEqual(code, 200)
        self.assertEqual(out["subject"], "curator@qualizeal.com")
        self.assertEqual(out["roles"], ["curator"])
        self.assertIn("restricted", out["scopes"])
        self.assertEqual(out["designation"], "Lead Curator")
        self.assertTrue(out["token"])

    def test_login_rejects_wrong_password(self):
        code, out = self._post(
            "/api/auth/login", {"email": "curator@qualizeal.com", "password": "WRONG"}
        )
        self.assertEqual(code, 401)
        self.assertIn("invalid", out["error"].lower())

    def test_login_rejects_unknown_donkey(self):
        code, _ = self._post(
            "/api/auth/login", {"email": "donkey@qualizeal.com", "password": "hee-haw"}
        )
        self.assertEqual(code, 401)

    def test_whoami_resolves_token(self):
        _, login = self._post(
            "/api/auth/login", {"email": "curator@qualizeal.com", "password": "curate-me"}
        )
        code, out = self._get(
            "/api/auth/whoami", headers={"Authorization": f"Bearer {login['token']}"}
        )
        self.assertEqual(code, 200)
        self.assertEqual(out["subject"], "curator@qualizeal.com")
        self.assertEqual(out["roles"], ["curator"])

    def test_whoami_without_token_is_401(self):
        code, _ = self._get("/api/auth/whoami")
        self.assertEqual(code, 401)

    def test_auth_config_shape_password_mode(self):
        # No OIDC_* set in this process → password mode, SSO disabled.
        code, out = self._get("/api/auth/config")
        self.assertEqual(code, 200)
        self.assertEqual(out["mode"], "password")
        self.assertFalse(out["sso"]["enabled"])
        self.assertIn("dev_login", out)

    def test_dev_login_gated_by_env(self):
        # conftest sets KF_DEV_LOGIN=1, so the dev path works; when disabled it is 403.
        code, out = self._post("/login", {"tenant": T, "subject": "admin"})
        self.assertEqual(code, 200)
        self.assertTrue(out["token"])

        saved = os.environ.pop("KF_DEV_LOGIN", None)
        try:
            code, _ = self._post("/login", {"tenant": T, "subject": "admin"})
            self.assertEqual(code, 403)
        finally:
            if saved is not None:
                os.environ["KF_DEV_LOGIN"] = saved


if __name__ == "__main__":
    unittest.main()
