# Third-party notices — QualiZeal Knowledge Fabric - licence manifest (invariant I14: licence-clean)

This product includes third-party software and services listed below. It is
generated from `ci/licence_manifest.json` by `scripts/notices.py`; edit the
manifest, then run `make notices`. Every component is used under its own
licence, named against each entry.

## Runtime — linked into the shipped process

| Component | Licence | Role |
|---|---|---|
| FastAPI | MIT License | HTTP application framework (T05, api/main.py) |
| Pydantic | MIT License | contract/validation models (T04, contracts/types.py) |
| Python standard library | Python Software Foundation License 2.0 | runtime |
| SQLite (bundled in Python sqlite3) | SQLite Blessing (public domain) | store / vector / lexical / graph / queue (local) |
| unittest (stdlib) + our own dashboard/surfaces | Python Software Foundation License 2.0 | test framework + dashboards |
| Uvicorn | BSD 3-Clause License | ASGI server (runtime, api entrypoint) |

## Optional runtime — guarded imports, selected by environment

| Component | Licence | Role |
|---|---|---|
| atlassian-python-api | Apache License 2.0 | Confluence/Jira client for connectors (P2.x) |
| boto3 / botocore | Apache License 2.0 | object store + queue client (AWS shape) |
| cryptography | Apache License 2.0 (dual-licensed with BSD-3-Clause) | asymmetric signature primitives used by PyJWT for RS256/ES256 |
| Docling | MIT License | document converter (PDF/Office/OCR) |
| docling | MIT License | real DocumentConverter adapter for PDF/office/OCR (P2.7) |
| faster-whisper | MIT License | ASR (speech-to-text) engine (P8.1) |
| google-api-python-client | Apache License 2.0 | Google Drive connector client (P2.x) |
| kokoro | Apache License 2.0 | TTS (text-to-speech) engine for en/fr/es/ja (P8.1) |
| lingua-language-detector | Apache License 2.0 | high-accuracy language detection (P3.4) |
| LiteLLM | MIT License | provider gateway (T08, providers/, guarded) |
| MCP Python SDK | MIT License | MCP server runtime (T20, mcp/, guarded) |
| msal | MIT License | Azure/Entra auth for the SharePoint connector (P2.x) |
| numpy | BSD 3-Clause License | audio buffer arithmetic for the speech loop (P8) |
| openai | Apache License 2.0 | provider gateway OpenAI backend (P4.5) |
| opentelemetry-api | Apache License 2.0 | real Telemetry SDK adapter (P5.1) |
| opentelemetry-exporter-otlp-proto-http | Apache License 2.0 | OTLP/HTTP exporter |
| opentelemetry-instrumentation | Apache License 2.0 | OTel auto-instrumentation base |
| opentelemetry-sdk | Apache License 2.0 | OTel SDK runtime |
| opentelemetry-semantic-conventions | Apache License 2.0 | OTel GenAI semantic conventions |
| pg8000 | BSD 3-Clause | PostgreSQL driver (AWS shape) |
| PyJWT | MIT License | OIDC/JWKS token verification (cloud identity adapter, KF_IDENTITY=oidc) |
| sentence-transformers | Apache License 2.0 | real embedder engine behind the Embedder contract (P3.2) |
| silero-vad | MIT License | voice-activity detection (P8.1) |
| soundfile | BSD 3-Clause License | audio I/O for the speech loop (P8) |
| spacy | MIT License | named-entity recognition adapter (P3.5) |
| tiktoken | MIT License | provider-accurate tokenisation for pricing/limits (P4.5) |
| torch | PyTorch BSD-3-Clause | tensor backend used by sentence-transformers and faster-whisper |
| transformers | Apache License 2.0 | HuggingFace transformers used by embedder / NLLB (P3.4) |

## Model weights — downloaded artefacts

| Component | Licence | Role |
|---|---|---|
| bge-m3 (BAAI) | MIT License | embedding model weights |

## Tooling — run at build or CI time, never shipped

| Component | Licence | Role |
|---|---|---|
| Docker Engine (Moby) + python:3.11-slim image | Apache License 2.0 (Moby); image contents PSF-2.0 + Debian DFSG-free packages | container build/runtime |
| GNU Make | GNU General Public License v3.0 or later | CI entrypoint (make ci) |
| hatchling | MIT License | build backend (build-system.requires) |
| HTTPX | BSD 3-Clause License | test HTTP client (dev / CI only) |
| mypy | MIT License | type checker (dev / CI only) |
| OpenTofu | Mozilla Public License 2.0 | infrastructure as code |
| pip-audit | Apache License 2.0 | dependency vulnerability audit (dev / CI only) |
| pytest | MIT License | test runner (dev / CI only) |
| pytest-asyncio | Apache License 2.0 | async test support (dev / CI only) |
| ruff | MIT License | linter (dev / CI only) |

## External services — reached over the network, code never linked

| Component | Licence | Role |
|---|---|---|
| Dex | Apache License 2.0 | identity provider (OIDC, lightweight alternative) |
| Grafana | GNU Affero General Public License v3.0 | dashboards (optional, alongside /dashboard) |
| Grafana Tempo | GNU Affero General Public License v3.0 | trace backend (optional) |
| Jaeger | Apache License 2.0 | trace backend / UI |
| Keycloak | Apache License 2.0 | identity provider (OIDC) |
| MinIO | GNU Affero General Public License v3.0 | object store (self-hosted S3-class) |
| OpenSearch | Apache License 2.0 | lexical index (approved alternative) |
| OpenTelemetry Collector (OTLP/HTTP receiver) | Apache License 2.0 | observability pipeline |
| pgvector | PostgreSQL License | vector index (cloud shape) |
| PostgreSQL | PostgreSQL License | relational store (cloud shape) |
| Prometheus | Apache License 2.0 | metrics backend |
| PyGithub | GNU Lesser General Public License v3.0 or later | GitHub connector client (P2.5) |
| vLLM | Apache License 2.0 | LLM serving (self-hosted, OpenAI-compatible) |

## Managed services — cloud services behind an open protocol

| Component | Licence | Role |
|---|---|---|
| Amazon S3 / SQS / RDS / ECS / Secrets Manager / CloudWatch / Cognito | AWS Customer Agreement (service, not software) | managed infrastructure (AWS shape) |
| GitHub Actions runners | GitHub Terms of Service (service, not software) | CI execution |

## Brand assets — first-party

| Component | Licence | Role |
|---|---|---|
| QualiZeal brand kit (logo, mark, favicons, watermark) | QualiZeal-owned — first-party brand assets, the deck template's own files | first-party brand assets shipped in the UI (L0.1) |
