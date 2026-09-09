# Decision: do we need LangSmith or LangFuse for LLM observability?

**Decision: No.** Neither is needed. The platform's own telemetry spine already records, per
answer, everything those products would show us, it is exportable in the OpenTelemetry wire
format today (proved by `knowledge_fabric/adapters/otel_export.py` and its tests), and the two
LangFuse features we do not replicate (a prompt-versioning UI and human-annotation queues) are
covered by the question bank, the curator queue and the curation-decision ledger. We standardise
on **OpenTelemetry (Apache-2.0)** as the export protocol, keep our **built-in dashboard** as the
primary surface, and treat Jaeger (Apache-2.0) or Grafana + Tempo (AGPL-3.0, external service
only) as optional backends. Status: **accepted** · Owner: platform · Revisit triggers at the end.

## 1. The question and the licences

| Product | Licence | Deployment | Cost model |
|---|---|---|---|
| **LangSmith** | Proprietary (service); Python SDK MIT | SaaS (self-host only on enterprise contract) | Per seat + per trace ingested/retained |
| **LangFuse** | MIT (core); `ee/` directory and Cloud under a commercial licence | Self-host (Postgres + ClickHouse + Redis + S3/MinIO) or Cloud | Free self-host; Cloud tiered |
| **OpenTelemetry** (Collector, protocol, semantic conventions) | Apache-2.0 | Self-host collector; any OTLP backend | Free |
| Jaeger v2 | Apache-2.0 | Self-host | Free |
| Prometheus | Apache-2.0 | Self-host | Free |
| Grafana / Grafana Tempo | AGPL-3.0 | Self-host as an **external service** (never linked) | Free |
| SigNoz | MIT core (+ EE) | Self-host | Free |

LangSmith fails the "approved open-source stack" bar outright (proprietary) and would move
per-user answer traces — subject, roles, question text, cited passages — out of the tenant
boundary into a third-party SaaS. LangFuse passes the licence bar in its core, so the question
for it is purely: *would it add something we do not already have?*

## 2. Evidence: what this codebase already records

Every call to `AnswerService.ask` (`knowledge_fabric/answer/service.py`) opens **one trace
per answer** on `SqlTelemetry` (`knowledge_fabric/adapters/telemetry.py`). The root span
`answer` and its children `answer.retrieve` → `answer.graph` → `answer.ground` →
`answer.compose` are persisted in the `spans` table (`knowledge_fabric/stores/db.py`). The
columns are the leadership questions, verbatim:

| Leadership question | Column(s) on `spans` | Written at (`answer/service.py`) | Exposed by |
|---|---|---|---|
| Which model ran, at which tier? | `tier`, `model_name`, `level` | `span.set(kind="answer", tier=…, model_name=…, level=decision["level_name"])` | `/api/analytics.models_used`, `routing_by_tier`, Ask card "model used" |
| **Why** that model? | `why` (JSON: `level_name`, `explain`, `reasons[{code, detail, signal}]`) | `why=decision` from the 4-level selector | `routing_reasons` (reason-code counts), why-card on the Ask page, dashboard *Models* tab |
| Tokens in / out and cost | `tokens_in`, `tokens_out`, `tokens`, `cost` | `tokens_in=tin, tokens_out=tout, cost=cost` | `tokens_in`, `tokens_out`, `total_cost`, `cost_by_tier`, `cost_by_stage`, per-bucket `timeseries` |
| Cache hit and savings by technique | `cache_hit`, `cache_technique`, `cost_saved` | `cache_hit=1, cache_technique="answer_cache" / "prompt_memory", cost_saved=saved` | `cache_hit_rate`, `total_cost_saved`, `savings_by_technique`, dashboard *Cost & Caching* tab |
| Per user, per role | `subject`, `roles` | from the `Principal` (real signed JWT) | `per_user`, `per_role`, `?subject=` / `?role=` filters on `/api/analytics` |
| Is the answer trustworthy? | `grounding`, `citations_count`, `attrs.signals` (five grounding signals) | `_grounding()` → `span.set(grounding=g, signals=signals)` | `grounding_avg`, `citation_coverage`, `clarify_back_rate`, dashboard *Trust* tab |
| Complexity, language, sources | `complexity`, `lang`, `sources` | `complexity=complexity, lang=qlang, sources=sources` | `routing_by_complexity`, `by_language`, *Sources* tab |
| Multistep / conditional reasoning | `reasoning` (JSON plan + per-step results) | `reasoning=surface` in the reasoning path | Ask card "Reasoning steps" timeline |
| Which data was answered against? | `dataset_version`; `attrs.trajectory.selected` (passage ids) | `dataset_version=dsv, trajectory=traj` | Replay via `GET /api/trace?trace_id=<traj_…>` and `versioning.lineage` |
| Latency per stage | `duration_ms` per child span | `_Span.__exit__` | `latency_p50_ms` / `latency_p95_ms`; per-stage waterfall in any OTLP UI |
| Ingestion pipeline stages | `ingest`, `ingest.detect` … `ingest.health` spans, mirrored into `ingest_runs.steps` | pipeline + `ingestion/runs.step_from_span` | Admin "Pipeline runs" panel |

Concretely, one seeded answer (`tests/test_stage2_licence.py::OtlpExportTests`) yields a root
span whose exported attributes are `kf.model.tier=deep`, `gen_ai.response.model=kf-mock-mid`,
`gen_ai.usage.input_tokens=40`, `gen_ai.usage.output_tokens=33`, `kf.cost.usd=0.000365`,
`kf.selector.level=reason`, `kf.selector.why.reasons=[factual_lookup, multi_document]`,
`kf.selector.why.explain="Routed to the deep tier — this needs reasoning across evidence across 4
documents (grounding 0.69)"`, `user.id=asker.public`, `user.roles=[asker]`, `kf.complexity=complex`,
`kf.grounding.score=0.6924` plus the five `kf.grounding.signal.*` values, `kf.citations.count=3`,
`kf.dataset.version=3`, `kf.sources=[…]`, `kf.trajectory.selected=[pas_…]` — with four child spans
carrying `parentSpanId` of the root. That *is* an LLM-observability trace.

Where it is already visible without any new product:

- **`/dashboard`** (`knowledge_fabric/surfaces/dashboard.py`): tabs Overview / Trust / Sources /
  Models / Usage / Cost & Caching, filters 24h / 7d / all, user, role — inline SVG, zero deps.
- **`/api/analytics`** (`SqlTelemetry.analytics`) — the JSON behind the dashboard, also
  Power BI-consumable.
- **`/metrics`** (`SqlTelemetry.metrics`) — cost by tier & stage, p50/p95, grounding, clarify rate.
- **`/api/trace?trace_id=`** — the full span list of one answer for replay.
- **Audit line per answer** (`AuditRepo`) — who asked what, allowed/denied, cost.

## 3. Evidence: it is OpenTelemetry-compatible today

`knowledge_fabric/adapters/otel_export.py` maps `spans` rows to **OTLP/JSON** (`resourceSpans`
→ `scopeSpans` → `spans`) using the proto3 JSON mapping every OTLP receiver accepts — the
OpenTelemetry Collector, Jaeger v2, Tempo, SigNoz, and vendor back-ends — and POSTs it with
`urllib` (no SDK, no third-party package):

```
to_otlp(spans: list[dict]) -> dict                     # {"resourceSpans": [...]}
export(platform, tenant, endpoint_url | None) -> dict  # POST to KF_OTLP_ENDPOINT/v1/traces, else return payload
export_trace(platform, tenant, trace_id) -> dict       # one answer's trace
```

Mapping choices: `traceId` is the answer's trajectory id (so an OTLP trace joins back to
`/api/trace`), span ids are derived deterministically (exports are idempotent), token counts use
the GenAI semantic conventions (`gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`,
`gen_ai.response.model`), user identity uses `user.id` / `user.roles`, and everything
platform-specific is namespaced `kf.*`. Tenant is a **resource attribute**, and every store read
goes through the tenant guard — a multi-tenant batch never mixes tenants in one resource.

The tests prove the shape (32-hex `traceId`, 16-hex `spanId`, string-encoded nanosecond
timestamps, `AnyValue` attributes, parent links, per-tenant isolation) **and** the transport: a
loopback HTTP receiver in the test receives the POST and parses the body as OTLP/JSON.
Configuration is one env var — `KF_OTLP_ENDPOINT` — already wired in `deploy/aws/variables.tf`
and the readiness doctor; unset means spans simply stay in the store.

## 4. What we would lose versus LangFuse / LangSmith, and how it is covered

| Feature they offer | Do we have it? | How it is covered here |
|---|---|---|
| Trace per LLM call with tokens, cost, latency, model | **Yes** | `spans` + `otel_export` (Section 2–3). |
| Cost dashboards by user / model / time | **Yes** | `/dashboard` Usage + Cost & Caching tabs; `per_user`, `per_role`, `timeseries`. |
| Cache-savings accounting | **Yes** (they don't) | `savings_by_technique` — five-layer cache ledger (`answer/cache.py`). |
| "Why this model" routing reasons | **Yes** (they don't) | `why.reasons[].code` — the 4-level selector explains itself. |
| Grounding / hallucination score per answer | **Yes** | Five-signal grounding gate; citation post-check drops unsupported sentences (I1, I2). |
| Prompt management UI (versioned prompts, A/B) | **No UI** | Prompts are code (`answer/service.py::_compose`, extractive by design) and versioned in git; behaviour changes are gated by the evaluation promotion gate. If prompt experimentation becomes a product need, a `prompts` table + admin page is a small addition — not a reason to buy a platform. |
| Human annotation / review queues | **Different shape** | `curation_queue` + `curation_decisions` (keep / delete / authoritative / rollback, by subject, with reason) and `kb_eval.document_quality` suggestions — annotation at the *knowledge* level, which is where a grounded system needs review. Answer-level thumbs-up/down can be one extra column on `spans`. |
| Datasets + evals runner | **Yes** | `question_bank` per tenant + `evaluation` promotion gate (`eval_runs`), `cli eval`. |
| Playground | **Partial** | `make ask Q=…` / Ask console with the full answer card (model, tokens, why, reasoning steps). |
| Trace UI with waterfall | **Via OTLP** | Jaeger (Apache-2.0) or Grafana Tempo (AGPL, external) fed by the collector; `/api/trace` for raw replay. |
| Alerts | **Via OTLP/Prometheus** | Collector → Prometheus alerting on `/metrics`; `health.metrics.risk_register` already flags SLA breaches and budget burn. |

Net: what LangFuse adds is a nicer *generic* UI for two workflows we have in domain-specific
form, at the price of running ClickHouse + Postgres + Redis + object storage next to the
platform and duplicating the `spans` table. LangSmith adds the same on a proprietary licence with
tenant data egress. Neither adds a signal we do not already record.

## 5. Recommended observability stack

1. **Keep** the telemetry spine (`spans`, `/dashboard`, `/api/analytics`, `/metrics`, `/api/trace`)
   as the system of record — it is tenant-filtered, audited and tested.
2. **Export** with `KF_OTLP_ENDPOINT` to an **OpenTelemetry Collector** (Apache-2.0) when a
   deployment wants traces outside the app. The collector fans out to whatever the customer
   already runs; we do not care which.
3. **Optional UIs**: Jaeger (Apache-2.0) is the default permissive choice; Grafana + Tempo
   (AGPL-3.0) is acceptable as an external service; SigNoz (MIT core) if one box must do
   traces + metrics. Prometheus (Apache-2.0) scrapes `/metrics`.
4. **Do not** add the OpenTelemetry Python SDK as a runtime dependency: the stdlib exporter
   keeps I14 (licence-clean, zero third-party packages) and the exact one-trace-per-answer
   semantics we test.

Cost: zero licence spend; operational cost is one collector container when enabled.

## 6. Revisit triggers

Re-open this decision if any of these becomes a product requirement: (a) non-engineers must
edit and A/B prompts through a UI; (b) answer-level human rating queues with reviewer
assignment and SLAs; (c) a customer mandates a specific LLM-observability vendor. Even then the
route is *LangFuse self-hosted (MIT) fed from our OTLP export*, never LangSmith, and never a
change to the application's recording path.

Related: `docs/TECH_STACK.md`, `ci/licence_manifest.json`, `scripts/licence_gate.py`,
`docs/ROADMAP_ALIGNMENT.md` (WS3 Prove).
