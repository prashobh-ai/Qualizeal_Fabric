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
