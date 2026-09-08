# Functionality checklist (Build Plan Section 20) → where it lives & its test

Legend: ✅ implemented + automated test · 🟡 implemented, partial coverage ·
⬜ scaffolded / documented path only (see "Scope" in the README).

## Ingestion & connectors
| Item | Where | Status |
|---|---|---|
| Drop-folder, upload API, CLI intake — one canonical record + event | `ingestion/intake.py`, `cli.py`, `surfaces/http_api.py` | ✅ `test_ingestion.test_three_intake_doors_same_canonical_record` |
| Durable transactional queue, retries + dead-letter | `adapters/queue.py` | ✅ `test_queue_durable_retry_then_deadletter` |
| 7-step pipeline (detect·convert·chunk·extract·fuse·embed·health) | `ingestion/pipeline.py` | ✅ `test_seven_step_pipeline_produces_passages` |
| Every modality (pdf/office/md, scans+bbox, tables→cells, a/v→timestamp, code→symbol/line) | `adapters/converter.py` | ✅ `test_all_modalities_have_resolvable_coordinates` |
| Idempotent-by-hash; incremental supersede/tombstone/version; no re-embed on provider swap | `ingestion/pipeline.py`, `adapters/embedder.py` | ✅ `test_idempotent_by_hash`, `test_incremental_supersede_on_change`, `test_tombstone_on_source_deletion` |
| Connectors read-only, scoped, change-detecting, tombstoning | `connectors/{base,files,github}.py` | ✅ `test_connectors.py` (GitHub, Files). Jira/Xray/Figma/Confluence/Drive/email/chat/transcripts: ⬜ SDK-additive |
| Connector SDK/template — new source is additive | `connectors/base.py` | ✅ |

## Stores & data model
| Item | Where | Status |
|---|---|---|
| Documents, passages(+coord,+abstract/overview), originals-by-hash, graph, ontology, jobs, health, spans, governance, eval, curation | `stores/db.py`, `stores/repositories.py` | ✅ |
| Tenant filter at store layer (guaranteed filter); isolation test | `stores/repositories.py` `_guard` | ✅ `test_store_call_without_tenant_fails_closed`, `test_tenant_isolation_in_retrieval` |
| Vector/lexical/graph tenant-partitioned & versioned | `adapters/{vectorindex,lexicalindex,graphstore}.py` | ✅ |

## Answer service (Graph + RAG)
| Item | Where | Status |
|---|---|---|
| Permission filter injected INSIDE the query | `adapters/vectorindex.py`, `adapters/lexicalindex.py` | ✅ `test_restricted_doc_never_enters_retrieval` |
| Hybrid retrieval fused with RRF; tiered; MMR | `answer/service.py` `_rrf`, `_mmr` | ✅ `test_hybrid_rrf_fuses_both_lists` |
| Graph expansion (connected/cross-doc evidence) | `answer/service.py` `_graph_expand` | ✅ `test_multi_document_citations` |
| Grounding gate: 5 signals, weighted geometric mean, per-tenant threshold | `answer/service.py` `_grounding` | ✅ `test_grounding_gate_five_signals` |
| Clarify-back on weak/ambiguous (first-class metric) | `answer/service.py` | ✅ `test_unsupported_question_refuses` + metrics `clarify_back_rate` |
| Model router: needed-or-not, fast/deep/escalation, budget-capped | `answer/service.py` `_route` | ✅ `test_budget_cap_atomic_under_race` |
| Compose from passages only; citation post-check drops unsupported; inline citations + confidence | `answer/service.py` `_compose`, `_postcheck` | ✅ `test_post_check_drops_unsupported_sentences` |
| Replayable trajectory id | `answer/service.py` (span attrs) | ✅ `test_trajectory_id_present_and_traced` |
| Core answers with model disabled | `adapters/model.py` `DisabledModelClient` | ✅ `test_model_off_still_returns_cited_answer` |

## Knowledge graph & ontology
| Item | Where | Status |
|---|---|---|
| Per-domain ontology packs, versioned; typed extraction | `ontology/packs.py`, `ingestion/extract.py` | ✅ |
| Concept nodes + dual edge weights (stated + co-occurrence-gated) | `ingestion/pipeline.py` `_fuse_graph` | ✅ |
| Entity resolution, edge fusion, contradiction flagging (no auto-resolve) | `ingestion/pipeline.py` | 🟡 pattern-based |
| Salience uses domain-salient vocab, not raw frequency | `ingestion/extract.py` `salience` | ✅ |

## Governance
| Item | Where | Status |
|---|---|---|
| Identity users **and** per-agent; roles/scopes/policies | `adapters/identity.py`, `governance/policy.py` | ✅ `test_agent_uses_same_gate_no_bypass` |
| Tenant isolation; permission-before-ranking | see above | ✅ |
| PolicyEngine (Allow/Deny/Clarify) + budget in one place | `governance/policy.py` | ✅ `test_role_gating` |
| Per-tenant/agent budget caps enforced by router; never silently exceeded | `governance/policy.py` `try_spend` | ✅ `test_budget_cap_atomic_under_race` |
| Immutable audit line on every answer/ingest/admin action | `stores/repositories.py` `AuditRepo` | ✅ `test_every_answer_audited` |
| Secrets via env/secret store, nothing in code | `adapters/*` (env) | ✅ |

## Surfaces
| Item | Where | Status |
|---|---|---|
| Ask: streamed cited answers, confidence, click-to-source, clarify-back, gaps, multi-language | `surfaces/http_api.py` | 🟡 cited answers + confidence + clarify + gaps + Ask console (streaming is chunkable; i18n input accepted) |
| Curator console: gaps, stale/contradiction, review queue, promote/retire, source health | `surfaces/http_api.py` `/curator/*`, `health/metrics.py` | 🟡 read views + risk register |
| Admin console: policy, identity, budget+spend, audit search, connector/model config | `surfaces/http_api.py` `/admin/*` | 🟡 audit + budget |
| Agent MCP tool server: per-agent identity, same gate/policy/budget, no bypass, traced+audited | `surfaces/agent_mcp.py` | ✅ |

## Telemetry & economics
| Item | Where | Status |
|---|---|---|
| One trace per answer and per ingest job | `adapters/telemetry.py` | ✅ `test_one_trace_per_answer` |
| Cost accounting by tenant × stage × model tier | `adapters/telemetry.py` `metrics` | ✅ |
| `/metrics` API | `surfaces/http_api.py` | ✅ |
| Dashboard suite panels | `adapters/telemetry.py` `metrics` (data behind panels) | 🟡 data + JSON; charts not shipped |

## Evaluation & health
| Item | Where | Status |
|---|---|---|
| Question bank (measured, thresholded, multi-doc, families) | `tenants/demo.py`, `evaluation/gate.py` | ✅ `test_question_bank_passes_gate` |
| Promotion gate: pass + no-regression; blocks on corrupt citation | `evaluation/gate.py` | ✅ `test_corrupted_citation_blocks_promotion` |
| Knowledge health: coverage, connectedness, traceability, currency, contradictions, gaps + risk register | `health/metrics.py` | ✅ `test_health_snapshot_and_risk_register` |

## Deployment & portability
| Item | Where | Status |
|---|---|---|
| Local (compose) / interim / client (IaC module) | `deploy/` | 🟡 local compose + Dockerfile runnable; interim/client declared as modules |
| App identical across shapes; only config + infra differ | contracts + adapters | ✅ by construction |
| Runbooks per shape; clean bring-up in minutes | `docs/RUNBOOK.md`, `make up` | ✅ |

## Multi-tenancy & genericity
| Item | Where | Status |
|---|---|---|
| Tenant = config + ontology pack + source set; zero customer-specific code | `tenants/demo.py`, `ontology/packs.py` | ✅ |
| Synthetic demo tenants across verticals; identifier-safety fails on real-resolvable id | `tenants/demo.py` `validate_identifiers` | ✅ `test_identifier_safety_*` |
| Generic-by-default (no client identity/vendor/financials) | demo corpora | ✅ |
