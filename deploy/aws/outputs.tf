# Outputs of the Knowledge Fabric AWS module (Build Plan Section 17).
#
# Two audiences:
#   * operators  — where the service is, which log group to tail, what to run
#                  after apply (image push, doctor, smoke test);
#   * the doctor — ``scripts/doctor.py --target aws`` and docs/AWS_READINESS.md
#                  name these outputs as the source of each KF_* variable.
#
# No output carries a secret value: secrets are referenced by ARN only and the
# container receives them through ECS's Secrets Manager injection.

# ---------------------------------------------------------------------------
# Where the service is
# ---------------------------------------------------------------------------
output "tenant_slug" {
  description = "Tenant this stack was applied for (prefixes every resource name)."
  value       = var.tenant_slug
}

output "region" {
  description = "Region every resource lives in (AWS_REGION in the task environment)."
  value       = var.region
}

output "alb_dns_name" {
  description = "Public DNS name of the application load balancer."
  value       = aws_lb.this.dns_name
}

output "app_url" {
  description = "Base URL of the Ask console (https when alb_certificate_arn is set, http otherwise)."
  value       = "${local.https ? "https" : "http"}://${aws_lb.this.dns_name}"
}

output "health_url" {
  description = "Liveness endpoint polled by the ALB target group and the container HEALTHCHECK."
  value       = "${local.https ? "https" : "http"}://${aws_lb.this.dns_name}/health"
}

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------
output "ecs_cluster_name" {
  description = "ECS cluster running the Fabric service."
  value       = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  description = "ECS service name (use with `aws ecs update-service --force-new-deployment` after an image push)."
  value       = aws_ecs_service.app.name
}

output "task_definition_arn" {
  description = "Current task definition revision."
  value       = aws_ecs_task_definition.app.arn
}

output "ecr_repository_url" {
  description = "ECR repository the module created for the image, or null when var.image points at an external registry."
  value       = var.create_ecr ? aws_ecr_repository.this[0].repository_url : null
}

output "image" {
  description = "Exact image reference the task runs — the SAME image that runs locally via deploy/compose."
  value       = local.image
}

output "task_role_arn" {
  description = "IAM role the application assumes (least privilege: originals read/write without delete, its own queues)."
  value       = aws_iam_role.task.arn
}

output "execution_role_arn" {
  description = "IAM role ECS uses to pull the image, write logs and inject the three secrets."
  value       = aws_iam_role.execution.arn
}

output "log_group" {
  description = "CloudWatch Logs group for application output (awslogs driver)."
  value       = aws_cloudwatch_log_group.app.name
}

# ---------------------------------------------------------------------------
# Data plane — the managed engines behind the Section-4 contracts
# ---------------------------------------------------------------------------
output "s3_bucket" {
  description = "Originals bucket (KF_S3_BUCKET). Versioned, encrypted, private, TLS-only."
  value       = aws_s3_bucket.originals.bucket
}

output "s3_prefix" {
  description = "Key prefix the task role is scoped to (KF_S3_PREFIX)."
  value       = local.app_env.KF_S3_PREFIX
}

output "sqs_queue_url" {
  description = "Ingest queue URL (KF_SQS_URL)."
  value       = aws_sqs_queue.ingest.url
}

output "sqs_dlq_url" {
  description = "Dead-letter queue URL (KF_SQS_DLQ_URL); redrive after maxReceiveCount = 5."
  value       = aws_sqs_queue.ingest_dlq.url
}

output "db_endpoint" {
  description = "RDS Postgres endpoint host:port (private subnets only; reachable from the app security group)."
  value       = "${aws_db_instance.this.address}:${aws_db_instance.this.port}"
}

output "db_name" {
  description = "Database name inside the RDS instance."
  value       = var.db_name
}

output "db_secret_arn" {
  description = "Secrets Manager ARN holding KF_DB_URL (postgresql://...?sslmode=require). The value is never output."
  value       = aws_secretsmanager_secret.db_url.arn
}

output "idp_secret_arn" {
  description = "Secrets Manager ARN holding KF_IDP_SECRET (HS256 token secret)."
  value       = aws_secretsmanager_secret.idp.arn
}

output "model_key_secret_arn" {
  description = "Secrets Manager ARN for KF_MODEL_API_KEY (only injected into the task when var.model_api_key was set)."
  value       = aws_secretsmanager_secret.model_key.arn
}

output "model_key_injected" {
  description = "Whether KF_MODEL_API_KEY is injected into the task (false when var.model_api_key is empty)."
  value       = var.model_api_key != ""
}

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
output "oidc_issuer" {
  description = "Token issuer the app validates against (KF_OIDC_ISSUER): the Cognito user pool, or var.oidc_issuer."
  value       = local.oidc_issuer
}

output "oidc_audience" {
  description = "Expected token audience (KF_OIDC_AUDIENCE): the Cognito app-client id, or var.oidc_audience."
  value       = local.oidc_audience
}

output "cognito_user_pool_id" {
  description = "Cognito user pool id, or null when an external OIDC issuer is used."
  value       = local.use_cognito ? aws_cognito_user_pool.this[0].id : null
}

output "cognito_hosted_ui_domain" {
  description = "Cognito hosted-UI domain prefix, or null when an external OIDC issuer is used."
  value       = local.use_cognito ? aws_cognito_user_pool_domain.this[0].domain : null
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
output "vpc_id" {
  description = "Tenant VPC id."
  value       = aws_vpc.this.id
}

output "private_subnet_ids" {
  description = "Private subnets hosting the tasks and the database."
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "Public subnets hosting the load balancer and the NAT gateway."
  value       = aws_subnet.public[*].id
}

# ---------------------------------------------------------------------------
# The container environment, for the doctor and for parity checks
# ---------------------------------------------------------------------------
output "app_env" {
  description = "Non-secret environment the task runs with — the exact local -> AWS mapping in docs/AWS_READINESS.md."
  value       = local.app_env
}

output "secret_env" {
  description = "Secret environment variable names and the Secrets Manager ARNs that back them (values are never output)."
  value       = { for s in local.container_secrets : s.name => s.valueFrom }
}

output "monthly_budget_usd" {
  description = "AWS Budgets guardrail applied to this tenant's tagged resources."
  value       = var.monthly_budget_usd
}

output "next_steps" {
  description = "What to run after apply."
  value = join("\n", [
    "1. docker build -t ${local.image} -f deploy/Dockerfile . && docker push ${local.image}",
    "2. aws ecs update-service --cluster ${aws_ecs_cluster.this.name} --service ${aws_ecs_service.app.name} --force-new-deployment --region ${var.region}",
    "3. python3 scripts/doctor.py --target aws   (with the app_env output exported; lists exactly what is still missing)",
    "4. curl ${local.https ? "https" : "http"}://${aws_lb.this.dns_name}/health",
  ])
}
