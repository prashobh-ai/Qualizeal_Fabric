# Dependency licences (Invariant I14 — licence-clean)

Shipped code links only permissively licensed dependencies. Copyleft projects
may inform design; their code is **not** imported (native reimplementation
only). Model weights carry their own licence, cleared per deployment.

## Runtime dependencies

| Dependency | Version | Licence | Notes |
|---|---|---|---|
| Python standard library | 3.11 | PSF (permissive) | The **only** runtime dependency. |

The platform runs with **zero third-party packages**: storage (SQLite),
vector/lexical search, the deterministic embedder, HS256 identity tokens, the
HTTP surfaces and the MCP server are all built on the standard library. This
is the strongest possible licence posture and also what makes a clean
laptop/GPU bring-up trivial (Runbook Section 2).

## Cloud adapters (optional, chosen per deployment)

When swapping local adapters for managed engines, each must be permissive:

| Concern | Suggested engine | Licence |
|---|---|---|
| Relational + vector | PostgreSQL + pgvector | PostgreSQL Licence / PostgreSQL Licence (permissive) |
| Object store | MinIO (AGPL) or S3-class | If AGPL: run as an external service, do **not** import its code. |
| Queue | Managed queue (SQS-class) | vendor |
| Identity | Keycloak (Apache-2.0) / Dex (Apache-2.0) | Apache-2.0 |
| Embeddings | bge-m3-class | model licence, cleared per deployment |
| LLM | hosted API / vLLM (Apache-2.0) | Apache-2.0 + model weights cleared |

**CI gate:** `make ci` fails on a non-permissive *code* addition. External
services reached over the network (e.g. MinIO/AGPL) are allowed because their
code is not linked into shipped artefacts.
