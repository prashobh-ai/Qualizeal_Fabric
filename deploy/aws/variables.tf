# Inputs for the Knowledge Fabric AWS module (Build Plan Section 17).
#
# Three inputs shape a deployment: tenant_slug, region, model_mode. Everything
# else has a conservative default. Secrets (model_api_key, idp_secret) are
# marked sensitive and land in Secrets Manager — never in task environment.

variable "tenant_slug" {
  description = "Short DNS-safe tenant/client identifier; prefixes every resource name (kf-<slug>-...)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.tenant_slug))
    error_message = "tenant_slug must be 2-31 lowercase letters, digits or hyphens and start with a letter."
  }
}

variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
}

variable "model_mode" {
  description = "LLM mode passed to the app as KF_MODEL_MODE: hosted (OpenAI-compatible gateway), self-hosted (vLLM behind model_base_url), mock, or off (extractive core only)."
  type        = string
  default     = "hosted"

  validation {
    condition     = contains(["hosted", "self-hosted", "mock", "off"], var.model_mode)
    error_message = "model_mode must be one of: hosted, self-hosted, mock, off."
  }
}

variable "model_base_url" {
  description = "OpenAI-compatible base URL of the model gateway (KF_MODEL_BASE_URL). Empty when model_mode is mock/off."
  type        = string
  default     = ""
}

variable "model_api_key" {
  description = "API key for the model gateway. Stored in Secrets Manager and injected as KF_MODEL_API_KEY; empty = no key injected."
  type        = string
  default     = ""
  sensitive   = true
}

variable "idp_secret" {
  description = "HS256 token secret (KF_IDP_SECRET). Empty = a random 48-character secret is generated and stored in Secrets Manager."
  type        = string
  default     = ""
  sensitive   = true
}

variable "oidc_issuer" {
  description = "External OIDC issuer URL (corporate IdP). Empty = create a Cognito user pool and use it as the issuer."
  type        = string
  default     = ""
}

variable "oidc_audience" {
  description = "Expected token audience / client id when oidc_issuer is external. Ignored for Cognito (the app-client id is used)."
  type        = string
  default     = ""
}

variable "image" {
  description = "Full container image reference (registry/repo:tag). Empty = <created ECR repository>:<image_tag>. The SAME image runs locally, on the interim host and here."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Tag used when image is empty and the module creates the ECR repository."
  type        = string
  default     = "latest"
}

variable "create_ecr" {
  description = "Create an ECR repository for the image. Set false when pulling from an existing registry via var.image."
  type        = bool
  default     = true
}

variable "container_port" {
  description = "Port the app listens on inside the container (KF_PORT)."
  type        = number
  default     = 8080
}

variable "cpu" {
  description = "Fargate task CPU units (256, 512, 1024, ...)."
  type        = number
  default     = 512
}

variable "memory" {
  description = "Fargate task memory in MiB (must be valid for the chosen cpu)."
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Initial number of running tasks (autoscaling adjusts it afterwards)."
  type        = number
  default     = 1
}

variable "min_capacity" {
  description = "Autoscaling floor for the service."
  type        = number
  default     = 1
}

variable "max_capacity" {
  description = "Autoscaling ceiling for the service."
  type        = number
  default     = 4
}

variable "cpu_target_percent" {
  description = "Target average CPU utilisation for the scaling policy."
  type        = number
  default     = 60
}

variable "vpc_cidr" {
  description = "CIDR block for the tenant VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "az_count" {
  description = "Number of availability zones (public + private subnet per AZ)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "allowed_ingress_cidrs" {
  description = "CIDRs allowed to reach the load balancer (restrict to the client's egress ranges when known)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "alb_certificate_arn" {
  description = "ACM certificate ARN for HTTPS on the ALB. Empty = plain HTTP listener only (evaluation shape)."
  type        = string
  default     = ""
}

variable "db_instance_class" {
  description = "RDS instance class for the Postgres+pgvector store."
  type        = string
  default     = "db.t4g.small"
}

variable "db_allocated_storage" {
  description = "Initial RDS storage in GiB (autoscaling up to 5x)."
  type        = number
  default     = 20
}

variable "db_engine_version" {
  description = "RDS Postgres engine version (pgvector is available on 15.2+ / 16)."
  type        = string
  default     = "16"
}

variable "db_multi_az" {
  description = "Multi-AZ standby for the database (recommended for production)."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  description = "Automated backup retention in days (0 disables backups; keep >= 7 for production)."
  type        = number
  default     = 7
}

variable "db_name" {
  description = "Database name inside the RDS instance."
  type        = string
  default     = "fabric"
}

variable "db_username" {
  description = "Master username for the RDS instance (password is generated and stored in Secrets Manager)."
  type        = string
  default     = "fabric"
}

variable "deletion_protection" {
  description = "Protect the database, load balancer and ECR repository from accidental destroy."
  type        = bool
  default     = true
}

variable "s3_force_destroy" {
  description = "Allow terraform destroy to empty the originals bucket. Keep false outside evaluation accounts."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the application log group."
  type        = number
  default     = 30
}

variable "grounding_threshold" {
  description = "Per-tenant grounding threshold passed as KF_GROUNDING_THRESHOLD."
  type        = number
  default     = 0.50
}

variable "budget_cap_usd" {
  description = "Per-tenant LLM spend cap passed as KF_BUDGET_CAP_USD (enforced by the app's policy engine)."
  type        = number
  default     = 50
}

variable "monthly_budget_usd" {
  description = "AWS Budgets cost guardrail for this tenant's resources (USD per month)."
  type        = number
  default     = 300
}

variable "budget_alert_emails" {
  description = "Recipients for 80% and 100% budget notifications. Empty = budget created without notifications."
  type        = list(string)
  default     = []
}

variable "otlp_endpoint" {
  description = "OTLP/HTTP collector endpoint for span export (KF_OTLP_ENDPOINT). Empty = spans stay in the store."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
