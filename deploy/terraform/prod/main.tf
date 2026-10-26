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

  # Same image cleanup as staging: delete images older than 30 days, but always keep the
  # newest 2 of each.
  startup_cpu_boost             = true
  delete_images_older_than_days = 30
  keep_recent_image_versions    = 2

  # Prod's own worker and OCR sizes, set here so a change to the module's defaults can't
  # change prod. Raise them here, after a long document has been timed with more.
  worker_cpu    = "1"
  worker_memory = "2Gi"
  ocr_cpu       = "4"
  ocr_memory    = "12Gi"
}

# Prod was applied before the module existed. These tell OpenTofu the resources only moved
# in the code, so it doesn't delete and re-create them. Safe to delete once every
# environment's state has been applied with them.
moved {
  from = google_project.prod
  to   = module.dice.google_project.prod
}
moved {
  from = google_project_service.apis
  to   = module.dice.google_project_service.apis
}
moved {
  from = google_service_account.runtime
  to   = module.dice.google_service_account.runtime
}
moved {
  from = google_service_account.db_backup
  to   = module.dice.google_service_account.db_backup[0]
}
moved {
  from = google_service_account.deployer
  to   = module.dice.google_service_account.deployer
}
moved {
  from = google_project_iam_member.deployer
  to   = module.dice.google_project_iam_member.deployer
}
moved {
  from = google_service_account_iam_member.deployer_acts_as_runtime
  to   = module.dice.google_service_account_iam_member.deployer_acts_as_runtime
}
moved {
  from = google_service_account_iam_member.deployer_acts_as_db_backup
  to   = module.dice.google_service_account_iam_member.deployer_acts_as_db_backup[0]
}
moved {
  from = google_artifact_registry_repository.dice
  to   = module.dice.google_artifact_registry_repository.dice
}
moved {
  from = google_secret_manager_secret.this
  to   = module.dice.google_secret_manager_secret.this
}
moved {
  from = google_secret_manager_secret_iam_member.runtime_reads
  to   = module.dice.google_secret_manager_secret_iam_member.runtime_reads
}
moved {
  from = google_secret_manager_secret_iam_member.db_backup_reads
  to   = module.dice.google_secret_manager_secret_iam_member.db_backup_reads
}
moved {
  from = google_cloud_run_v2_service.app
  to   = module.dice.google_cloud_run_v2_service.app
}
moved {
  from = google_cloud_run_v2_service_iam_member.public
  to   = module.dice.google_cloud_run_v2_service_iam_member.public
}
moved {
  from = google_cloud_run_v2_job.migrate
  to   = module.dice.google_cloud_run_v2_job.migrate
}
moved {
  from = google_cloud_run_v2_job.worker
  to   = module.dice.google_cloud_run_v2_job.worker
}
moved {
  from = google_cloud_run_v2_job.db_backup
  to   = module.dice.google_cloud_run_v2_job.db_backup
}
moved {
  from = google_cloud_scheduler_job.worker
  to   = module.dice.google_cloud_scheduler_job.worker
}
moved {
  from = google_cloud_scheduler_job.db_backup
  to   = module.dice.google_cloud_scheduler_job.db_backup
}
