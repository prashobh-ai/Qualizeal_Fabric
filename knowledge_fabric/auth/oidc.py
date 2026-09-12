"""OpenID Connect / OAuth2 authorization-code-with-PKCE flow (T117).

Switched on when the company provides an IdP: with ``OIDC_ISSUER``,
``OIDC_CLIENT_ID``, ``OIDC_CLIENT_SECRET`` and ``OIDC_REDIRECT_URI`` set, the
sign-in page shows "Sign in with SSO", ``/api/auth/sso/login`` redirects to the
IdP, and ``/api/auth/sso/callback`` exchanges the code, validates the ``id_token``
(issuer, audience, signature via the IdP JWKS — reusing
:class:`adapters.identity.OIDCIdentity`), maps the verified email and groups to
the user's roles/designation, and mints the same internal JWT. Entra ID, Okta
and Google are all OIDC-compliant, so this one flow covers them.

Endpoints are discovered from ``<issuer>/.well-known/openid-configuration`` (or
overridden by ``OIDC_AUTH_URL`` / ``OIDC_TOKEN_URL`` for a non-discovery IdP);
the token exchange goes through an injectable transport so the flow is testable
offline. The group→role mapping is data (Admin → Settings), not code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import urllib.request

# a company SSO group → an internal role. Overridable in Admin → Settings; these
# are sensible defaults so an "…-Admins" group becomes admin out of the box.
DEFAULT_GROUP_MAP = {
    "AI-CoE-Admins": "admin",
    "Curators": "curator",
    "Engineers": "asker",
}


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def pkce_pair() -> tuple[str, str]:
    """``(verifier, challenge)`` — a PKCE S256 pair (RFC 7636)."""
    verifier = _b64u(secrets.token_bytes(32))
    challenge = _b64u(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def new_state() -> str:
    return _b64u(secrets.token_bytes(16))


class OIDCFlow:
    def __init__(self, env: dict | None = None, transport=None):
        e = env if env is not None else os.environ
        self.issuer = (e.get("OIDC_ISSUER") or "").rstrip("/")
        self.client_id = e.get("OIDC_CLIENT_ID") or ""
        self.client_secret = e.get("OIDC_CLIENT_SECRET") or ""
        self.redirect_uri = e.get("OIDC_REDIRECT_URI") or ""
        self.group_claim = e.get("OIDC_GROUP_CLAIM") or "groups"
        self.audience = e.get("OIDC_AUDIENCE") or self.client_id
        self._auth_url = e.get("OIDC_AUTH_URL") or ""
        self._token_url = e.get("OIDC_TOKEN_URL") or ""
        self.label = e.get("OIDC_LABEL") or "SSO"
        self._transport = transport  # (url, data:bytes|None, headers) -> bytes
        self._discovery: dict | None = None

    def configured(self) -> bool:
        return bool(self.issuer and self.client_id and self.client_secret and self.redirect_uri)

    # -- discovery ----------------------------------------------------------
    def _fetch(self, url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
        if self._transport is not None:
            return self._transport(url, data, headers or {})
        req = urllib.request.Request(url, data=data, headers=headers or {})
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — issuer is configured
            return r.read()

    def discovery(self) -> dict:
        if self._discovery is None:
            if self._auth_url and self._token_url:
                self._discovery = {
                    "authorization_endpoint": self._auth_url,
                    "token_endpoint": self._token_url,
                }
            else:
                raw = self._fetch(self.issuer + "/.well-known/openid-configuration")
                self._discovery = json.loads(raw)
        return self._discovery

    # -- the flow -----------------------------------------------------------
    def authorize_url(
        self, state: str, code_challenge: str, *, scope: str = "openid email profile"
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return self.discovery()["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)

    def exchange_code(self, code: str, code_verifier: str) -> dict:
        """Exchange the authorization code for tokens at the token endpoint."""
        body = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code_verifier": code_verifier,
            }
        ).encode("ascii")
        raw = self._fetch(
            self.discovery()["token_endpoint"],
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return json.loads(raw)

    # -- claims → identity --------------------------------------------------
    def map_roles(self, claims: dict, group_map: dict | None = None) -> list[str]:
        """Map the IdP group claim to internal roles; default to ``asker``."""
        gmap = {k.lower(): v for k, v in (group_map or DEFAULT_GROUP_MAP).items()}
        groups = claims.get(self.group_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        roles: list[str] = []
        for g in groups:
            r = gmap.get(str(g).lower())
            if r and r not in roles:
                roles.append(r)
        return roles or ["asker"]

    def identity(self, claims: dict, group_map: dict | None = None) -> dict:
        """A verified ``id_token``'s claims → ``{email, roles, scopes, designation}``.
        The caller has already validated issuer/audience/signature."""
        from .users import scopes_for

        email = (claims.get("email") or claims.get("preferred_username") or "").strip().lower()
        roles = self.map_roles(claims, group_map)
        designation = claims.get("title") or claims.get("job_title") or ""
        return {
            "email": email,
            "roles": roles,
            "scopes": scopes_for(roles),
            "designation": designation,
            "name": claims.get("name") or email,
        }
