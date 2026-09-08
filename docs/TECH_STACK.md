# Technology stack — approved open-source components (invariant I14)

**Ask from leadership:** use only an approved open-source stack. **Answer:** the shipped
process has exactly one runtime dependency — the Python 3.11 standard library (PSF-2.0). Every
engine we may point it at is listed below with its licence, whether it is approved, and why.
The list is enforced, not aspirational: `ci/licence_manifest.json` is the machine-readable
version of this page and `scripts/licence_gate.py` fails `make ci` on any runtime import that
is not declared there and permissively licensed.

## Principles

1. **Runtime = permissive only.** Code imported into the shipped Python process must be PSF,
   MIT, Apache-2.0, BSD, ISC or PostgreSQL-licensed. Today that set is empty beyond the stdlib.
2. **Copyleft is an external service, never a link.** AGPL projects (MinIO, Grafana, Tempo) are
   acceptable when we talk to them over the network (S3 API, OTLP) and never import, vendor or
   ship their code. Running them unmodified as a service triggers no AGPL obligation on us.
3. **Weak copyleft is fine for tooling.** MPL-2.0 (OpenTofu) is file-level copyleft: we run
   it at deploy time, we do not modify or redistribute it. Tooling never ships in the image.
4. **Source-available is not open source.** SSPL / BUSL / Elastic-2.0 (Elasticsearch,
   Terraform) and proprietary SaaS (LangSmith) are never approved; they appear in the manifest
   only as *rejected* so the decision is on record and the gate refuses their imports.
5. **Application code never learns which shape it runs in.** Local ↔ AWS ↔ client is an env
   change (`KF_DB_URL`, `KF_OBJECTSTORE`, `KF_QUEUE`, `KF_MODEL_BASE_URL`, `KF_OTLP_ENDPOINT`),
   never a code change (ADR-0001, `docs/AWS_READINESS.md`).

## Component table

Linkage: **runtime** = imported into the process · **optional** = guarded import selected by
env · **service** = reached over the network, code never linked · **tooling** = build/deploy
time only · **managed** = cloud-provider service behind an open protocol.

| Layer | Component (local default → cloud option) | Licence | Linkage | Approved | Why |
|---|---|---|---|---|---|
| Runtime | Python 3.11 standard library | PSF-2.0 | runtime | **yes** | The only runtime dependency (`deploy/Dockerfile`: `python:3.11-slim`, no `pip install`). |
| Relational store | SQLite (bundled `sqlite3`) → PostgreSQL (RDS) | Public domain (SQLite blessing) / PostgreSQL Licence | runtime / service | **yes** | Tenant filter enforced in `stores/repositories.py::_guard` on both. |
| DB driver (cloud) | pg8000 | BSD-3-Clause | optional | **yes** | Pure Python; chosen over psycopg (LGPL-3.0) to keep runtime permissive-only. |
| Vector index | SQLite vector table → pgvector | Public domain / PostgreSQL Licence | runtime / service | **yes** | Same `VectorIndex` contract; embeddings never recomputed on swap (I9). |
| Lexical index | SQLite lexical index → Postgres full-text or OpenSearch | Public domain / PostgreSQL / Apache-2.0 | runtime / service | **yes** | Elasticsearch (SSPL/Elastic-2.0) **rejected**. |
| Graph store | SQLite tables (`graph_nodes`, `graph_edges`) → Postgres | Public domain / PostgreSQL | runtime / service | **yes** | Concept graph with dual edge weights; no graph DB dependency. |
| Object store | Filesystem → Amazon S3 or MinIO | PSF / AWS terms / **AGPL-3.0** | runtime / managed / service | **yes** (MinIO service-only) | Content-hash addressed originals. MinIO's AGPL code is never linked. |
| Queue | SQLite durable queue → Amazon SQS | Public domain / AWS terms | runtime / managed | **yes** | Retries + dead-letter in both shapes (`deploy/aws/main.tf`). |
| S3/SQS client | boto3 / botocore | Apache-2.0 | optional | **yes** | Guarded import; `CloudNotReady` when absent (`adapters/cloud.py`). |
| Identity | Local HS256 IdP (`hmac`) → Keycloak, Dex, or Cognito | PSF / Apache-2.0 / Apache-2.0 / AWS terms | runtime / service / managed | **yes** | Real signed JWTs locally; enterprise SSO is a JWKS URL change. |
| Embeddings | Deterministic hashing embedder → bge-m3 (BAAI) | PSF / **MIT** (weights) | runtime / model-weights | **yes** | Weights cleared per deployment; only permissive weights pre-approved. |
| LLM serving | Mock / OpenAI-compatible endpoint → vLLM with open weights | PSF / Apache-2.0 | runtime / service | **yes** | Model is a dial (I4): the core answers extractively with the model off. |
| Converter | DoclingLite (native) → Docling | PSF / MIT | runtime / optional | **yes** | Binary PDF/scan support plugs behind the same `DocumentConverter` contract. |
| Cache | In-process five-layer cache (`answer/cache.py`) | PSF | runtime | **yes** | No Redis (RSALv2/SSPL/AGPL); Valkey (BSD-3) is the approved route if a shared cache is ever needed. |
| Telemetry | `spans` table + `adapters/telemetry.py`; OTLP/JSON export via `urllib` | PSF | runtime | **yes** | One trace per answer; see `docs/OBSERVABILITY_DECISION.md`. |
| Observability backend | OpenTelemetry Collector → Jaeger / Prometheus (optional Grafana + Tempo) | Apache-2.0 / Apache-2.0 / Apache-2.0 / **AGPL-3.0** | service | **yes** (Grafana/Tempo service-only) | We emit the OTLP wire format, never link the SDK. LangSmith (proprietary) and LangFuse (MIT core, commercial cloud) **not needed**. |
| Dashboards | Built-in Power BI-style `/dashboard` (inline SVG) | PSF | runtime | **yes** | Zero charting libraries; Grafana optional alongside, never instead. |
| Container | Docker Engine (Moby) + `python:3.11-slim` | Apache-2.0 / PSF + Debian DFSG | tooling | **yes** | One image for local / interim / client. |
| IaC | OpenTofu (`deploy/aws/*.tf`) | **MPL-2.0** | tooling | **yes** | Weak copyleft acceptable for tooling. Terraform (BUSL-1.1) **rejected**. |
| CI | `make ci` (GNU Make) on any Python 3.11 runner (e.g. GitHub Actions) | GPL-3.0-or-later (run, not shipped) / GitHub terms | tooling / managed | **yes** | unittest + `scripts/licence_gate.py` + identifier-safety. |
| Tests | `unittest` (stdlib) | PSF-2.0 | runtime | **yes** | No pytest, no plugins. |

## Rejected alternatives (recorded so the gate can refuse them)

| Component | Licence | Why not |
|---|---|---|
| LangSmith | Proprietary SaaS (SDK MIT) | Licensed, per-seat/per-trace pricing, trace data leaves the tenant boundary; our spine already records everything it would show. |
| LangFuse | MIT core; `ee/` + Cloud commercial | Licence-acceptable but redundant with `spans` + `/dashboard` + curator queue; adds ClickHouse + Postgres + Redis + S3 to operate. |
| LangChain / LangGraph | MIT | Not a licence problem — we keep planner/selector/grounding as deterministic, testable stdlib code so the "why" stays visible. |
| Elasticsearch | SSPL-1.0 / Elastic-2.0 / AGPL-3.0 | Source-available primary licence; OpenSearch (Apache-2.0) is the approved substitute. |
| Terraform ≥ 1.6 | BUSL-1.1 | Source-available; OpenTofu applies the same HCL. |
| psycopg | LGPL-3.0 | Weak copyleft *in the runtime*; pg8000 (BSD) instead. |
| Redis ≥ 7.4 | RSALv2 / SSPL (8.x adds AGPL) | Not needed (in-process cache); Valkey (BSD-3-Clause) if it ever is. |

## How the gate enforces this

```
make ci   →   python3 scripts/licence_gate.py        # exit 1 on any violation
```

The gate (a) validates the manifest, (b) checks every component's licence class against what its
linkage allows, (c) parses every `import` under `knowledge_fabric/` and `scripts/` with `ast` and
fails on any non-stdlib import that is undeclared, rejected, or an `optional-runtime` import
that is not wrapped in `try/except ImportError`, and (d) checks any requirements file that
appears. Test: `tests/test_stage2_licence.py`. Current result: **PASS — third-party imports in
shipped code: boto3, pg8000 (both guarded, both permissive)**.

## Env mapping (same image, different engines)

| Concern | Local | AWS / client |
|---|---|---|
| Store | `KF_DB=./data/kf.db` | `KF_DB_URL=postgres://…` (Secrets Manager) |
| Objects | `KF_BLOBS=./data/blobs` | `KF_OBJECTSTORE=s3` + bucket |
| Queue | in-process SQLite | `KF_QUEUE=sqs` + queue URL |
| Model | `KF_MODEL_MODE=mock\|hosted\|off` | `KF_MODEL_BASE_URL` → vLLM / hosted, key in Secrets Manager |
| Identity | `KF_IDP_SECRET` (HS256) | OIDC issuer (Keycloak / Cognito) |
| Traces | stay in `spans` | `KF_OTLP_ENDPOINT=http://otel-collector:4318` |

See `docs/licences.md` (short posture statement), `docs/decisions/ADR-0001-local-stdlib-adapters.md`
and `docs/OBSERVABILITY_DECISION.md`.
