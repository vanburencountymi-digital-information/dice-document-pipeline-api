# Staging is one use of the shared setup in ../modules/dice. Prod is another.
# What differs from prod is set here.
module "dice" {
  source = "../modules/dice"

  environment        = "staging"
  sentry_environment = "staging"
  project_id         = var.project_id
  region             = var.region

  # No custom domain: the app uses its own run.app address.
  public_hostname     = null
  extra_allowed_hosts = var.extra_allowed_hosts

  # No nightly backup here, and Cloudflare isn't in front of staging (no origin lock, ADR 0036).
  # That also means nothing limits traffic to staging's public run.app address except the
  # app's own throttling. Accepted: staging holds no real documents. Revisit if that changes.
  enable_db_backup   = false
  enable_origin_lock = false

  # Paused on purpose: someone runs the worker by hand (ADR 0029).
  worker_schedule_paused = true

  # Staging's real sizes, as set by hand. The module's defaults match neither environment.
  worker_cpu    = "2"
  worker_memory = "4Gi"
  ocr_memory    = "16Gi"

  # Staging is public through Cloud Run's invoker setting: the organization policy blocks the
  # `allUsers` grant that prod uses. Old images are cleaned up (OCR images are ~12 GB).
  startup_cpu_boost             = true
  invoker_iam_disabled          = true
  delete_images_older_than_days = 30
  keep_recent_image_versions    = 2

  deploy_cloud_run = true
  app_image        = var.app_image
  ocr_image        = var.ocr_image
  s3_bucket_name   = var.s3_bucket_name
  s3_endpoint_url  = var.s3_endpoint_url
}
