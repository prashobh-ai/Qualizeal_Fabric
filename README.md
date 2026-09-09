# QualiZeal Knowledge Fabric

A generic, multi-tenant, deployment-portable **knowledge-fabric platform** — a
governed layer that turns an organisation's own content into answers that carry
their source, reachable by people, agents and applications through **one door**.

This repository is a working, executed implementation of the
[End-to-End Build Plan](docs/) with its 14 product invariants (I1–I14) and the
[Run/Test/Demo Runbook](docs/RUNBOOK.md). It runs on a laptop with **zero
third-party packages** (Python standard library only) and demonstrates the
things enterprises pay for — identity, tenant isolation, permission-aware
retrieval, budget caps and a full audit trail — before any cloud exists.

> **Scope of this build.** The full spine is implemented, tested and executed
> end-to-end: ingestion from files/GitHub/Jira with continuous refresh, the
> governed answer path with multistep/conditional reasoning and a 4-level
> multi-model selector, data versioning and authoritative-source policy, the
> evaluation promotion gate, knowledge-base evaluation, the Ask / Curator /
> Admin / Dashboard surfaces, the **MCP agent tool**, and AWS-parity adapters
> + IaC. Remaining connectors (Confluence/SharePoint/Drive/email/chat/
> transcripts) are **additive** against the connector SDK. See
> [`docs/CHECKLIST.md`](docs/CHECKLIST.md) and
> [`docs/ROADMAP_ALIGNMENT.md`](docs/ROADMAP_ALIGNMENT.md) for item-by-item
> status mapped to tests. Nothing here is a stub pretending to work.

## Quickstart (under a minute, no dependencies)

```bash
make test            # 61 tests mapped to Build Plan Section 20 + roadmap WS1/WS2/WS3
make demo            # narrated end-to-end execution of every invariant + capability
make serve           # Ask console (/) + telemetry dashboard (/dashboard) + JSON API
make dashboard       # build a self-contained dashboard snapshot with real seeded data
make doctor TARGET=aws   # AWS deployment-readiness report (exact gap list)
make load-corpus DIR=<folder-of-docx>   # ingest QualiZeal's own .docx corpus
```

## Stage 2 — leadership review list

Covered and executed (`make demo` §15–21; map in [`docs/ROADMAP_ALIGNMENT.md`](docs/ROADMAP_ALIGNMENT.md)):
**multistep & conditional reasoning** (decomposed, every step governed) · **query complexity simple/medium/complex
→ multi-model routing** with model used + tokens in/out on every answer · **authoritative source** (ranks, curator
marks, conflicts) · **data versioning** (history / diff / rollback / dataset versions / lineage) · **continuous
refresh** (schedules, delta-only, SLA health) · **Curator console** (`/curator`: KB evaluation suggests
keep/review/delete with reasons, data-quality dashboard, history/rollback) distinct from the **Admin console**
(`/admin`: connectors + permissions + health, live pipeline runs, bulk upload/delete, budgets, users, audit, AWS
readiness) · **AWS deployment readiness** (IaC module, cloud adapters, `make doctor TARGET=aws`) · **approved
open-source stack** with the LangSmith/LangFuse decision (not needed) in `docs/OBSERVABILITY_DECISION.md`.

## QualiZeal Core Build Roadmap coverage (QZ-KF-BLD-001)

Aligned to the leadership roadmap's answer chain **Connect → Understand → Decide → Answer**
and its three workstreams — see [`docs/ROADMAP_ALIGNMENT.md`](docs/ROADMAP_ALIGNMENT.md) for the
capability-by-capability map. The three headline "things to show":

1. **Automated multi-source ingestion** — GitHub + Jira (storyboard) connectors and a bulk-upload
   door for admin/curator, all emitting one canonical record through the 7-step pipeline; read-only,
   allow-listed, change-detecting, tombstoning; a connector **registry** makes new sources plug-and-play.
2. **Power BI-style telemetry dashboard** at `/dashboard` — tabs for Overview / Trust / Sources /
   Models / Usage / Cost & Caching, filterable by **last 24h / last 7d / all**, **user**, and **role**;
   shows **which model ran when and why** (the 4-level selector's reason codes), tokens in/out, cost,
   and **cost saved by cache technique**.
3. **User-based access control from day one** — real signed-JWT identity, roles/scopes,
   permission-before-ranking, per-agent identity, and per-tenant + per-user budget caps.

Or drive it directly:

```bash
python -m knowledge_fabric.cli seed
python -m knowledge_fabric.cli ask acme-assurance asha.asker "what must a release achieve before promotion?"
python -m knowledge_fabric.cli eval acme-assurance
```

## What the execution shows (`make demo`)

1. **Seed** synthetic, identifier-safe demo tenants across verticals.
2. **Grounded answer** cited to exact coordinates — page/¶, audio `@timestamp`,
   table `cell[r,c]` — fused from multiple documents.
3. **Honest refusal**: an out-of-corpus question returns a declared gap, never a
   guess (I3).
4. **Idempotency** (I9): re-ingesting identical content is a no-op.
5. **Tenant isolation** (I5): a store call without a tenant fails closed.
6. **Permission-before-ranking** (I6): a restricted document never enters an
   asker's retrieval set — verified in the *retrieval trace*, not just the answer.
7. **The model is a dial** (I4): disable the model, the core still answers
   extractively with citations.
8. **Agent parity** (I7): an agent uses the *same* gate and is audited.
9. **Budget cap under a 100-way race** (I12): atomic, never exceeded.
10. **Promotion gate** (I10): a corrupted citation coordinate **blocks** going
    live; the clean index passes.
11. **Economics & health**: one trace per answer, cost by tier & stage, and a
    per-tenant risk register.

## Architecture

```
DATA SOURCES → INTAKE → 7-STEP PIPELINE → STORES → ANSWER SERVICE (Graph + RAG) → SURFACES
                                                        │ every step emits a span ▼
                                                     TELEMETRY & ECONOMICS
```

Everything programs against the **frozen contracts** in
[`knowledge_fabric/contracts/`](knowledge_fabric/contracts/) (Section 4).
Concrete engines live in [`adapters/`](knowledge_fabric/adapters/) and are
swapped local↔cloud by configuration only — application code never learns which
deployment shape it runs in.

| Layer | Module |
|---|---|
| Contracts (frozen core) | `contracts/` |
| Local adapters (SQLite, filesystem, hashing embedder, mock/hosted/off model, HS256 IdP) | `adapters/` |
| Tenant-guarded stores | `stores/` |
| 7-step ingestion pipeline | `ingestion/` |
| Governed answer path (RRF · MMR · graph · grounding gate · clarify · post-check) | `answer/service.py` |
| 4-level model selector with explainable "why" | `answer/selector.py` |
| Five cache layers + savings-by-technique ledger | `answer/cache.py` |
| Multilingual detect/translate EN·FR·ES·JA | `answer/lang.py` |
| Identity / policy / budget / audit | `governance/` |
| Ontology packs + typed extraction | `ontology/`, `ingestion/extract.py` |
| Connectors (SDK + registry: GitHub · Jira · Files) + auto-sync | `connectors/`, `ingestion/sync.py` |
| Ask · Curator · Admin · **Dashboard** · **Agent (MCP)** surfaces | `surfaces/` |
| Telemetry, cost accounting, `/metrics`, `/api/analytics` | `adapters/telemetry.py` |
| Evaluation harness + promotion gate | `evaluation/` |
| Knowledge health + risk register | `health/` |
| Synthetic demo tenants + identifier-safety | `tenants/` |
| Deploy: compose / Dockerfile / client IaC | `deploy/` |

## Identity for the demo

A local OIDC-style provider issues **real HS256-signed, expiring JWTs** (see
[ADR-0001](docs/decisions/ADR-0001-local-stdlib-adapters.md)). Moving to
enterprise SSO is pointing the same `Principal` adapter at the corporate IdP —
a config change, zero code change. The demo users:

| tenant | user | roles | may see |
|---|---|---|---|
| acme-assurance | `asha.asker` | asker | public |
| acme-assurance | `carl.curator` | curator | public + restricted |
| acme-assurance | `adar.admin` | admin | everything |
| acme-assurance | `rana.restricted` | asker | public only |
| acme-assurance | `qa-agent` | agent | public (service principal) |

## Docs
- [`docs/ROADMAP_ALIGNMENT.md`](docs/ROADMAP_ALIGNMENT.md) — QualiZeal Core Build Roadmap coverage map.
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — run/test/demo, mapped to the provided runbook.
- [`docs/CHECKLIST.md`](docs/CHECKLIST.md) — Section 20 item-by-item status + tests.
- [`docs/licences.md`](docs/licences.md) — licence posture (I14).
- [`docs/decisions/`](docs/decisions/) — ADRs.
