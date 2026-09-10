# AWS deployment readiness (Build Plan Section 17)

**Principle:** one container image runs in every shape — laptop, interim host,
client AWS account. Application code (`answer/`, `ingestion/`, `surfaces/`,
`governance/`) never learns which shape it is in. `knowledge_fabric/app.py`
asks the factories in `adapters/cloud.py` for an object store, a queue and a
database, and the factories choose **by environment only**.

Run the doctor for the truth at any moment — it inspects the repository, the
image build file, the IaC module and the process environment and never talks
to AWS:

```bash
python3 scripts/doctor.py --target local     # passes on a clean checkout
python3 scripts/doctor.py --target aws       # lists exactly what is still missing (exit 1 until ready)
python3 scripts/doctor.py --target aws --json --skip-tests
```

The Admin console serves the same report (`GET /admin/doctor?target=aws`) and
`GET /health` reports which adapters are active (`"adapters": {...}`).

Legend: ✅ done and tested · 🟡 done, with a caveat listed · ⬜ not shipped in this build.

## Checklist

| # | Item | Status | Evidence / caveat |
|---|---|---|---|
| 1 | **12-factor: config from env only** | ✅ | Every knob is a `KF_*` variable read in `app.py`, `adapters/model.py`, `adapters/otel_export.py` (mapping below). No config files, no code changes between shapes. `test_stage2_cloud.TestPlatformEnvSelection`. |
| 2 | **Adapter selection by env** (`KF_OBJECTSTORE`, `KF_QUEUE`, `KF_DB_URL`) | ✅ | `adapters/cloud.build_objectstore / build_queue / build_database`; wired in `app.py`. Unknown modes and missing coordinates fail at start-up with a `ValueError` naming the variable. |
| 3 | **S3 object store adapter** (`S3ObjectStore`) | ✅ | Content-hash keys `originals/<tenant>/<hh>/<hash>`, tenant-guarded, never overwrites (I8), SSE on every put; boto3 is a guarded import → `CloudNotReadyError("boto3 not installed; run: pip install boto3")` on first use. Tested with a fake client, no network. |
| 4 | **SQS queue adapter** (`SqsQueue`) | ✅ | Visibility-timeout leases, `ack`=delete, `nack`=retry-now or dead-letter after 5 attempts (matches the IaC redrive policy), `depth`. Same guarded import and error. Tested with a fake client. |
| 5 | **Postgres + pgvector store** | ⬜ | `KF_DB_URL=postgres://…` selects `PostgresNotice`, which describes the target (password redacted) and **fails closed** with `CloudNotReadyError` on first store call. The SQLite `Database` is the only implemented store in this build; the AWS doctor lists this as a blocker until the Postgres repository adapter ships. |
| 6 | **Stateless containers** | 🟡 | Originals in S3, jobs in SQS, no local state needed once item 5 lands. Until then a Fargate task with SQLite on `/tmp` is ephemeral — acceptable for evaluation only. |
| 7 | **Health checks** | ✅ | `GET /health` → `{status, model, connectors, adapters}`; used by the Dockerfile `HEALTHCHECK`, the ALB target group, the ECS container health check, and probed live by the doctor. |
| 8 | **Secrets** | ✅ | `KF_DB_URL`, `KF_IDP_SECRET`, `KF_MODEL_API_KEY` are Secrets Manager references injected by ECS; nothing in the task definition or repo. The doctor greps 7 credential patterns across the repo and checks `.gitignore` (`.env`, `data/`). `deploy/aws/main.tf` holds no plaintext secret literal (checked). |
| 9 | **IAM least privilege** | ✅ | Task role: `s3:GetObject/PutObject/AbortMultipartUpload` on `originals/*` (no delete), `s3:ListBucket` on that prefix, its ingest queue, `SendMessage` to the DLQ. Execution role: managed ECS execution policy + `GetSecretValue` on the three secret ARNs. No wildcards. |
| 10 | **Identity: Cognito or corporate OIDC** | 🟡 | IaC provisions a Cognito user pool (MFA, admin-only user creation, `custom:tenant`) or accepts `var.oidc_issuer`; `KF_OIDC_ISSUER`/`KF_OIDC_AUDIENCE` are exported. **Caveat:** the application verifies HS256 tokens from its built-in IdP (`adapters/identity.py`); the OIDC/JWKS verifier is the next adapter swap, so on AWS `KF_IDP_SECRET` must be a strong Secrets Manager value (the doctor blocks on the dev default). |
| 11 | **Logging** | ✅ | stdout/stderr → CloudWatch Logs `/kf/<tenant>/app` (`awslogs` driver, retention `var.log_retention_days`). Container Insights enabled on the cluster. |
| 12 | **Metrics / trace export** | 🟡 | One span per answer with tier, tokens, cost, why (`adapters/telemetry.py`); `adapters/otel_export.py` ships OTLP/JSON to `KF_OTLP_ENDPOINT` when set (`docs/OBSERVABILITY_DECISION.md`). No collector is provisioned by the module — point the variable at the client's collector. |
| 13 | **Scaling** | ✅ | ECS target-tracking on CPU (`min_capacity`..`max_capacity`), ALB in ≥2 AZs, RDS storage autoscaling. |
| 14 | **DR / backups** | ✅ | RDS automated backups (7 days default) + final snapshot; S3 versioning with 90-day non-current expiry; `db_multi_az` opt-in; `deletion_protection` on DB, ALB, ECR. |
| 15 | **Cost guardrails** | ✅ | AWS Budgets 80 %/100 % alerts scoped to the tenant tag; one NAT gateway; `db.t4g.small`; per-tenant LLM spend cap enforced by the policy engine (`POST /admin/budget`). |
| 16 | **Same image everywhere** | ✅ | `deploy/Dockerfile`: `python:3.11-slim`, non-root, `HEALTHCHECK`, `EXPOSE 8080`; the doctor proves the package has zero unguarded third-party imports. |
| 17 | **IaC validity** | 🟡 | `deploy/aws/{main,variables,outputs}.tf` + README. No terraform binary in CI: the doctor performs a structural check (files present, brackets/strings balanced, every `var.*` declared, required resources present, no secret literals). Run `tofu validate` before the first apply. |
| 18 | **Image extras for the cloud shape** | ⬜ | The stock image is stdlib-only, so `S3ObjectStore`/`SqsQueue` raise `CloudNotReadyError` inside it. Build the AWS image with `boto3` (Apache-2.0) and `pg8000` (BSD-3) — a build argument, not a code change; the image still runs locally unchanged. |

## Environment variable mapping — local → AWS

Single source of truth: `knowledge_fabric/ops/readiness.py::ENV_SPEC`
(`test_stage2_cloud` asserts this table lists every variable there and that
every variable `deploy/aws/main.tf` injects is in the spec). "Required on AWS"
variables are blockers in `doctor --target aws`; secrets are redacted in every report.

| Variable | Local (compose / laptop) | AWS (source) | Required on AWS | Secret |
|---|---|---|---|---|
| `KF_DB_URL` | unset (SQLite via `KF_DB`) | `postgresql://…?sslmode=require` from Secrets Manager (`terraform output db_secret_arn`) | yes | yes |
| `KF_DB` | `:memory:` or `/data/kf.db` | unused once `KF_DB_URL` is postgres (`/tmp/kf.db` scratch) | no | no |
| `KF_OBJECTSTORE` | `local` | `s3` | yes | no |
| `KF_S3_BUCKET` | — | `terraform output s3_bucket` | yes | no |
| `KF_S3_PREFIX` | — | `originals` (`terraform output s3_prefix`) | no | no |
| `KF_BLOBS` | `./data/blobs` | unused (container scratch only) | no | no |
| `KF_QUEUE` | `local` | `sqs` | yes | no |
| `KF_SQS_URL` | — | `terraform output sqs_queue_url` | yes | no |
| `KF_SQS_DLQ_URL` | — | `terraform output sqs_dlq_url` | no | no |
| `AWS_REGION` | — | `var.region` (task env; `terraform output region`) | yes | no |
| `KF_IDP_SECRET` | `local-dev-secret-change-me` | Secrets Manager (`terraform output idp_secret_arn`) | yes | yes |
| `KF_OIDC_ISSUER` | `kf-local` (built-in IdP) | Cognito user-pool issuer or `var.oidc_issuer` (`terraform output oidc_issuer`) | yes | no |
| `KF_OIDC_AUDIENCE` | `knowledge-fabric` | Cognito app-client id or `var.oidc_audience` (`terraform output oidc_audience`) | no | no |
| `KF_MODEL_MODE` | `mock` | `hosted` \| `self-hosted` \| `off` (`var.model_mode`) | yes | no |
| `KF_MODEL_BASE_URL` | — | model gateway URL (`var.model_base_url`) | no | no |
| `KF_MODEL_API_KEY` | — | Secrets Manager (`terraform output model_key_secret_arn`) | no | yes |
| `KF_MODEL_FAST` | `kf-mock-small` | named model per tier (optional) | no | no |
| `KF_MODEL_DEEP` | `kf-mock-mid` | named model per tier (optional) | no | no |
| `KF_MODEL_ESCALATION` | `kf-mock-large` | named model per tier (optional) | no | no |
| `KF_GROUNDING_THRESHOLD` | `0.50` | `var.grounding_threshold` | no | no |
| `KF_BUDGET_CAP_USD` | — | `var.budget_cap_usd` (reserved; the cap is set via `POST /admin/budget` today) | no | no |
| `KF_PORT` | `8080` | `8080` (`var.container_port`) | no | no |
| `KF_OTLP_ENDPOINT` | — | OTLP/HTTP collector endpoint (`var.otlp_endpoint`, optional) | no | no |

Everything in the "AWS" column is produced by `deploy/aws` — `terraform output
app_env` prints the complete non-secret set and `terraform output secret_env`
the names → secret ARNs, so the container environment can be reproduced
locally for the doctor without touching a secret value.

## What "ready" means per target

* `--target local`: passes on a clean checkout. Warnings only: the dev IdP
  secret and (until this file's IaC and doc exist) the missing IaC files.
* `--target aws`: every "required on AWS" variable present with the right
  shape (`s3`, `sqs`, `postgres://`, `https://` issuer, non-default secret ≥ 32
  chars), boto3 + a Postgres driver importable, IaC structurally valid, no
  secrets in the repo, `/health` well-formed, tests green. With all of that in
  place the **only** remaining blocker in this build is item 5 — the report
  says so verbatim.

## Image extras (item 18)

The cloud client libraries are optional, permissively licensed (`docs/TECH_STACK.md`,
`ci/licence_manifest.json`) and installed at build time only for the cloud shape:

```dockerfile
ARG KF_EXTRAS=""                       # e.g. --build-arg KF_EXTRAS="boto3 pg8000"
RUN [ -z "$KF_EXTRAS" ] || pip install --no-cache-dir $KF_EXTRAS
```

The application code is identical; only the presence of the libraries changes,
and `adapters/cloud.libraries()` / the doctor report which are present.

## Related

* `deploy/aws/README.md` — apply, push, rotate, scale, destroy.
* `docs/RUNBOOK.md` — local → interim → client is a config change.
* `docs/OBSERVABILITY_DECISION.md` — OTel export instead of a hosted tracing SaaS.
* `docs/TECH_STACK.md` — licence posture of every component, including boto3/pg8000/OpenTofu.
