# Stage-2 integration contract (Rasool's list) — read fully before writing code

Repo: /home/user/Qualizeal_Fabric · Python 3.11 · **standard library ONLY** (no pip). Tests: `unittest`
(`PYTHONPATH=. python3 -m unittest tests.test_stage2_<area> -v`). All existing 61 tests must stay green.
Conventions: every store call takes `tenant` and filters by it (see `stores/repositories.py::_guard`);
`Platform` (`knowledge_fabric/app.py`) exposes `db`, `documents`, `passages`, `graph_repo`, `vindex`,
`lindex`, `embedder`, `telemetry`, `policy`, `audit`, `curation`, `cache`, `model`, `idp`, `objects`, `queue`.
Demo tenants/users: `knowledge_fabric/tenants/demo.py` (`seed`, `principal_for`). Answer path:
`knowledge_fabric/answer/service.py::AnswerService.ask(principal, question)` → `contracts/types.py::Answer`.

**Rule for parallel builders:** create NEW files only (module + `tests/test_stage2_<area>.py`). Do NOT edit
existing files. If you need a hook in an existing file, describe it precisely in your returned
`integration_notes` (file, function, 1–3 lines) — the integrator applies it.

## Schema already present (stores/db.py)
- `documents.authoritative INTEGER` · `spans.model_name, complexity, dataset_version, reasoning(TEXT json)`
- `dataset_versions(tenant, version, created_at, reason, doc_count, passage_count)`
- `document_versions(id, tenant, document_id, version, content_hash, created_at, passage_ids TEXT json, source_version)`
- `source_authority(tenant, source, rank, weight)`
- `refresh_schedules(tenant, source, interval_s, next_run, last_run, last_status, error_count, enabled, config TEXT json)`
- `connector_config(tenant, source, enabled, config TEXT json, allow TEXT json, scopes TEXT json, updated_at)`
- `curation_decisions(id, tenant, document_id, decision, reason, by_subject, at)`
- `ingest_runs(id, tenant, source, started_at, finished_at, status, steps TEXT json, items)`
Existing you may read: `passages` (id, tenant, document_id, text, coord_*, version, superseded_by, acl json),
`embeddings(tenant, passage_id, model_id, vec json)`, `spans` (…`sources` json list of doc titles, `attrs` json
with `trajectory.selected` passage ids), `connector_cursors`, `health_snapshots`, `curation_queue`, `audit_log`.

## Module contracts (one builder each)

### A. `knowledge_fabric/answer/reasoning.py` — multistep + conditional reasoning
```
plan(question: str) -> dict   # {"mode": "single"|"multistep"|"conditional"|"compare",
                              #  "steps": [{"id": "s1", "question": str, "kind": "lookup"|"condition"|"compare"|"synthesize",
                              #             "depends_on": [ids], "branch": {"if_true": id|None, "if_false": id|None} | None}],
                              #  "complexity": "simple"|"medium"|"complex", "signals": {...}}
execute(plan: dict, ask_fn) -> dict
   # ask_fn(sub_question:str) -> Answer (governed path). Returns
   # {"mode", "steps": [{"id","question","kind","answer_text","citations":[Citation],"grounding":float,
   #                     "kind_result": {"condition": True|False|None, ...}, "skipped": bool}],
   #  "final_text": str (composed ONLY from step answer_texts, each sentence keeps its [n] markers re-numbered),
   #  "citations": [Citation] (merged, de-duplicated by passage_id, renumbered),
   #  "confidence": float (min over executed steps × coverage), "complexity": str}
complexity(question: str, plan: dict|None=None) -> "simple"|"medium"|"complex"
```
Deterministic, rule-based (no model): split conjunctions ("and", "then", "after that"), detect conditionals
("if … then … (otherwise …)", "when X, does/what …"), comparisons ("compare A and B", "difference between",
"A vs B"). Condition evaluation: ask the condition sub-question; decide True/False from its answer_text via
negation/keyword heuristics + grounding ≥ threshold, else None → clarify. A step whose ask_fn returns kind
GAP/CLARIFY marks the whole result with that step's gap (final_text says which step lacked evidence).
`Answer` has fields kind (AnswerKind.ANSWER/CLARIFY/GAP), answer_text, citations (Citation: document_id,
document_title, coordinate, passage_id, snippet), grounding_score, confidence. Tests: plan shapes for ≥6
question forms, execute with a fake ask_fn (multistep merge + renumbered citations, conditional true/false
branches, compare), complexity labels.

### B. `knowledge_fabric/stores/versioning.py` + `knowledge_fabric/governance/authority.py`
versioning:
```
record_version(platform, tenant, document_id, version:int, content_hash, passage_ids:list[str], source_version) -> None
history(platform, tenant, document_id) -> [ {version, content_hash, created_at, passages:int, source_version} ]
diff(platform, tenant, document_id, v_from:int, v_to:int) -> {"added":[text...], "removed":[text...], "unchanged":int}
rollback(platform, tenant, document_id, to_version:int, by_subject:str) -> {"new_version": int, "reactivated": int}
   # re-activate the passages of `to_version` (clear superseded_by), supersede the currently live ones,
   # set documents.current_version = new_version (= max+1), record a document_versions row, audit it.
bump_dataset(platform, tenant, reason:str) -> int   # next dataset version; counts docs/passages
current_dataset(platform, tenant) -> int            # 0 if none
lineage(platform, tenant, passage_id) -> {"passage_id","document_id","content_hash","version","source","source_version","coordinate"}
```
authority:
```
DEFAULT_RANKS = {"files": 1, "confluence": 2, "sharepoint": 2, "github": 3, "jira": 4, "drive": 3, "upload": 2, "cli": 2}
set_source_rank(platform, tenant, source, rank:int) -> None            # rank 1 = most authoritative
weight_for(platform, tenant, source) -> float                          # 1.0 for rank1 decaying (e.g. 1/(1+0.25*(rank-1)))
mark_authoritative(platform, tenant, document_id, flag:bool, by_subject) -> None   # documents.authoritative + audit
is_authoritative(platform, tenant, document_id) -> bool
boost(platform, tenant, fused:list[tuple[passage_id, score]], passage_doc:dict[passage_id -> (document_id, source)]) -> list[tuple]
   # multiply score by weight_for(source) × (1.5 if document authoritative) and re-sort
authoritative_source(platform, tenant, citations:list[Citation]) -> {"document_id","document_title","source","reason"} | None
   # the top-ranked/authoritative doc among citations, with a one-line reason
conflicts(platform, tenant, citations) -> [ {"a": doc_id, "b": doc_id, "preferred": doc_id, "reason": str} ]
   # pairs of cited docs from different sources whose rank differs; preferred = higher authority
list_ranks(platform, tenant) -> [ {source, rank, weight} ]
```
Tests for all of the above with a seeded platform (`tests.util.seeded(["q-quality"])`).

### C. `knowledge_fabric/ingestion/scheduler.py` + `knowledge_fabric/ingestion/runs.py` + `knowledge_fabric/connectors/admin.py`
runs (pipeline progress for the UI):
```
start_run(platform, tenant, source) -> run_id
step(platform, run_id, name:str, status:"ok"|"error"|"skipped", count:int=0, ms:float=0.0) -> None  # appends to steps json
finish(platform, run_id, status:"ok"|"error", items:int) -> None
list_runs(platform, tenant, limit=20) -> [ {id, source, started_at, finished_at, status, steps:[...], items} ]
```
connector admin (permissions + enable/disable + allow-lists):
```
upsert(platform, tenant, source, enabled:bool|None=None, config:dict|None=None, allow:list|None=None, scopes:list|None=None) -> dict
get(platform, tenant, source) -> dict | None
list_all(platform, tenant) -> [ {source, enabled, config, allow, scopes, updated_at, registered:bool} ]  # registry ∪ configured
is_enabled(platform, tenant, source) -> bool  (default True if unconfigured)
```
scheduler (continuous refresh):
```
set_schedule(platform, tenant, source, interval_s:int, config:dict, enabled=True, now:float) -> dict
schedules(platform, tenant) -> [ {source, interval_s, next_run, last_run, last_status, error_count, enabled, config} ]
due(platform, tenant, now:float) -> [source...]
run_due(platform, tenant, now:float, records_by_source:dict|None=None) -> [summary...]
   # for each due+enabled source: SyncManager(platform).sync(...) inside a run (start_run/step/finish);
   # on exception: error_count+=1, last_status="error:<msg>", still advance next_run (backoff = interval × min(4, 1+error_count))
health(platform, tenant) -> [ {source, enabled, freshness_minutes, last_status, error_count, next_run, interval_s, items, sla_breach:bool} ]
   # sla_breach = freshness_minutes > 2 × interval_s/60
class RefreshLoop(platform, tenant, tick_s=5): start() / stop()  # daemon thread calling run_due(now=time.time())
```
`SyncManager` is `knowledge_fabric/ingestion/sync.py` (`sync(tenant, source, config, ontology, **kw)`; kw may carry `records=`).
Tests: schedule → due → run_due ingests via jira records → health freshness; disabled connector is skipped;
error path increments error_count and backs off; runs list shows steps.

### D. `knowledge_fabric/health/kb_eval.py` — knowledge-base evaluation → curator suggestions + data quality
```
citation_usage(platform, tenant) -> {document_id: int}     # count how often each doc was cited (spans.sources titles → doc ids via documents table; also attrs.trajectory.selected passage_ids → document_id)
duplicates(platform, tenant, threshold=0.92) -> [ {"passage_id","dup_of","document_id","dup_document_id","cosine"} ]   # cross-document only, using embeddings table + adapters.embedder.cosine
readability(text:str) -> float 0..1   # simple: sentence length + long-word ratio → clamp
document_quality(platform, tenant, now_ms:int|None=None) -> [ {document_id, title, source, uri, ingested_at, passages, authoritative,
      signals:{citation_uses, age_days, duplicate_passages, contradiction_flags, readability, coverage_contribution, orphan_ratio, gap_hits},
      score: 0..1, suggestion: "keep"|"review"|"delete", reasons:[str...]} ]
   # suggestion rules (transparent, printed as reasons): delete if duplicate_passages ≥ 50% of passages AND not authoritative;
   # delete if age_days > 365 AND citation_uses == 0 AND not authoritative; review if citation_uses == 0 OR readability < 0.35
   # OR contradiction_flags > 0; keep otherwise. Authoritative docs are never "delete" (at most "review").
data_quality(platform, tenant) -> {coverage, freshness, contradictions, gaps, connectedness, traceability, readability_avg,
      duplicate_rate, citation_coverage(docs cited ≥1 / docs), documents, passages, suggestions:{keep,review,delete}, risk_register:[...]}
   # reuse health.metrics.latest/risk_register
```
Tests with seeded platform after a few `AnswerService.ask` calls; inject a duplicate doc and assert it is flagged.

### E. `deploy/aws/*` + `knowledge_fabric/adapters/cloud.py` + `scripts/doctor.py` + `docs/AWS_READINESS.md`
- OpenTofu/Terraform module under `deploy/aws/` (main.tf, variables.tf, outputs.tf, README.md): VPC, ECS Fargate
  service for the single image, ALB, RDS Postgres (pgvector), S3 bucket (originals), SQS (ingest queue),
  Secrets Manager (model key, IdP secret), CloudWatch logs, IAM least-privilege task role; Cognito or external OIDC
  issuer variable. Parameterised by tenant_slug/region/model_mode. Must be syntactically valid HCL.
- `knowledge_fabric/adapters/cloud.py`: `S3ObjectStore`, `SqsQueue`, `PostgresNotice` adapters implementing the
  Section-4 contracts using **guarded imports** (`try: import boto3 except ImportError`) and raising
  `CloudNotReady("boto3 not installed; run: pip install boto3")` on use when missing; plus factories
  `build_objectstore(env:dict, local_factory)` / `build_queue(env, local_factory)` selecting by `KF_OBJECTSTORE`
  (`local`|`s3`) / `KF_QUEUE` (`local`|`sqs`) / `KF_DB_URL` (sqlite path | postgres://…). Unit-test the factory
  selection and the NotReady error without network.
- `scripts/doctor.py`: `python scripts/doctor.py --target local|aws` prints a readiness report (env vars present,
  adapters selected, image build file present, IaC files present, secrets not in repo (grep), health endpoint
  shape, tests green) and exits non-zero on blockers. Test the report generation.
- `docs/AWS_READINESS.md`: checklist (12-factor, config via env, stateless containers, health checks, secrets,
  IAM, logging/metrics export, scaling, DR/backups, cost guardrails) with status ✅/🟡/⬜ per item and the exact
  env-var mapping local→AWS. Application code must NOT learn which shape it runs in.

### F. `docs/TECH_STACK.md` + `docs/OBSERVABILITY_DECISION.md` + `ci/licence_manifest.json` + `scripts/licence_gate.py` + `knowledge_fabric/adapters/otel_export.py`
- TECH_STACK: every component (runtime, DB, vector, lexical, object store, queue, IdP, embeddings, LLM serving,
  converter, IaC, CI, dashboards) with licence + "approved: yes/no" + why; only OSI/permissive; call out
  copyleft (AGPL MinIO/Grafana) as external-service-only, and MPL (OpenTofu) as acceptable.
- OBSERVABILITY_DECISION: do we need LangSmith (proprietary, licensed) or LangFuse (MIT core; cloud commercial)?
  Answer with evidence from this codebase: our telemetry spine already gives one trace/answer with tier, tokens
  in/out, cost, why, cache savings, per user/role; it is OpenTelemetry-compatible; recommend OTel (Apache-2.0)
  + our dashboard, optionally Grafana/Tempo/Jaeger; list exactly what we'd lose vs LangFuse (prompt mgmt UI,
  human annotation queues) and how we cover it (question bank + curator queue). Decision: NOT needed.
- `adapters/otel_export.py`: `to_otlp(spans:list[dict]) -> dict` (OTLP/JSON resourceSpans shape) and
  `export(platform, tenant, endpoint_url|None)` that POSTs with urllib when an endpoint is configured
  (`KF_OTLP_ENDPOINT`), else returns the payload. Test the payload shape.
- `ci/licence_manifest.json` + `scripts/licence_gate.py`: manifest of allowed licences + component list; gate
  fails on any non-permissive runtime dependency; test it.

### G. Surfaces — `knowledge_fabric/surfaces/ask_ui.py`, `curator_ui.py`, `admin_ui.py` (HTML/JS strings, QualiZeal navy brand like `surfaces/dashboard.py`, zero external deps, inline SVG only)
Code against these endpoints (the integrator implements them in `surfaces/http_api.py`):
- `POST /login {tenant, subject}` → `{token, roles, scopes}`; all others send `Authorization: Bearer <token>`.
- **Ask** `POST /ask {question}` → Answer dict: `kind, answer_text, citations[{document_title, coordinate_render, snippet}],
  confidence, grounding_score, tier, level, why{level_name, explain, reasons[{code}]}, lang, cache_hit, cost_saved,
  tokens_in, tokens_out, cost, model_name, complexity, authoritative_source{document_title, source, reason}|null,
  dataset_version, reasoning{mode, steps[{id, question, kind, answer_text, grounding, condition}]}|null, trajectory_id`.
  The Ask page shows ALL of these as an "answer card": answer, confidence meter, citations (click expands snippet),
  why-card, **model used**, **tokens in/out**, complexity badge, language, cache/savings, authoritative source badge,
  dataset version, and a collapsible "Reasoning steps" timeline when reasoning is non-null. User picker via /login.
- **Curator** (`/curator`): `GET /curator/quality` → data_quality dict; `GET /curator/documents` → `{documents:[…document_quality item…]}`;
  `POST /curator/decision {document_id, decision: keep|delete|authoritative|not_authoritative|rollback, reason, to_version?}` → `{ok, …}`;
  `POST /curator/upload {files:[{filename, text}]}` → `{uploaded, ingested}`; `GET /curator/versions?document_id=` →
  `{history:[…], dataset_version}`; `GET /curator/gaps` (exists). Page: data-quality KPI tiles + risk register; document
  table with score, suggestion badge (keep/review/delete) + reasons, buttons Keep / Delete / Mark authoritative / History
  (shows versions + Rollback); "Add document" form. Curator sees NO connector/permission/bulk controls.
- **Admin** (`/admin`): `GET /admin/connectors` → `{connectors:[{source, enabled, allow, scopes, health{freshness_minutes,
  last_status, error_count, next_run, interval_s, items, sla_breach}}]}`; `POST /admin/connectors {source, enabled?, allow?,
  interval_s?}`; `POST /admin/sync {source}` (trigger ingestion) → `{…, run_id}`; `POST /admin/refresh/run-due` → `[…]`;
  `GET /admin/runs` → `{runs:[{id, source, status, started_at, finished_at, items, steps[{name,status,count,ms}]}]}`;
  `POST /admin/upload {files:[{filename,text,acl?}]}` → `{uploaded, ingested, run_id}`; `POST /admin/bulk-delete
  {document_ids?:[], source?, uri_prefix?}` → `{deleted}`; `GET /admin/audit`; `POST /admin/budget {cap}`; `GET /admin/users`
  → `{users:[{subject, roles, scopes}]}`; `GET /admin/doctor?target=aws` → readiness report. Page: connector cards with
  enable/disable toggle, allow-list edit, refresh interval, "Sync now", health badge (SLA breach red); a live "Pipeline runs"
  panel (poll /admin/runs every 2s while a run is active, show 7 steps with status); bulk upload (multi-file textarea/JSON),
  bulk delete (by ids / source / prefix) with confirm; budgets; users & roles; audit tail; AWS readiness panel.
Return the HTML strings as module constants `ASK_HTML`, `CURATOR_HTML`, `ADMIN_HTML`. Provide a tiny unittest that
imports them and asserts key element ids exist.

## Integrator (main session) will: wire A→`answer/service.py` (mode auto: plan → if multistep/conditional/compare
execute via ask_fn = self.ask on sub-questions with `_nested=True`), B→retrieval boost + answer.authoritative_source +
dataset_version, C→pipeline step instrumentation + sync run wrapping + scheduler endpoints, D→curator endpoints,
E→`app.py` adapter selection + doctor endpoint, F→CI, G→routes. Then run everything, extend `scripts/demo.py`, docs.
