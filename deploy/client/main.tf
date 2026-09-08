# Per-client infrastructure module (Build Plan Section 17) — OpenTofu-class.
#
# interim -> client is THE SAME module applied against a different account.
# Application code never learns which shape it runs in; only these inputs and
# the adapter env differ. This is a faithful sketch of the declared surface.

terraform {
  required_version = ">= 1.6"
}

variable "tenant_slug"      { type = string }
variable "region"           { type = string  default = "us-east-1" }
variable "model_mode"       { type = string  default = "hosted" } # hosted | self-hosted(vLLM)
variable "grounding_threshold" { type = number default = 0.50 }
variable "budget_cap_usd"   { type = number  default = 50 }

# --- managed data plane (swaps the local adapters) --------------------------
# module "database"    { source = "./modules/postgres_pgvector"  region = var.region }
# module "objectstore" { source = "./modules/object_store"       region = var.region }
# module "queue"       { source = "./modules/managed_queue"      region = var.region }
# module "identity"    { source = "./modules/oidc"               issuer = "corp-idp" }

# --- application (identical image across all shapes) ------------------------
locals {
  app_env = {
    KF_MODEL_MODE          = var.model_mode
    KF_GROUNDING_THRESHOLD = tostring(var.grounding_threshold)
    # KF_DB / KF_BLOBS / KF_QUEUE_URL / OIDC_ISSUER wired from the modules above
  }
}

output "handover" {
  value = {
    tenant   = var.tenant_slug
    region   = var.region
    shape    = "client-account"
    note     = "local->interim is a config change; interim->client is this module against a different account."
  }
}
