# Staging was set up by hand first. These tell OpenTofu about what already exists, so the
# first plan adopts it instead of trying to create it again. They do nothing once the
# resources are in state, and can be deleted then.
#
# The lists below copy names from ../modules/dice (the APIs, the secrets, the service accounts
# and the Cloud Run names). If the module changes any of them before the first apply, change
# them here too, or the plan will try to create something that already exists.
#
# Not listed: IAM grants. They only add a permission, so creating one that already exists
# changes nothing.

locals {
  imported_apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
  ]
  imported_secrets = {
    SECRET_KEY        = "dice-secret-key"
    DATABASE_URL      = "dice-database-url"
    S3_ACCESS_KEY     = "dice-s3-access-key"
    S3_SECRET_KEY     = "dice-s3-secret-key"
    SENTRY_DSN        = "dice-sentry-dsn"
    ANTHROPIC_API_KEY = "dice-anthropic-api-key"
  }
  sa_domain = "${var.project_id}.iam.gserviceaccount.com"
}

import {
  for_each = toset(local.imported_apis)
  to       = module.dice.google_project_service.apis[each.value]
  id       = "${var.project_id}/${each.value}"
}

import {
  to = module.dice.google_service_account.runtime
  id = "projects/${var.project_id}/serviceAccounts/dice-staging-runtime@${local.sa_domain}"
}

import {
  to = module.dice.google_service_account.deployer
  id = "projects/${var.project_id}/serviceAccounts/dice-staging-deployer@${local.sa_domain}"
}

import {
  to = module.dice.google_artifact_registry_repository.dice
  id = "projects/${var.project_id}/locations/${var.region}/repositories/dice"
}

import {
  for_each = local.imported_secrets
  to       = module.dice.google_secret_manager_secret.this[each.key]
  id       = "projects/${var.project_id}/secrets/${each.value}"
}

import {
  to = module.dice.google_cloud_run_v2_service.app[0]
  id = "projects/${var.project_id}/locations/${var.region}/services/dice-app"
}

import {
  to = module.dice.google_cloud_run_v2_job.migrate[0]
  id = "projects/${var.project_id}/locations/${var.region}/jobs/dice-migrate"
}

import {
  to = module.dice.google_cloud_run_v2_job.worker[0]
  id = "projects/${var.project_id}/locations/${var.region}/jobs/dice-worker"
}

import {
  to = module.dice.google_cloud_scheduler_job.worker[0]
  id = "projects/${var.project_id}/locations/${var.region}/jobs/dice-worker-scheduler-trigger"
}
