locals {
  runtime_sa_id  = "dice-${var.environment}-runtime"
  deployer_sa_id = "dice-${var.environment}-deployer"
  backup_sa_id   = "dice-${var.environment}-db-backup"
  repo_id        = "dice"
  env_title      = title(var.environment)

  # The one place the backup switch is read. Everything backup-related goes through these.
  db_backup_count     = var.enable_db_backup ? 1 : 0
  db_backup_job_count = var.deploy_cloud_run ? local.db_backup_count : 0

  apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
  ]

  # Empty secrets are made here. Their values are added by hand (README.md) so they never
  # enter OpenTofu or its state. Maps the app's variable name to the secret's name.
  all_secrets = {
    SECRET_KEY         = "dice-secret-key"
    DATABASE_URL       = "dice-database-url"
    S3_ACCESS_KEY      = "dice-s3-access-key"
    S3_SECRET_KEY      = "dice-s3-secret-key"
    SENTRY_DSN         = "dice-sentry-dsn"
    ANTHROPIC_API_KEY  = "dice-anthropic-api-key"
    ORIGIN_LOCK_SECRET = "dice-origin-lock-secret"
  }

  # Without the origin lock (ADR 0036) there is no secret to make: Cloud Run won't start a
  # container that references a secret with no value.
  secrets = {
    for name, secret_id in local.all_secrets : name => secret_id
    if name != "ORIGIN_LOCK_SECRET" || var.enable_origin_lock
  }

  # Only the nightly backup job gets these (a separate R2 bucket and token, DIC-2540). It runs
  # as its own service account, so the app and worker can't read them even from inside their
  # containers. They are not in `secrets` above, which the app and worker get.
  backup_secrets = local.db_backup_count == 1 ? {
    DB_BACKUP_ACCESS_KEY = "dice-db-backup-access-key"
    DB_BACKUP_SECRET_KEY = "dice-db-backup-secret-key"
  } : {}

  # The shared secrets the backup job also needs, by the app's variable name.
  backup_shared_secret_names = ["SECRET_KEY", "DATABASE_URL", "SENTRY_DSN"]

  # With no custom domain, use the address Cloud Run gives every service. It can be worked
  # out up front from the project number, so the first apply gets it right. The `try` only
  # covers the unused branch (no lookup is made when a hostname is given); a failed lookup
  # still stops the plan.
  public_hostname = (
    var.public_hostname != null
    ? var.public_hostname
    : "dice-app-${try(data.google_project.this[0].number, "")}.${var.region}.run.app"
  )

  allowed_hosts = join(",", concat([local.public_hostname], var.extra_allowed_hosts))

  plain_env = merge(
    { for name, on in var.run_steps : name => title(tostring(on)) },
    {
      S3_BUCKET_NAME       = var.s3_bucket_name
      S3_ENDPOINT_URL      = var.s3_endpoint_url
      S3_ADDRESSING_STYLE  = "path"
      SENTRY_ENVIRONMENT   = var.sentry_environment
      TRUSTED_PROXY_HOPS   = "1" # Cloud Run adds one X-Forwarded-For entry (ADR 0031)
      ALLOWED_HOSTS        = local.allowed_hosts
      CSRF_TRUSTED_ORIGINS = "https://${local.public_hostname}"
      PUBLIC_BASE_URL      = "https://${local.public_hostname}"
    },
    # Only with the Cloudflare Worker in front, which sends the public hostname this way (ADR 0041).
    var.use_forwarded_host ? { USE_X_FORWARDED_HOST = "True" } : {},
  )
}

# --- Project and services -------------------------------------------------------------

# Only looked up when there is no custom domain (see local.public_hostname).
data "google_project" "this" {
  count      = var.public_hostname == null ? 1 : 0
  project_id = var.project_id

  depends_on = [google_project.prod]
}

# Named `prod` because prod's state knows it by that name. Renaming it needs a `moved` block.
resource "google_project" "prod" {
  count           = var.create_project ? 1 : 0
  project_id      = var.project_id
  name            = var.project_id
  billing_account = var.billing_account
  org_id          = var.org_id

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false

  depends_on = [google_project.prod]
}

# --- Service accounts -----------------------------------------------------------------

# What the app and worker run as.
resource "google_service_account" "runtime" {
  account_id   = local.runtime_sa_id
  display_name = "${local.env_title} app and worker"

  depends_on = [google_project_service.apis]
}

# What the nightly database backup runs as. Kept apart from `runtime` so the backup token is
# out of reach of the app and worker, which handle uploaded files (DIC-2540).
resource "google_service_account" "db_backup" {
  count        = local.db_backup_count
  account_id   = local.backup_sa_id
  display_name = "${local.env_title} nightly database backup"

  depends_on = [google_project_service.apis]
}

# What builds run as, and what starts the worker on its schedule.
resource "google_service_account" "deployer" {
  account_id   = local.deployer_sa_id
  display_name = "${local.env_title} deploys and worker schedule"

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "deployer" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/run.developer",
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = google_service_account.deployer.member
}

# Lets the deployer deploy things that run as the runtime account, and nothing else.
resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

resource "google_service_account_iam_member" "deployer_acts_as_db_backup" {
  count              = local.db_backup_count
  service_account_id = google_service_account.db_backup[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

# --- Images ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "dice" {
  repository_id = local.repo_id
  location      = var.region
  format        = "DOCKER"

  # Old images are cleaned up only where asked (the OCR image is ~12 GB). In dry-run mode
  # nothing is deleted; Google only logs what it would delete.
  cleanup_policy_dry_run = var.image_cleanup_dry_run

  dynamic "cleanup_policies" {
    for_each = var.delete_images_older_than_days == null ? [] : [1]
    content {
      id     = "delete-old"
      action = "DELETE"
      condition {
        tag_state  = "ANY"
        older_than = "${var.delete_images_older_than_days * 86400}s"
      }
    }
  }

  dynamic "cleanup_policies" {
    for_each = var.keep_recent_image_versions == null ? [] : [1]
    content {
      id     = "keep-recent"
      action = "KEEP"
      most_recent_versions {
        keep_count = var.keep_recent_image_versions
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# --- Secrets --------------------------------------------------------------------------

resource "google_secret_manager_secret" "this" {
  for_each  = merge(local.secrets, local.backup_secrets)
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "runtime_reads" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.this[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.runtime.member
}

# The backup job reads only the secrets it uses.
resource "google_secret_manager_secret_iam_member" "db_backup_reads" {
  for_each = local.db_backup_count == 1 ? merge(
    { for name in local.backup_shared_secret_names : name => local.secrets[name] },
    local.backup_secrets,
  ) : {}
  secret_id = google_secret_manager_secret.this[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.db_backup[0].member
}
