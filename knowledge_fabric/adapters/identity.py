"""Identity: a local OIDC-style provider that issues REAL signed tokens.

The Runbook's central point (Section 0) is that the demo must run real
identity locally, not the stub. Since the environment's ``cryptography``
build is broken, we implement HS256 JWTs directly with stdlib ``hmac`` —
genuine signed, expiring, audience-checked tokens. Moving to enterprise SSO
later means pointing this same adapter at the corporate IdP's JWKS: the
Principal shape and every downstream check are unchanged (zero code change).

A ``StubIdentity`` remains for the inner dev loop only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from ..contracts.types import Principal


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class LocalIdP:
    """HS256 signer/verifier standing in for Keycloak/Dex locally."""

    def __init__(self, secret: str, issuer: str = "kf-local", audience: str = "knowledge-fabric",
                 ttl_s: int = 3600):
        self.secret = secret.encode()
        self.issuer = issuer
        self.audience = audience
        self.ttl_s = ttl_s

    def mint(self, principal: Principal, ttl_s: int | None = None) -> str:
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": principal.subject, "tenant": principal.tenant,
            "roles": principal.roles, "scopes": principal.scopes,
            "agent": principal.agent, "iss": self.issuer, "aud": self.audience,
            "iat": now, "exp": now + (ttl_s or self.ttl_s),
        }
        seg = f"{_b64u(json.dumps(header).encode())}.{_b64u(json.dumps(payload).encode())}"
        sig = hmac.new(self.secret, seg.encode(), hashlib.sha256).digest()
        return f"{seg}.{_b64u(sig)}"

    def _verify(self, token: str) -> dict:
        try:
            h, p, s = token.split(".")
        except ValueError:
            raise PermissionError("malformed token")
        seg = f"{h}.{p}"
        expected = hmac.new(self.secret, seg.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64u_dec(s)):
            raise PermissionError("bad token signature")
        payload = json.loads(_b64u_dec(p))
        if payload.get("aud") != self.audience:
            raise PermissionError("wrong audience")
        if payload.get("exp", 0) < int(time.time()):
            raise PermissionError("token expired")
        return payload

    def authenticate(self, credentials: dict) -> Principal:
        token = credentials.get("token") or credentials.get("Authorization", "").replace("Bearer ", "")
        if not token:
            raise PermissionError("no bearer token")
        p = self._verify(token)
        return Principal(subject=p["sub"], tenant=p["tenant"], roles=p.get("roles", []),
                         scopes=p.get("scopes", []), agent=bool(p.get("agent", False)))


class StubIdentity:
    """Inner-dev-loop only: trusts request headers. Never used for the demo."""

    def mint(self, principal: Principal, ttl_s: int | None = None) -> str:
        return "stub"

    def authenticate(self, credentials: dict) -> Principal:
        return Principal(
            subject=credentials.get("x-subject", "anon"),
            tenant=credentials.get("x-tenant", ""),
            roles=[r for r in credentials.get("x-roles", "").split(",") if r],
            scopes=[s for s in credentials.get("x-scopes", "").split(",") if s],
            agent=credentials.get("x-agent", "false").lower() == "true",
        )


# ---------------------------------------------------------------------------
# OIDC (AWS Cognito / corporate IdP) — the cloud-shape Principal adapter.
# Consumes the IaC-exported KF_OIDC_ISSUER / KF_OIDC_AUDIENCE. Signature
# verification needs an RS256-capable library (PyJWT + cryptography, both
# permissive) which the stdlib-only image lacks; when it is missing the adapter
# FAILS CLOSED with a clear error rather than accepting unverified tokens.
# Selected by KF_IDENTITY=oidc (see build_identity); the Principal shape and
# every downstream check are unchanged — swapping IdPs is config only.
# ---------------------------------------------------------------------------
class OIDCNotReady(RuntimeError):
    pass


class OIDCIdentity:
    def __init__(self, issuer: str, audience: str, jwks_url: str | None = None,
                 roles_claim: str = "roles", scopes_claim: str = "scopes",
                 tenant_claim: str = "tenant"):
        if not issuer or not audience:
            raise ValueError("OIDC identity requires KF_OIDC_ISSUER and KF_OIDC_AUDIENCE")
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_url = jwks_url or f"{self.issuer}/.well-known/jwks.json"
        self.roles_claim, self.scopes_claim, self.tenant_claim = roles_claim, scopes_claim, tenant_claim
        self._jwks: dict | None = None

    def mint(self, principal: Principal, ttl_s: int | None = None) -> str:
        raise OIDCNotReady("tokens are minted by the external IdP, not by the application")

    def _load_jwks(self) -> dict:
        if self._jwks is None:
            import urllib.request
            with urllib.request.urlopen(self.jwks_url, timeout=10) as r:
                self._jwks = json.loads(r.read())
        return self._jwks

    def _decode(self, token: str) -> dict:
        try:
            import jwt  # PyJWT (MIT) + cryptography (Apache-2.0/BSD) — cloud image extras
            from jwt import PyJWKClient
        except BaseException as e:  # a broken native build panics with a BaseException — still fail closed
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            raise OIDCNotReady(f"OIDC verification needs PyJWT+cryptography in the image: {type(e).__name__}")
        key = PyJWKClient(self.jwks_url).get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=self.audience,
                          issuer=self.issuer)

    def authenticate(self, credentials: dict) -> Principal:
        token = credentials.get("token") or credentials.get("Authorization", "").replace("Bearer ", "")
        if not token:
            raise PermissionError("no bearer token")
        claims = self._decode(token)          # signature, exp, aud, iss all verified by the library
        roles = claims.get(self.roles_claim) or claims.get("cognito:groups") or []
        scopes = claims.get(self.scopes_claim) or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        tenant = claims.get(self.tenant_claim) or claims.get("custom:tenant") or ""
        if not tenant:
            raise PermissionError("token carries no tenant claim (I5)")
        return Principal(subject=claims.get("sub", ""), tenant=tenant, roles=list(roles),
                         scopes=list(scopes), agent=bool(claims.get("agent", False)))


def build_identity(env: dict, secret: str):
    """KF_IDENTITY=local (default) -> LocalIdP(HS256); oidc -> OIDCIdentity(issuer, audience)."""
    mode = (env.get("KF_IDENTITY") or "local").lower()
    if mode == "oidc":
        return OIDCIdentity(env.get("KF_OIDC_ISSUER", ""), env.get("KF_OIDC_AUDIENCE", ""),
                            env.get("KF_OIDC_JWKS_URL") or None)
    return LocalIdP(secret)
