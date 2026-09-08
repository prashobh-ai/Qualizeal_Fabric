# Run, Test & Demo Runbook (pre-cloud)

Companion to the Build Plan; mirrors the provided *KF Run/Test/Demo Runbook*.
A laptop is a complete, self-contained deployment. When cloud arrives it is a
configuration change, not new work.

## The one principle
Run **real identity, real access control, real budget enforcement locally** —
issued by a local provider. This build ships an in-process local IdP that
mints **real HS256-signed JWTs** (`adapters/identity.py`). Swapping in a
Keycloak/Dex container (or corporate SSO) is pointing the same `Principal`
adapter at its JWKS — zero code change.

## 0. Bring-up (the 5-minute test)
```bash
git clone <repo> && cd Qualizeal_Fabric
make health        # DB ok; model available
make up            # seed demo tenants + serve on :8080  (Ask console at /)
```
Clean, repeatable, in seconds — no external services (SQLite + filesystem).
`make demo-reset` returns to a clean seeded state (Runbook 10.1).

## 3. Seed & functional smoke
```bash
make seed          # synthetic tenants; identifier-safety validated (fails on any real-resolvable id)
make demo          # golden path: ingest → cited answer → honest refusal → idempotency → trace
make test          # automated suite mapped to Section 20 checklist
```

## 4. Access control (real, no cloud IAM)
- **Permission-before-ranking (I6):** `test_governance.test_restricted_doc_never_enters_retrieval`
  asserts the forbidden document id never appears in the *retrieval set*.
- **Tenant isolation (I5):** `test_tenant_isolation_in_retrieval`, and a
  store call without a tenant fails closed.
- **Role gating:** `test_role_gating` (asker cannot set budget; admin can) and
  HTTP `403` on `/curator/*` and `/admin/*` for the wrong role.
- **Per-agent identity (I7):** `test_agent_uses_same_gate_no_bypass`.
- **Audit line (I11):** `test_every_answer_audited`.

## 5. Sessions & concurrency correctness
- **Footgun A (leaked tenant on pooled connections):** the store never binds a
  tenant to a connection; tenant is a query parameter on every statement
  (`stores/db.py`, `stores/repositories.py`). `test_concurrency.test_mixed_tenant_no_leakage_and_unique_traces`
  fires 60 interleaved mixed-tenant requests and asserts zero cross-tenant
  citations and unique trace ids.
- **Footgun B (request context in a global):** the `Principal` is passed per
  call, never module-global. `test_audit_subject_matches_requester_under_load`
  fires 40 concurrent users and asserts each audit row names *its* requester.

## 6. LLM usage & budget enforcement
- Test with the **mock model** (`KF_MODEL_MODE=mock`), verify with a real one
  (`KF_MODEL_MODE=hosted` + `KF_MODEL_BASE_URL`/`KF_MODEL_API_KEY`).
- **Budget race (I12):** `try_spend` is an atomic conditional decrement;
  `test_budget_cap_atomic_under_race` fires 100 concurrent 1-unit spends against
  a cap of 10 and asserts exactly 10 succeed, spend never exceeds the cap.
- **Routing observable:** cost by tier & stage in `/metrics`.
- **Rate limiting:** `test_rate_limit_blocks_flood`.
- **Model-off is the cheapest tier:** `test_model_off_still_returns_cited_answer`.

## 8. Resilience & data-integrity
- **Worker-kill mid-ingest:** `test_worker_kill_lease_expiry_reclaims_job` (lease
  expiry → re-lease → complete).
- **Queue durability + dead-letter:** `test_queue_durable_retry_then_deadletter`.
- **Duplicate storm:** `test_duplicate_storm_processes_once` (idempotent-by-hash).
- **Corrupt-index gate proof:** `test_corrupted_citation_blocks_promotion`.

## 9. Pre-cloud security
- No secrets in code — all via env (`KF_MODEL_API_KEY`, `KF_IDP_SECRET`).
- Connectors read-only + allow-listed (`test_connectors`).
- Tokens validated (signature, expiry, audience) every request
  (`test_expired_token_rejected`, `test_forged_token_rejected`).
- Put TLS in front for a real demo (reverse proxy / tunnel).

## 12. What changes when cloud/GPU arrives
- **GPU:** swap `Embedder` + `ModelClient` to self-hosted adapters. Config change.
- **SSO:** point the OIDC `Principal` adapter at the corporate IdP. Config change.
- **Cloud storage/queue/DB:** switch adapters via the infra module. Config + IaC.
Because each lives behind a Section 4 contract, none is a rewrite.
