# Knowledge Fabric on AWS — `deploy/aws`

OpenTofu / Terraform module that stands up the AWS shape of the Knowledge
Fabric for **one tenant**. It provisions the managed engines behind the
Section-4 contracts and runs **the same container image** that runs on a
laptop (`deploy/compose`) — the application never learns which shape it is in;
only the environment differs (`docs/AWS_READINESS.md` has the exact mapping).

```
 internet ──► ALB (80 / 443) ──► ECS Fargate service (private subnets)
                                    │  KF_OBJECTSTORE=s3   ──► S3 originals bucket (versioned, SSE, TLS-only)
                                    │  KF_QUEUE=sqs        ──► SQS ingest queue + DLQ
                                    │  KF_DB_URL (secret)  ──► RDS Postgres 16 (pgvector), force_ssl
                                    │  KF_OIDC_ISSUER      ──► Cognito user pool  |  external OIDC issuer
                                    │  secrets             ──► Secrets Manager (db url · idp secret · model key)
                                    └─ logs                ──► CloudWatch Logs (/kf/<tenant>/app)
```

| File | Contents |
|---|---|
| `main.tf` | VPC (2–3 AZs, one NAT), security groups, S3, SQS + DLQ, RDS, Secrets Manager, CloudWatch, IAM (execution + least-privilege task role), ECR (optional), ECS cluster/task/service + autoscaling, ALB, Cognito (optional), AWS Budgets |
| `variables.tf` | Inputs. Three shape a deployment: `tenant_slug`, `region`, `model_mode`; everything else has a conservative default |
| `outputs.tf` | Coordinates for operators and for `scripts/doctor.py` (bucket, queue URLs, secret ARNs, issuer, log group, `app_env`) |

## Prerequisites

* OpenTofu ≥ 1.6 (MPL-2.0) or Terraform ≥ 1.6, AWS provider `~> 5.40`.
* An AWS account/role that can create the resources above, credentials in the
  usual places (`AWS_PROFILE` or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`).
  Nothing in this repository holds credentials — `scripts/doctor.py` greps to prove it.
* Docker, to build and push the image.
* Optional: an ACM certificate ARN for HTTPS, an external OIDC issuer.

## Apply

```bash
cd deploy/aws
tofu init
tofu plan  -var tenant_slug=acme -var region=eu-west-1 -var model_mode=off
tofu apply -var tenant_slug=acme -var region=eu-west-1 -var model_mode=off
```

Recommended `terraform.tfvars` for a real client (never commit secrets — pass
them with `-var` or `TF_VAR_model_api_key` from your secret manager):

```hcl
tenant_slug           = "acme"
region                = "eu-west-1"
model_mode            = "hosted"            # hosted | self-hosted | mock | off
model_base_url        = "https://gateway.example.internal/v1"
oidc_issuer           = ""                  # empty -> Cognito user pool is created
alb_certificate_arn   = "arn:aws:acm:eu-west-1:123456789012:certificate/..."
allowed_ingress_cidrs = ["203.0.113.0/24"]  # the client's egress ranges
db_multi_az           = true
monthly_budget_usd    = 300
budget_alert_emails   = ["platform-ops@example.com"]
```

## After apply

1. **Push the image** (the module created an ECR repository unless `image` was set):
   ```bash
   docker build -t "$(tofu output -raw image)" -f deploy/Dockerfile .
   aws ecr get-login-password --region "$(tofu output -raw region)" \
     | docker login --username AWS --password-stdin "$(tofu output -raw ecr_repository_url)"
   docker push "$(tofu output -raw image)"
   aws ecs update-service --cluster "$(tofu output -raw ecs_cluster_name)" \
     --service "$(tofu output -raw ecs_service_name)" --force-new-deployment
   ```
   The stock image is standard-library only. For the AWS shape build it with
   the client libraries the cloud adapters need (`boto3` Apache-2.0,
   `pg8000` BSD-3) — see "Image extras" in `docs/AWS_READINESS.md`.
2. **Run the doctor** with the container environment exported — it lists
   exactly what is still missing before you look at CloudWatch:
   ```bash
   eval "$(tofu output -json app_env | python3 -c 'import json,sys; [print(f"export {k}={v!r}") for k,v in json.load(sys.stdin).items()]')"
   python3 scripts/doctor.py --target aws
   ```
3. **Smoke test**: `curl "$(tofu output -raw health_url)"` must answer
   `{"status":"ok", ...,"adapters":{"objectstore":"S3ObjectStore","queue":"SqsQueue","database":"PostgresNotice"}}`.
   The Admin console shows the same report under **AWS readiness**
   (`GET /admin/doctor?target=aws`).

## What the module deliberately does

* **Least privilege.** The task role can `GetObject`/`PutObject` under
  `originals/*` and **cannot delete** (I8: originals are immutable — the
  adapter also refuses to overwrite), can use its ingest queue and push to the
  DLQ, and nothing else. The execution role can pull the image, write logs and
  read exactly the three injected secrets. No wildcards.
* **Secrets never touch the task definition.** `KF_DB_URL`, `KF_IDP_SECRET`
  and `KF_MODEL_API_KEY` are Secrets Manager references injected by ECS; the
  DB password and IdP secret are generated by `random_password` unless supplied.
* **Private by default.** Tasks and the database sit in private subnets; the
  only ingress is ALB → container port; RDS accepts the app security group only
  and forces SSL; the bucket blocks public access and denies non-TLS access.
* **Health and rollback.** ALB and container health checks hit `/health`;
  the deployment circuit breaker rolls back a failed deploy automatically.
* **Scaling.** Target-tracking autoscaling on CPU between `min_capacity` and
  `max_capacity`; RDS storage autoscaling to 5× the initial allocation.
* **Backups / DR.** RDS automated backups (`db_backup_retention_days`, default
  7), final snapshot on destroy, S3 versioning with 90-day non-current expiry;
  `db_multi_az = true` for production.
* **Cost guardrails.** One NAT gateway, `db.t4g.small`, an AWS Budgets alarm
  at 80 % / 100 % of `monthly_budget_usd` scoped to the tenant tag,
  `deletion_protection` on the database, load balancer and registry.
* **Identity.** A Cognito user pool with MFA and a `custom:tenant` attribute
  (I5: the token carries the tenant) — or point `oidc_issuer` at the corporate
  IdP and no pool is created.

## Operations

| Task | How |
|---|---|
| Tail logs | `aws logs tail "$(tofu output -raw log_group)" --follow` |
| Roll a new image | push with the same tag, then `aws ecs update-service ... --force-new-deployment` (or pin by digest and bump `image_tag`) |
| Rotate the IdP secret | `aws secretsmanager put-secret-value --secret-id "$(tofu output -raw idp_secret_arn)" --secret-string ...` then redeploy the service |
| Rotate the model key | same with `model_key_secret_arn`, or `tofu apply -var model_api_key=...` |
| Rotate the DB password | `tofu taint random_password.db && tofu apply` (updates RDS and the secret together) |
| Inspect the DLQ | `aws sqs receive-message --queue-url "$(tofu output -raw sqs_dlq_url)"` |
| Scale manually | `tofu apply -var min_capacity=2 -var max_capacity=8` |
| Destroy (evaluation only) | `tofu destroy -var deletion_protection=false -var s3_force_destroy=true ...` |

## Validation without a terraform binary

CI has no `tofu`/`terraform`. `scripts/doctor.py --target aws` performs a
structural check of this module instead: every file present, HCL brackets and
strings balanced (comment/heredoc/interpolation aware), every `var.*` reference
declared in `variables.tf`, the required resource types present in `main.tf`,
and no plaintext secret literal in any `.tf` file. Run `tofu validate` locally
before the first real apply.
