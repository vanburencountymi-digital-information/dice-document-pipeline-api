locals {
  runtime_sa_id  = "dice-prod-runtime"
  deployer_sa_id = "dice-prod-deployer"
  dump_sa_id     = "dice-prod-db-dump"
  repo_id        = "dice"

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
  secrets = {
    SECRET_KEY         = "dice-secret-key"
    DATABASE_URL       = "dice-database-url"
    S3_ACCESS_KEY      = "dice-s3-access-key"
    S3_SECRET_KEY      = "dice-s3-secret-key"
    SENTRY_DSN         = "dice-sentry-dsn"
    ANTHROPIC_API_KEY  = "dice-anthropic-api-key"
    ORIGIN_LOCK_SECRET = "dice-origin-lock-secret"
  }

  # Only the nightly dump job gets these (a separate R2 bucket and token, DIC-2540). It runs
  # as its own service account, so the app and worker can't read them even from inside their
  # containers. They are not in `secrets` above, which the app and worker get.
  dump_secrets = {
    DB_DUMP_ACCESS_KEY = "dice-db-dump-access-key"
    DB_DUMP_SECRET_KEY = "dice-db-dump-secret-key"
  }

  # The shared secrets the dump job also needs, by the app's variable name.
  dump_shared_secret_names = ["SECRET_KEY", "DATABASE_URL", "SENTRY_DSN"]

  allowed_hosts = join(",", concat([var.public_hostname], var.extra_allowed_hosts))

  plain_env = merge(
    { for name, on in var.run_steps : name => title(tostring(on)) },
    {
      S3_BUCKET_NAME       = var.s3_bucket_name
      S3_ENDPOINT_URL      = var.s3_endpoint_url
      S3_ADDRESSING_STYLE  = "path"
      SENTRY_ENVIRONMENT   = "production"
      TRUSTED_PROXY_HOPS   = "1" # Cloud Run adds one X-Forwarded-For entry (ADR 0031)
      ALLOWED_HOSTS        = local.allowed_hosts
      CSRF_TRUSTED_ORIGINS = "https://${var.public_hostname}"
      PUBLIC_BASE_URL      = "https://${var.public_hostname}"
    },
  )
}

# --- Project and services -------------------------------------------------------------

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
  display_name = "Prod app and worker"

  depends_on = [google_project_service.apis]
}

# What the nightly database dump runs as. Kept apart from `runtime` so the backup token is
# out of reach of the app and worker, which handle uploaded files (DIC-2540).
resource "google_service_account" "db_dump" {
  account_id   = local.dump_sa_id
  display_name = "Prod nightly database dump"

  depends_on = [google_project_service.apis]
}

# What builds run as, and what starts the worker on its schedule.
resource "google_service_account" "deployer" {
  account_id   = local.deployer_sa_id
  display_name = "Prod deploys and worker schedule"

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

resource "google_service_account_iam_member" "deployer_acts_as_db_dump" {
  service_account_id = google_service_account.db_dump.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

# --- Images ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "dice" {
  repository_id = local.repo_id
  location      = var.region
  format        = "DOCKER"

  depends_on = [google_project_service.apis]
}

# --- Secrets --------------------------------------------------------------------------

resource "google_secret_manager_secret" "this" {
  for_each  = merge(local.secrets, local.dump_secrets)
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

# The dump job reads only the secrets it uses.
resource "google_secret_manager_secret_iam_member" "db_dump_reads" {
  for_each = merge(
    { for name in local.dump_shared_secret_names : name => local.secrets[name] },
    local.dump_secrets,
  )
  secret_id = google_secret_manager_secret.this[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.db_dump.member
}
