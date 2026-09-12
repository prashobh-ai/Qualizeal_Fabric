"""The users credential store (T117) — real password login, stdlib only.

Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user salt and ≥ 200k
iterations (``hashlib.pbkdf2_hmac`` — no external crypto dependency); a login is
verified with ``hmac.compare_digest`` so it is constant-time. A verified login
mints the platform's existing internal JWT for that user's real roles, scopes and
designation; there is no free-entry path.

Seed users come from the ``KF_SEED_USERS`` secret (a JSON list of
``{email, password, roles, designation}``) so only known accounts can sign in —
``donkey@qualizeal.com`` cannot. Admin → Users can add a user with a temporary
password.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time

from ..stores.repositories import _guard

_ITER = 200_000
_ALGO = "pbkdf2_sha256"

# roles → the retrieval scopes they carry (ACL before ranking). Askers see
# public; curators/admins/leads also see restricted; agents see public.
_ROLE_SCOPES = {
    "asker": ["public"],
    "agent": ["public"],
    "curator": ["public", "restricted"],
    "admin": ["public", "restricted"],
}


def scopes_for(roles: list[str]) -> list[str]:
    out: list[str] = []
    for r in roles:
        for s in _ROLE_SCOPES.get(r, ["public"]):
            if s not in out:
                out.append(s)
    return out or ["public"]


def hash_password(password: str, *, iterations: int = _ITER, salt: bytes | None = None) -> str:
    """``pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>`` — a self-describing string."""
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${dk.hex()}"


def verify_password(stored: str, password: str) -> bool:
    """Constant-time verify of ``password`` against a stored PBKDF2 string."""
    try:
        algo, iters, salt_hex, hash_hex = str(stored).split("$", 3)
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


class UserStore:
    def __init__(self, db):
        self.db = db

    def _row(self, r) -> dict:
        return {
            "email": r["email"],
            "tenant": r["tenant"],
            "roles": json.loads(r["roles"] or "[]"),
            "scopes": json.loads(r["scopes"] or "[]"),
            "designation": r["designation"] or "",
            "status": r["status"] or "active",
            "created_at": r["created_at"],
        }

    def get(self, email: str) -> dict | None:
        r = self.db.one("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),))
        return self._row(r) if r else None

    def create(
        self,
        email: str,
        password: str,
        roles: list[str],
        *,
        tenant: str = "qualizeal",
        scopes: list[str] | None = None,
        designation: str = "",
        status: str = "active",
    ) -> dict:
        """Create or replace a user. Scopes default to those the roles carry."""
        _guard(tenant)
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise ValueError("a valid email is required")
        roles = list(roles or ["asker"])
        scopes = list(scopes or scopes_for(roles))
        self.db.execute(
            """INSERT INTO users(email,tenant,roles,scopes,designation,password_hash,status,
               created_at) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(email) DO UPDATE SET tenant=excluded.tenant, roles=excluded.roles,
               scopes=excluded.scopes, designation=excluded.designation,
               password_hash=excluded.password_hash, status=excluded.status""",
            (
                email,
                tenant,
                json.dumps(roles),
                json.dumps(scopes),
                designation,
                hash_password(password),
                status,
                int(time.time() * 1000),
            ),
        )
        return self.get(email)

    def set_password(self, email: str, password: str) -> None:
        self.db.execute(
            "UPDATE users SET password_hash=? WHERE email=?",
            (hash_password(password), (email or "").strip().lower()),
        )

    def verify(self, email: str, password: str) -> dict | None:
        """Return the user dict on a correct password + active status, else None."""
        email = (email or "").strip().lower()
        r = self.db.one("SELECT * FROM users WHERE email=?", (email,))
        if not r or (r["status"] or "active") != "active":
            return None
        return self._row(r) if verify_password(r["password_hash"] or "", password) else None

    def list(self, tenant: str | None = None) -> list[dict]:
        if tenant:
            rows = self.db.query("SELECT * FROM users WHERE tenant=? ORDER BY email", (tenant,))
        else:
            rows = self.db.query("SELECT * FROM users ORDER BY email")
        return [self._row(r) for r in rows]

    def count(self) -> int:
        r = self.db.one("SELECT COUNT(*) c FROM users")
        return int(r["c"]) if r else 0


def seed_from_env(
    store: UserStore, *, tenant: str = "qualizeal", env: str = "KF_SEED_USERS"
) -> int:
    """Seed users from the ``KF_SEED_USERS`` secret — a JSON list of
    ``{email, password, roles, designation}``. Returns the number seeded (0 when
    the secret is absent, so a keyless/dev run needs no seed)."""
    raw = os.environ.get(env, "").strip()
    if not raw:
        return 0
    try:
        users = json.loads(raw)
    except ValueError:
        return 0
    n = 0
    for u in users if isinstance(users, list) else []:
        email, pw = (u.get("email") or "").strip(), u.get("password") or ""
        if not email or not pw:
            continue
        store.create(
            email,
            pw,
            list(u.get("roles") or ["asker"]),
            tenant=tenant,
            designation=u.get("designation") or "",
        )
        n += 1
    return n
