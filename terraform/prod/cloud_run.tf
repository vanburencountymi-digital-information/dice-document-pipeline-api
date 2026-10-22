# Everything here waits for var.deploy_cloud_run: Cloud Run refuses to start without its
# images and secret values. After the first apply, Cloud Build changes the images on every
# release, so Terraform ignores them (otherwise it would roll back each deploy).

locals {
  secret_env = local.secrets
}

resource "google_cloud_run_v2_service" "app" {
  count               = var.deploy_cloud_run ? 1 : 0
  name                = "dice-app"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = var.app_min_instances
      max_instance_count = var.app_max_instances
    }

    containers {
      image = var.app_image

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this[env.key].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].revision,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.runtime_reads,
    google_service_account_iam_member.deployer_acts_as_runtime,
  ]
}

# "Public" only lets requests reach the app. The API still needs its own tokens.
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.deploy_cloud_run ? 1 : 0
  name     = google_cloud_run_v2_service.app[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job" "migrate" {
  count               = var.deploy_cloud_run ? 1 : 0
  name                = "dice-migrate"
  location            = var.region
  deletion_protection = true

  template {
    template {
      service_account = google_service_account.runtime.email
      max_retries     = 0

      containers {
        image   = var.app_image
        command = ["python"]
        args    = ["manage.py", "migrate"]

        dynamic "env" {
          for_each = local.plain_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = local.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.this[env.key].secret_id
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [google_secret_manager_secret_iam_member.runtime_reads]
}

# The worker job has two containers, named `worker` and `ocr`: cloudbuild.yaml updates
# them by name. There is no startup dependency between them on purpose (the worker waits
# for OCR itself, and only when there is work).
resource "google_cloud_run_v2_job" "worker" {
  count               = var.deploy_cloud_run ? 1 : 0
  name                = "dice-worker"
  location            = var.region
  deletion_protection = true

  template {
    template {
      service_account = google_service_account.runtime.email
      timeout         = "${var.worker_timeout_seconds}s"
      max_retries     = 0

      containers {
        name    = "worker"
        image   = var.app_image
        command = ["python"]
        args    = ["manage.py", "run_queued_tasks"]

        resources {
          limits = {
            cpu    = var.worker_cpu
            memory = var.worker_memory
          }
        }

        dynamic "env" {
          for_each = merge(local.plain_env, { OPENDATALOADER_HYBRID_URL = "http://localhost:5002" })
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = local.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.this[env.key].secret_id
                version = "latest"
              }
            }
          }
        }
      }

      containers {
        name  = "ocr"
        image = var.ocr_image

        resources {
          limits = {
            cpu    = var.ocr_cpu
            memory = var.ocr_memory
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      template[0].template[0].containers[1].image,
      client,
      client_version,
    ]
  }

  depends_on = [google_secret_manager_secret_iam_member.runtime_reads]
}

resource "google_cloud_scheduler_job" "worker" {
  count     = var.deploy_cloud_run ? 1 : 0
  name      = "dice-worker-scheduler-trigger"
  region    = var.region
  schedule  = var.worker_schedule
  time_zone = var.worker_schedule_time_zone
  paused    = var.worker_schedule_paused

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.worker[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.deployer.email
    }
  }

  depends_on = [google_project_iam_member.deployer]
}
