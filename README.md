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

> **Scope of this build.** The Release-1 spine is fully implemented, tested and
> executed end-to-end, plus the Release-2/3 elements that prove the platform
> thesis: graph expansion, ontology packs, incrementality, the evaluation
> promotion gate, knowledge-health, the Curator/Admin read surfaces, the
> **MCP agent tool**, per-agent identity, budget caps, and the connector SDK
> (GitHub + Files). Remaining connectors (Jira/Xray/Figma/Confluence/Drive/
> email/chat/transcripts) are **additive** against the connector SDK, and the
> dashboard *data* ships (JSON) without the charting UI. See
> [`docs/CHECKLIST.md`](docs/CHECKLIST.md) for the item-by-item status mapped to
> tests. Nothing here is a stub pretending to work: every ✅ has a passing test.

## Quickstart (under a minute, no dependencies)

```bash
make test            # 41 tests mapped to Build Plan Section 20
make demo            # narrated end-to-end execution of every invariant
make serve           # Ask console + JSON API at http://localhost:8080/
```

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
| Governed answer path (RRF · MMR · graph · grounding gate · clarify · router · post-check) | `answer/` |
| Identity / policy / budget / audit | `governance/` |
| Ontology packs + typed extraction | `ontology/`, `ingestion/extract.py` |
| Connectors (SDK + GitHub + Files) | `connectors/` |
| Ask / Curator / Admin / **Agent (MCP)** surfaces | `surfaces/` |
| Telemetry, cost accounting, `/metrics` | `telemetry/`, `adapters/telemetry.py` |
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
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — run/test/demo, mapped to the provided runbook.
- [`docs/CHECKLIST.md`](docs/CHECKLIST.md) — Section 20 item-by-item status + tests.
- [`docs/licences.md`](docs/licences.md) — licence posture (I14).
- [`docs/decisions/`](docs/decisions/) — ADRs.
