# QualiZeal KF Core Build Roadmap — coverage map

Maps the leadership-agreed roadmap (QZ-KF-BLD-001) and the three "main things
to show" to what is implemented, executed and tested in this repository.
Answer chain: **Connect → Understand → Decide → Answer**, proved every step.

## The three "main things to show"

### 1. Automated data ingestion from many sources (GitHub, Jira storyboard, bulk upload)
- **Connector SDK + registry** (plug-and-play): `connectors/base.py`, `connectors/registry.py`
  (`files`, `github`, `jira`; additive — a new source touches nothing downstream).
- **GitHub** connector: `connectors/github.py` — repos/code/PRs/issues, read-only,
  allow-listed, change-detecting, tombstoning. Tested: `test_ws_connect`, `test_connectors`.
- **Jira storyboard** connector: `connectors/jira.py` — projects/issues/status, read-only,
  allow-listed, change-detecting, tombstoning. Tested: `test_ws_connect`.
- **Automated sync runner**: `ingestion/sync.py` — cursor persistence, delta-only pulls,
  tombstone on delete, source freshness. Tested: `test_ws_connect` (incremental + tombstone).
- **Bulk upload** for admin/curator: `POST /admin/upload` (`surfaces/http_api.py`), plus the
  drop-folder and CLI doors. **Word (.docx)** supported: `adapters/converter.py`.
- **Real QualiZeal corpus** loader: `scripts/load_qualizeal_corpus.py` (48 docs → 3,041 passages).

### 2. Detailed telemetry dashboard (Power BI-style), with which model ran when & why
- **Dashboard**: `surfaces/dashboard.py` served at `/dashboard` — QualiZeal-branded, tabbed
  (Overview · Trust · Sources · Models · Usage · Cost & Caching), offline SVG charts.
- **Model routing WITH the why**: the 4-level selector records level + tier + reason codes +
  a plain-language why per answer (`answer/selector.py`); the Models tab shows routing by
  level/tier and the reason-code breakdown (query-complexity classification).
- **Analytics API**: `GET /api/analytics` (`adapters/telemetry.py::analytics`). Tested:
  `test_ws_prove`.

### 3. User-based access control from the start + all the dashboard filters
- **Access control**: real HS256 JWT identity, roles/scopes, permission-before-ranking,
  per-user + per-agent identity — from day one (`adapters/identity.py`, `governance/policy.py`).
  The same Ask UI serves everyone; what each user retrieves and sees is gated.
- **Filters** (all present in `/api/analytics` + dashboard): window **last 24h / last 7d / all**,
  **per user**, **per user role**; **tokens in/out**, **model routing with reason**,
  **cost**, **cost saved by caching** (savings by technique). Tested: `test_ws_prove`.

### Modularity / plug-and-play across GitHub, standalone, AWS
- Everything sits behind the Section-4 contracts; connectors behind the registry; engines behind
  adapters. Local ↔ interim ↔ AWS is config + the IaC module only (`deploy/`), never app code.

## Roadmap workstreams

| Roadmap (PPTX) | Where | Tests |
|---|---|---|
| **WS1 Connect** — 6 sources → one canonical record, permissions carried in, idempotent-by-hash, source freshness | `connectors/*`, `ingestion/sync.py`, `ingestion/pipeline.py` | `test_ws_connect`, `test_ingestion` |
| **WS2 Understand** — extract w/ positions, graph+vector+lexical fused, EN·FR·ES·JA | `ingestion/*`, `answer/service.py`, `answer/lang.py` | `test_answer`, `test_ws_decide` |
| **WS2 Decide** — 4 levels (look-up→reason), decision uses no AI, escalate on confidence fail, gateway over providers | `answer/selector.py`, `adapters/model.py` | `test_ws_decide` |
| **WS3 Prove — telemetry & caching** — one trace/answer (tier, tokens, cost, why), five cache layers incl. prompt memory, savings by technique, 24h/7d | `adapters/telemetry.py`, `answer/cache.py` | `test_ws_prove` |
| **WS3 Prove — surfaces** — Ask (why-card), Admin (policy/identity/spend caps), Dashboard (trust/sources/models/usage) | `surfaces/*` | live HTTP smoke |
| **Budget cap per tenant & per user from day one** | `governance/policy.py` | `test_governance` |
| **AWS parity / hardening** | `deploy/` (Dockerfile, compose, client IaC) | — |

## Gates (roadmap G0–G5)
- **G0 contracts + first record** ✅ · **G1 sources live** ✅ (files/github/jira; others additive)
- **G2 languages answer, cited** ✅ (EN/FR/ES/JA, cited to English source)
- **G3 selector + providers visible** ✅ (level + why on every trace; provider gateway via `ModelClient`)
- **G4 caching + dashboard live** ✅ (`/dashboard`, savings by technique)
- **G5 go/no-go evidence** ✅ (eval promotion gate blocks a corrupted index; `make demo` is the evidence run)

## Cache layers (roadmap: "five cache layers, incl. the model's own prompt memory")
1. answer cache · 2. retrieval cache · 3. embedding cache · 4. graph-expansion cache ·
5. provider prompt-memory discount — all in `answer/cache.py`; savings attributed per technique.

---

# Stage 2 — leadership review list (Rasool) → coverage map

| # | Ask | Where | How it is shown |
|---|---|---|---|
| 1 | **AWS deployment readiness** | `deploy/aws/*.tf` (VPC · ECS Fargate · ALB · RDS pgvector · S3 · SQS · Secrets Manager · CloudWatch · IAM), `adapters/cloud.py` (S3/SQS/Postgres adapters behind the Section-4 contracts, guarded imports), env selection in `app.py`, `scripts/doctor.py` + `ops/readiness.py`, `docs/AWS_READINESS.md` | `make doctor TARGET=aws` prints the exact gap list; `GET /admin/doctor?target=aws` in the Admin console. Same image everywhere; only env differs. |
| 2 | **Data versioning** | `stores/versioning.py` — immutable document-version ledger written by the pipeline, `history`, `diff`, `rollback`, dataset versions (bumped per changed batch), passage `lineage` | Demo §18; Curator console → History → Rollback; every answer carries `dataset_version`. |
| 3 | **Continuous refresh** | `ingestion/scheduler.py` (per-source interval, `due`, `run_due`, error backoff, SLA breach, `RefreshLoop` thread), cursors in `ingestion/sync.py` | Demo §19; Admin console → connector card → interval + *Sync now*; `POST /admin/refresh/run-due`. Only the delta since the cursor is ingested. |
| 4 | **Which source is authoritative** | `governance/authority.py` — per-source ranks (files > confluence/sharepoint/upload > github/drive > jira), curator **Mark authoritative**, retrieval boost, `authoritative_source` card with reason, cross-source `conflicts` | Demo §17; Ask card shows the *Authoritative source* badge + conflicts; Curator console marks/unmarks; Admin sets ranks. |
| 5 | **Query complexity simple/medium/complex → multi-model** | `answer/reasoning.py::complexity` + selector tier → `complexity`; `adapters/model.py` multi-model gateway (named model per tier, env-overridable) | Demo §16; Ask card shows *complexity* + *model used* + *tokens in/out*; Dashboard → Models tab (routing by complexity, models used). |
| 6 | **Multistep & conditional reasoning** | `answer/reasoning.py` — deterministic planner (multistep / conditional / compare) + executor; every step runs through the governed `ask()` (`_nested`) so permissions, grounding, selector and budget apply per step; merged, renumbered citations; a gap in any step is declared | Demo §15; Ask card → collapsible *Reasoning steps* timeline with the condition's evaluated value and skipped branches. |
| 7 | **Approved open-source stack; LangSmith/LangFuse?** | `docs/TECH_STACK.md`, `docs/OBSERVABILITY_DECISION.md`, `ci/licence_manifest.json` + `scripts/licence_gate.py`, `adapters/otel_export.py` (OTLP/JSON) | Decision: **not needed** — our telemetry spine already records one trace per answer with tier, tokens in/out, cost, why, model, complexity, cache savings, per user/role, and exports OTLP (Apache-2.0). |
| 8 | **Demo flow**: trigger ingestion, bulk load, Q&A card with answer + confidence + citations + tokens + model | Admin → *Sync now* / *Bulk upload* (live 7-stage pipeline-run panel); Ask card | Demo §12–16; `make serve` then `/admin`, `/`. |
| 9 | **Curator ≠ Admin; suggest what to delete/keep; data-quality dashboard** | `health/kb_eval.py` (per-document score, keep/review/delete with reasons: duplicates, never cited, stale, low readability, contradictions; authoritative never deleted), `/curator/*` endpoints, `surfaces/curator_ui.py` | Demo §20; `/curator` — quality tiles + risk register, document table with suggestion badges + reasons, Keep / Delete / Mark authoritative / History+Rollback, single-doc add. No connector or bulk controls. |
| 10 | **Admin: bulk upload/delete, connector permissions, health monitoring** | `connectors/admin.py` (enable/disable, allow-lists, scopes), scheduler health (freshness, error count, next run, SLA), `POST /admin/bulk-delete`, `surfaces/admin_ui.py` | `/admin` — connector cards, pipeline runs, bulk upload/delete, budgets, users & roles, audit, AWS readiness. |
