# ADR-0001 — Local defaults are stdlib-only adapters behind the Section 4 contracts

## Status
Accepted.

## Context
The Build Plan requires deployment portability (I: portability) with concrete
engines swappable by configuration behind frozen contracts (Section 4). The
Runbook requires a clean laptop/GPU bring-up "in minutes" with no cloud, and
real (not stubbed) identity, access control and budget enforcement running
locally.

## Decision
The **local** adapter set is implemented entirely on the Python standard
library:

- **Store / Vector / Lexical / Graph / Queue** → SQLite (`stores/db.py`).
- **ObjectStore** → filesystem, content-hash addressed.
- **Embedder** → a deterministic hashing embedder (`kf-hash-embed-v1`),
  satisfying I9 (embeddings never recomputed on a provider swap) with no GPU
  or network.
- **ModelClient** → mock / hosted (OpenAI-compatible via env) / disabled.
- **Identity** → a local OIDC-style IdP issuing **real HS256-signed JWTs**
  (`hmac`), because the environment's `cryptography` build is broken and
  because HS256 is sufficient for the local demo; moving to enterprise SSO is
  pointing the same adapter at the corporate JWKS.

Cloud adapters (Postgres+pgvector, S3-class object store, managed queue,
Keycloak/Dex OIDC, bge-m3 + vLLM) implement the identical contracts and are
selected by `deploy/` + env.

## Consequences
- The whole platform runs, is tested and is demoed with **zero third-party
  packages** — best-case licence posture (I14) and bring-up speed.
- The hashing embedder trades absolute retrieval quality for determinism and
  portability; the grounding threshold is calibrated to it and is per-tenant
  configurable, so a bge-m3 swap only shifts the threshold, not the design.
- `eval` is renamed `evaluation` as a Python package to avoid shadowing the
  builtin.
