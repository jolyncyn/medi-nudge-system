variable "environment" { type = string }
variable "aws_region" { type = string }
variable "aws_account_id" { type = string }
variable "vpc_id" { type = string }
variable "public_subnet_ids" { type = list(string) }
variable "alb_target_group_arn" { type = string }
variable "alb_sg_id" { type = string }
variable "ecs_sg_id" { type = string }
variable "ecr_repository_url" { type = string }
variable "media_bucket_name" { type = string }
variable "db_secret_arn" { type = string }
variable "task_role_arn" { type = string }
variable "execution_role_arn" { type = string }
variable "api_desired_count" {
  type    = number
  default = 1
}

locals {
  image_tag   = "latest"
  image_uri   = "${var.ecr_repository_url}:${local.image_tag}"
  secret_base = "/medi-nudge/${var.environment}"
}

# Look up the real secret ARNs (AWS appends a 6-char suffix that we can't predict)
data "aws_secretsmanager_secret" "jwt_secret_key" { name = "${local.secret_base}/jwt-secret-key" }
data "aws_secretsmanager_secret" "openai_api_key" { name = "${local.secret_base}/openai-api-key" }
data "aws_secretsmanager_secret" "telegram_bot_token" { name = "${local.secret_base}/telegram-bot-token" }
data "aws_secretsmanager_secret" "telegram_webhook_secret" { name = "${local.secret_base}/telegram-webhook-secret" }
data "aws_secretsmanager_secret" "elevenlabs_api_key" { name = "${local.secret_base}/elevenlabs-api-key" }
data "aws_secretsmanager_secret" "elevenlabs_default_voice_female" { name = "${local.secret_base}/elevenlabs-default-voice-female" }
data "aws_secretsmanager_secret" "elevenlabs_default_voice_male" { name = "${local.secret_base}/elevenlabs-default-voice-male" }
data "aws_secretsmanager_secret" "telegram_bot_username" { name = "${local.secret_base}/telegram-bot-username" }
data "aws_secretsmanager_secret" "allowed_origins" { name = "${local.secret_base}/allowed-origins" }
data "aws_secretsmanager_secret" "apns_private_key" { name = "${local.secret_base}/apns-private-key" }
data "aws_secretsmanager_secret" "apns_key_id" { name = "${local.secret_base}/apns-key-id" }
data "aws_secretsmanager_secret" "apns_team_id" { name = "${local.secret_base}/apns-team-id" }
data "aws_secretsmanager_secret" "apns_topic" { name = "${local.secret_base}/apns-topic" }
data "aws_secretsmanager_secret" "apns_environment" { name = "${local.secret_base}/apns-environment" }

resource "aws_ecs_cluster" "main" {
  name = "medi-nudge-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/medi-nudge-api/${var.environment}"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "scheduler" {
  name              = "/ecs/medi-nudge-scheduler/${var.environment}"
  retention_in_days = 30
}


# ── API task definition ───────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "api" {
  family                   = "medi-nudge-api-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  task_role_arn            = var.task_role_arn
  execution_role_arn       = var.execution_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "api"
    image     = local.image_uri
    essential = true

    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    environment = [
      { name = "SCHEDULER_ENABLED", value = "false" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "AWS_S3_BUCKET_NAME", value = var.media_bucket_name },
    ]

    secrets = [
      { name = "DATABASE_URL", valueFrom = "${var.db_secret_arn}" },
      { name = "JWT_SECRET_KEY", valueFrom = data.aws_secretsmanager_secret.jwt_secret_key.arn },
      { name = "OPENAI_API_KEY", valueFrom = data.aws_secretsmanager_secret.openai_api_key.arn },
      { name = "TELEGRAM_BOT_TOKEN", valueFrom = data.aws_secretsmanager_secret.telegram_bot_token.arn },
      { name = "TELEGRAM_WEBHOOK_SECRET", valueFrom = data.aws_secretsmanager_secret.telegram_webhook_secret.arn },
      { name = "TELEGRAM_BOT_USERNAME", valueFrom = data.aws_secretsmanager_secret.telegram_bot_username.arn },
      { name = "ELEVENLABS_API_KEY", valueFrom = data.aws_secretsmanager_secret.elevenlabs_api_key.arn },
      { name = "ELEVENLABS_DEFAULT_VOICE_FEMALE", valueFrom = data.aws_secretsmanager_secret.elevenlabs_default_voice_female.arn },
      { name = "ELEVENLABS_DEFAULT_VOICE_MALE", valueFrom = data.aws_secretsmanager_secret.elevenlabs_default_voice_male.arn },
      { name = "ALLOWED_ORIGINS", valueFrom = data.aws_secretsmanager_secret.allowed_origins.arn },
      { name = "APNS_PRIVATE_KEY", valueFrom = data.aws_secretsmanager_secret.apns_private_key.arn },
      { name = "APNS_KEY_ID", valueFrom = data.aws_secretsmanager_secret.apns_key_id.arn },
      { name = "APNS_TEAM_ID", valueFrom = data.aws_secretsmanager_secret.apns_team_id.arn },
      { name = "APNS_TOPIC", valueFrom = data.aws_secretsmanager_secret.apns_topic.arn },
      { name = "APNS_ENVIRONMENT", valueFrom = data.aws_secretsmanager_secret.apns_environment.arn },
    ]

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

# ── Scheduler task definition ─────────────────────────────────────────────────

resource "aws_ecs_task_definition" "scheduler" {
  family                   = "medi-nudge-scheduler-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  task_role_arn            = var.task_role_arn
  execution_role_arn       = var.execution_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "scheduler"
    image     = local.image_uri
    essential = true
    command   = ["python", "-m", "app.worker"]

    environment = [
      { name = "SCHEDULER_ENABLED", value = "true" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "AWS_S3_BUCKET_NAME", value = var.media_bucket_name },
    ]

    secrets = [
      { name = "DATABASE_URL", valueFrom = var.db_secret_arn },
      { name = "OPENAI_API_KEY", valueFrom = data.aws_secretsmanager_secret.openai_api_key.arn },
      { name = "TELEGRAM_BOT_TOKEN", valueFrom = data.aws_secretsmanager_secret.telegram_bot_token.arn },
      { name = "TELEGRAM_WEBHOOK_SECRET", valueFrom = data.aws_secretsmanager_secret.telegram_webhook_secret.arn },
      { name = "TELEGRAM_BOT_USERNAME", valueFrom = data.aws_secretsmanager_secret.telegram_bot_username.arn },
      { name = "ELEVENLABS_API_KEY", valueFrom = data.aws_secretsmanager_secret.elevenlabs_api_key.arn },
      { name = "ELEVENLABS_DEFAULT_VOICE_FEMALE", valueFrom = data.aws_secretsmanager_secret.elevenlabs_default_voice_female.arn },
      { name = "ELEVENLABS_DEFAULT_VOICE_MALE", valueFrom = data.aws_secretsmanager_secret.elevenlabs_default_voice_male.arn },
      { name = "APNS_PRIVATE_KEY", valueFrom = data.aws_secretsmanager_secret.apns_private_key.arn },
      { name = "APNS_KEY_ID", valueFrom = data.aws_secretsmanager_secret.apns_key_id.arn },
      { name = "APNS_TEAM_ID", valueFrom = data.aws_secretsmanager_secret.apns_team_id.arn },
      { name = "APNS_TOPIC", valueFrom = data.aws_secretsmanager_secret.apns_topic.arn },
      { name = "APNS_ENVIRONMENT", valueFrom = data.aws_secretsmanager_secret.apns_environment.arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.scheduler.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

# ── Migration task definition (one-off, run from CI) ─────────────────────────

resource "aws_ecs_task_definition" "migrate" {
  family                   = "medi-nudge-migrate-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  task_role_arn            = var.task_role_arn
  execution_role_arn       = var.execution_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "migrate"
    image     = local.image_uri
    essential = true
    command   = ["alembic", "upgrade", "head"]

    environment = []

    secrets = [
      { name = "DATABASE_URL", valueFrom = "${var.db_secret_arn}" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  }])
}

# ── ECS services ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "api" {
  name                              = "api-service"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = var.api_desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60
  force_new_deployment              = false

  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [var.ecs_sg_id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = var.alb_target_group_arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }
}

resource "aws_ecs_service" "scheduler" {
  name            = "scheduler-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.scheduler.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [var.ecs_sg_id]
    assign_public_ip = true
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}

output "cluster_arn" { value = aws_ecs_cluster.main.arn }
output "cluster_name" { value = aws_ecs_cluster.main.name }
output "migrate_task_definition_arn" { value = aws_ecs_task_definition.migrate.arn }
