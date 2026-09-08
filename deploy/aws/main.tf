# QualiZeal Knowledge Fabric — AWS deployment shape (Build Plan Section 17).
#
# ONE container image (deploy/Dockerfile) runs in every shape. This module only
# provisions the managed engines behind the Section-4 contracts and hands their
# coordinates to the task as environment variables / Secrets Manager references:
#
#   ObjectStore -> S3 (KF_OBJECTSTORE=s3, KF_S3_BUCKET)
#   Queue       -> SQS + DLQ (KF_QUEUE=sqs, KF_SQS_URL, KF_SQS_DLQ_URL)
#   Store       -> RDS Postgres 16 with pgvector (KF_DB_URL from Secrets Manager)
#   Identity    -> Cognito user pool, or an external OIDC issuer (KF_OIDC_ISSUER)
#   Secrets     -> Secrets Manager (model key, IdP token secret, DB URL)
#   Logs        -> CloudWatch Logs (awslogs driver)
#   Compute     -> ECS Fargate service behind an ALB, private subnets, NAT egress
#
# Least privilege: the task role can read/write originals (no delete — I8),
# talk to its own queue, and nothing else. The execution role can pull the
# image, write logs and read exactly the three secrets it injects.
#
# Validated structurally by scripts/doctor.py (no terraform binary in CI).

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge({
      Project   = "knowledge-fabric"
      Tenant    = var.tenant_slug
      ManagedBy = "opentofu"
    }, var.tags)
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Naming, identity and the env mapping handed to the container
# ---------------------------------------------------------------------------
locals {
  name        = "kf-${var.tenant_slug}"
  azs         = slice(data.aws_availability_zones.available.names, 0, var.az_count)
  use_cognito = var.oidc_issuer == ""
  https       = var.alb_certificate_arn != ""

  oidc_issuer   = local.use_cognito ? "https://cognito-idp.${var.region}.amazonaws.com/${try(aws_cognito_user_pool.this[0].id, "")}" : var.oidc_issuer
  oidc_audience = local.use_cognito ? try(aws_cognito_user_pool_client.this[0].id, "") : var.oidc_audience

  image = var.image != "" ? var.image : "${try(aws_ecr_repository.this[0].repository_url, "ecr-not-created")}:${var.image_tag}"

  # Non-secret environment: the exact local -> AWS mapping in docs/AWS_READINESS.md.
  app_env = {
    KF_OBJECTSTORE         = "s3"
    KF_S3_BUCKET           = aws_s3_bucket.originals.bucket
    KF_S3_PREFIX           = "originals"
    KF_QUEUE               = "sqs"
    KF_SQS_URL             = aws_sqs_queue.ingest.url
    KF_SQS_DLQ_URL         = aws_sqs_queue.ingest_dlq.url
    AWS_REGION             = var.region
    KF_OIDC_ISSUER         = local.oidc_issuer
    KF_OIDC_AUDIENCE       = local.oidc_audience
    KF_MODEL_MODE          = var.model_mode
    KF_MODEL_BASE_URL      = var.model_base_url
    KF_GROUNDING_THRESHOLD = tostring(var.grounding_threshold)
    KF_BUDGET_CAP_USD      = tostring(var.budget_cap_usd)
    KF_PORT                = tostring(var.container_port)
    KF_OTLP_ENDPOINT       = var.otlp_endpoint
    KF_BLOBS               = "/tmp/kf-blobs" # scratch only; originals live in S3
    KF_DB                  = "/tmp/kf.db"    # unused once KF_DB_URL is postgres
  }

  # Secret environment: injected by ECS from Secrets Manager, never stored in the task definition.
  container_secrets = concat(
    [
      { name = "KF_DB_URL", valueFrom = aws_secretsmanager_secret.db_url.arn },
      { name = "KF_IDP_SECRET", valueFrom = aws_secretsmanager_secret.idp.arn },
    ],
    var.model_api_key != "" ? [{ name = "KF_MODEL_API_KEY", valueFrom = aws_secretsmanager_secret.model_key.arn }] : []
  )

  db_url = "postgresql://${var.db_username}:${urlencode(random_password.db.result)}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${var.db_name}?sslmode=require"
}

# ---------------------------------------------------------------------------
# Network: VPC, public subnets (ALB, NAT) and private subnets (tasks, RDS)
# ---------------------------------------------------------------------------
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.name }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = local.name }
}

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = { Name = "${local.name}-public-${count.index}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count = var.az_count

  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 100)
  availability_zone = local.azs[count.index]

  tags = { Name = "${local.name}-private-${count.index}", Tier = "private" }
}

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = { Name = "${local.name}-nat" }
}

# One NAT gateway (cost guardrail); tasks need egress for ECR pulls and the model gateway.
resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id

  tags       = { Name = local.name }
  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }

  tags = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count = var.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ---------------------------------------------------------------------------
# Security groups: internet -> ALB -> app -> database, nothing else
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Load balancer ingress from allowed CIDRs"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
  }

  dynamic "ingress" {
    for_each = local.https ? [443] : []
    content {
      description = "HTTPS"
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = var.allowed_ingress_cidrs
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-alb" }
}

resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "Fabric tasks: only the load balancer may reach the container port"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "from ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-app" }
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Postgres: only the app security group"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "postgres from app"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${local.name}-db" }
}

# ---------------------------------------------------------------------------
# Object store: originals by content hash, versioned, encrypted, private, TLS-only
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "originals" {
  bucket        = "${local.name}-originals-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.s3_force_destroy
}

resource "aws_s3_bucket_versioning" "originals" {
  bucket = aws_s3_bucket.originals.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "originals" {
  bucket = aws_s3_bucket.originals.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "originals" {
  bucket = aws_s3_bucket.originals.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "originals" {
  bucket = aws_s3_bucket.originals.id

  rule {
    id     = "noncurrent-expiry"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "originals_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.originals.arn,
      "${aws_s3_bucket.originals.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "originals" {
  bucket = aws_s3_bucket.originals.id
  policy = data.aws_iam_policy_document.originals_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.originals]
}

# ---------------------------------------------------------------------------
# Queue: ingest jobs with a dead-letter queue (maxReceiveCount matches SqsQueue.max_attempts)
# ---------------------------------------------------------------------------
resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name}-ingest-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${local.name}-ingest"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 0
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "ingest_dlq" {
  queue_url = aws_sqs_queue.ingest_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingest.arn]
  })
}

# ---------------------------------------------------------------------------
# Store: RDS Postgres 16 (pgvector is enabled by the app: CREATE EXTENSION vector)
# ---------------------------------------------------------------------------
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "this" {
  name   = "${local.name}-pg${split(".", var.db_engine_version)[0]}"
  family = "postgres${split(".", var.db_engine_version)[0]}"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "this" {
  identifier     = "${local.name}-db"
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 5
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period = var.db_backup_retention_days
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot   = true

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = !var.deletion_protection
  final_snapshot_identifier = "${local.name}-db-final"

  performance_insights_enabled = true
  auto_minor_version_upgrade   = true
  apply_immediately            = false
}

# ---------------------------------------------------------------------------
# Secrets Manager: DB URL, IdP token secret, model API key
# ---------------------------------------------------------------------------
resource "random_password" "idp" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "db_url" {
  name                    = "${local.name}/db-url"
  description             = "KF_DB_URL for the Fabric task (postgresql://...?sslmode=require)"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_url" {
  secret_id     = aws_secretsmanager_secret.db_url.id
  secret_string = local.db_url
}

resource "aws_secretsmanager_secret" "idp" {
  name                    = "${local.name}/idp-secret"
  description             = "KF_IDP_SECRET (HS256 token secret) for the Fabric task"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "idp" {
  secret_id     = aws_secretsmanager_secret.idp.id
  secret_string = var.idp_secret != "" ? var.idp_secret : random_password.idp.result
}

resource "aws_secretsmanager_secret" "model_key" {
  name                    = "${local.name}/model-api-key"
  description             = "KF_MODEL_API_KEY for the model gateway (only injected when set)"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "model_key" {
  count = var.model_api_key != "" ? 1 : 0

  secret_id     = aws_secretsmanager_secret.model_key.id
  secret_string = var.model_api_key
}

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "app" {
  name              = "/kf/${var.tenant_slug}/app"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------------------
# IAM: execution role (pull image, write logs, inject secrets) and task role (least privilege)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid     = "ReadInjectedSecrets"
    actions = ["secretsmanager:GetSecretValue"]

    resources = [
      aws_secretsmanager_secret.db_url.arn,
      aws_secretsmanager_secret.idp.arn,
      aws_secretsmanager_secret.model_key.arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-injected-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# The task role is the application's identity. It can:
#  - write and read originals under its prefix — and NOT delete them (I8: immutable originals)
#  - list its prefix (so HeadObject on a missing key returns 404 rather than 403)
#  - use its ingest queue and push to the dead-letter queue
# No wildcards, no other services.
data "aws_iam_policy_document" "task" {
  statement {
    sid       = "OriginalsReadWriteNoDelete"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.originals.arn}/originals/*"]
  }

  statement {
    sid       = "OriginalsListPrefix"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.originals.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["originals/*"]
    }
  }

  statement {
    sid = "IngestQueue"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.ingest.arn]
  }

  statement {
    sid       = "IngestDeadLetter"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest_dlq.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "least-privilege"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ---------------------------------------------------------------------------
# Image registry (optional) and ECS Fargate service behind the ALB
# ---------------------------------------------------------------------------
resource "aws_ecr_repository" "this" {
  count = var.create_ecr ? 1 : 0

  name                 = local.name
  image_tag_mutability = "MUTABLE" # pin by digest in production pipelines
  force_delete         = !var.deletion_protection

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "fabric"
      image     = local.image
      essential = true

      portMappings = [
        { containerPort = var.container_port, protocol = "tcp" }
      ]

      environment = [for k, v in local.app_env : { name = k, value = v }]
      secrets     = local.container_secrets

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request as u,sys; sys.exit(0 if u.urlopen('http://localhost:${var.container_port}/health').status==200 else 1)\""]
        interval    = 15
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "fabric"
        }
      }
    }
  ])

  lifecycle {
    precondition {
      condition     = var.image != "" || var.create_ecr
      error_message = "Set var.image to an existing image, or leave create_ecr = true so the module creates a repository."
    }
  }
}

resource "aws_lb" "this" {
  name                       = substr("${local.name}-alb", 0, 32)
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
  idle_timeout               = 120
  enable_deletion_protection = var.deletion_protection
}

resource "aws_lb_target_group" "app" {
  name                 = substr("${local.name}-tg", 0, 32)
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.this.id
  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = local.https ? [1] : []
    content {
      type = "redirect"

      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  dynamic "default_action" {
    for_each = local.https ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.app.arn
    }
  }
}

resource "aws_lb_listener" "https" {
  count = local.https ? 1 : 0

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.alb_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_ecs_service" "app" {
  name             = local.name
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.app.arn
  desired_count    = var.desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  enable_execute_command             = false
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "fabric"
    container_port   = var.container_port
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [desired_count] # autoscaling owns it after the first apply
  }

  depends_on = [aws_lb_listener.http]
}

resource "aws_appautoscaling_target" "app" {
  max_capacity       = var.max_capacity
  min_capacity       = var.min_capacity
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${local.name}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.app.resource_id
  scalable_dimension = aws_appautoscaling_target.app.scalable_dimension
  service_namespace  = aws_appautoscaling_target.app.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = var.cpu_target_percent
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

# ---------------------------------------------------------------------------
# Identity: Cognito user pool unless an external OIDC issuer is supplied
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "this" {
  count = local.use_cognito ? 1 : 0

  name              = local.name
  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # custom:tenant claim -> Principal.tenant (I5: the token carries the tenant)
  schema {
    name                = "tenant"
    attribute_data_type = "String"
    mutable             = false

    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }
}

resource "aws_cognito_user_pool_client" "this" {
  count = local.use_cognito ? 1 : 0

  name            = "${local.name}-app"
  user_pool_id    = aws_cognito_user_pool.this[0].id
  generate_secret = false

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"

  callback_urls = ["https://${aws_lb.this.dns_name}/auth/callback"]
  logout_urls   = ["https://${aws_lb.this.dns_name}/"]

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  count = local.use_cognito ? 1 : 0

  domain       = "${local.name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.this[0].id
}

# ---------------------------------------------------------------------------
# Cost guardrail: a monthly budget scoped to this tenant's tag
# ---------------------------------------------------------------------------
resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Tenant$%s", var.tenant_slug)]
  }

  dynamic "notification" {
    for_each = length(var.budget_alert_emails) > 0 ? [80, 100] : []
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.budget_alert_emails
    }
  }
}
