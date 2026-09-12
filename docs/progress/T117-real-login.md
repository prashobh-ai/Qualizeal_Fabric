# T117 — Real login (password now, OIDC/OAuth2-ready)

## Problem

The sign-in page accepted any `{tenant, subject}` and minted a token — an
open door. `donkey@qualizeal.com` (or any typed string) could sign in and
land on a role. The company needs a real front door: passwords today, and a
drop-in path to corporate SSO (Entra / Okta / Google) when the IdP is wired.

## What shipped

### 1. A credential store — PBKDF2, stdlib only

`knowledge_fabric/auth/users.py`

- `hash_password` / `verify_password` — PBKDF2-HMAC-SHA256, per-user 16-byte
  salt, 200k iterations, self-describing string `pbkdf2_sha256$iter$salt$hash`.
  Verify is constant-time (`hmac.compare_digest`). No external crypto dep.
- `UserStore(db)` over a new `users` table (`stores/db.py`): `get`, `create`
  (email lowercased, `ON CONFLICT` upsert, scopes default from roles),
  `set_password`, `verify` (correct password **and** `status == active`),
  `list`, `count`.
- `scopes_for(roles)` — the ACL each role carries (asker/agent → public;
  curator/admin → public + restricted).
- `seed_from_env` — seeds users from the **`KF_SEED_USERS`** secret (a JSON
  list of `{email, password, roles, designation}`). Returns 0 when absent, so
  a keyless dev run needs no seed. Only seeded/added accounts can sign in —
  `donkey@` cannot. Wired best-effort in `tenants/demo.py::seed()`.

### 2. Auth endpoints

`knowledge_fabric/surfaces/http_api.py`

- **`POST /api/auth/login {email, password}`** → verify the PBKDF2 hash, mint
  the platform's internal JWT for the user's real roles/scopes/designation.
  Unknown user or wrong password → **401** (never a free entry).
- **`GET /api/auth/config`** (public) → `{mode, sso:{enabled,label}, dev_login}`
  — tells the sign-in page whether to show password, an SSO button, or the dev
  picker.
- **`GET /api/auth/whoami`** → resolves the bearer token to
  `{subject, roles, scopes, designation, tenant}` (used after an SSO redirect).
- The old passwordless **`POST /login {tenant, subject}`** is now **DEV-ONLY**,
  gated behind **`KF_DEV_LOGIN=1`** → **403** otherwise. The deployed build has
  no passwordless entry.
- **Admin → Users**: a payload with `email` creates a real credential user; a
  temporary password is generated (`secrets.token_urlsafe`) when none is given
  and returned exactly once so the admin can hand it over.

### 3. OIDC / OAuth2 (drop-in SSO)

`knowledge_fabric/auth/oidc.py` + `GET /api/auth/sso/login` & `/callback`

- Authorization-code-with-PKCE (S256). Endpoints discovered from
  `<issuer>/.well-known/openid-configuration` (or `OIDC_AUTH_URL`/`OIDC_TOKEN_URL`
  overrides). Config from `OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET /
  OIDC_REDIRECT_URI / OIDC_GROUP_CLAIM`.
- `/sso/login` → 302 to the IdP (state + PKCE verifier held in-process).
  `/sso/callback` → exchange code, validate the `id_token`
  (issuer/audience/signature via the IdP JWKS, reusing
  `adapters.identity.OIDCIdentity`), map the verified email + groups to roles,
  mint the same internal JWT, redirect to `/signin#token=…`.
- Group → role mapping is data (`DEFAULT_GROUP_MAP`, overridable via
  `OIDC_GROUP_MAP`). Entra / Okta / Google are all OIDC-compliant, so one flow
  covers them.
- The token exchange goes through an **injectable transport**, so the flow is
  unit-tested offline (no network).

### 4. Sign-in page

`assets/signin_ui__{body.html,js.js}` + `ui_common__runtime_js.js`

- Email + password form. On load it reads `/api/auth/config`: shows the SSO
  button when OIDC is configured, and the dev picker only when `dev_login`.
- An SSO redirect lands back with the token in the URL fragment; `ssoFromHash`
  stores it and calls `whoami` so roles/nav are correct.
- Runtime helpers: `authLogin`, `whoami`, `authConfig`, `ssoFromHash`.

### 5. Static showcase parity

`scripts/showcase/engine.js` gained `/api/auth/login`, `/api/auth/config`
(reports `dev_login:true` — no OIDC on Pages), and `/api/auth/whoami` shims so
the static demo sign-in keeps working against the baked snapshot.

## Secrets (environment only, never in the repo)

`KF_SEED_USERS`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
`OIDC_REDIRECT_URI`, `OIDC_GROUP_CLAIM`, `OIDC_GROUP_MAP`. `KF_DEV_LOGIN=1`
enables the dev sign-in for local development and is set for the test process
by `tests/conftest.py`.

## Verification

- `tests/test_auth.py` (23 cases): PBKDF2 hash/verify + salting; UserStore
  create/verify/inactive/unknown; `seed_from_env`; OIDC `configured`, PKCE
  S256, authorize-URL params, code exchange via injected transport, group→role
  mapping and identity; `/api/auth/login` accepts seeded user, rejects wrong
  password and `donkey@` with 401; `/api/auth/whoami`; `/api/auth/config` shape;
  `/login` 200 with `KF_DEV_LOGIN=1` and 403 without.
- `ruff check` clean; full suite green; `build_showcase` + `parity_check` pass.

## Gate

- `donkey@qualizeal.com` → rejected (401).
- A seeded user logs in and gets their real role.
- An unknown password → 401.
- The deployed build has no passwordless entry (`/login` → 403 without
  `KF_DEV_LOGIN`).
