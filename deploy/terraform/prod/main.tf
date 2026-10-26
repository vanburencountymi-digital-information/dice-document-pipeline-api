# Prod is one use of the shared setup in ../modules/dice. Staging is another.
module "dice" {
  source = "../modules/dice"

  project_id            = var.project_id
  create_project        = var.create_project
  billing_account       = var.billing_account
  org_id                = var.org_id
  region                = var.region
  public_hostname       = var.public_hostname
  extra_allowed_hosts   = var.extra_allowed_hosts
  deploy_cloud_run      = var.deploy_cloud_run
  app_image             = var.app_image
  ocr_image             = var.ocr_image
  s3_bucket_name        = var.s3_bucket_name
  s3_endpoint_url       = var.s3_endpoint_url
  db_backup_bucket_name = var.db_backup_bucket_name

  # App CPU: only while handling requests (cheaper when idle), with extra CPU while a new
  # container starts. Same as staging.
  cpu_idle          = true
  startup_cpu_boost = true

  # Same image cleanup as staging: delete images older than 30 days, but always keep the
  # newest 2 of each.
  delete_images_older_than_days = 30
  keep_recent_image_versions    = 2

  # Prod's own worker and OCR sizes, set here so a change to the module's defaults can't
  # change prod. Raise them here, after a long document has been timed with more.
  worker_cpu    = "1"
  worker_memory = "2Gi"
  ocr_cpu       = "4"
  ocr_memory    = "12Gi"
}
