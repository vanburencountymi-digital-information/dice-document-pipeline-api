variable "environment" {
  description = "Short name of this environment, used in service account names (dice-ENVIRONMENT-runtime). Changing it for an existing environment renames its service accounts."
  type        = string
  default     = "prod"
}

variable "sentry_environment" {
  description = "Tag Sentry puts on this environment's errors."
  type        = string
  default     = "production"
}

variable "project_id" {
  description = "ID of the Google Cloud project (created here if create_project is true)."
  type        = string
}

variable "create_project" {
  description = "Create the project. Leave false if it already exists."
  type        = bool
  default     = false
}

variable "billing_account" {
  description = "Billing account ID. Only used when create_project is true."
  type        = string
  default     = null
}

variable "org_id" {
  description = "Organization ID. Only used when create_project is true; leave null for none."
  type        = string
  default     = null
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "public_hostname" {
  description = "Public hostname people use, without https://, e.g. api.example.org. Leave null to use the app's own run.app address."
  type        = string
  default     = null
}

variable "extra_allowed_hosts" {
  description = "Other hostnames the app must accept, e.g. the dice-app-....run.app one."
  type        = list(string)
  default     = []
}

# Cloud Run needs the images and secret values to exist first, so the first apply is
# done in two passes. See README.md.
variable "deploy_cloud_run" {
  description = "Create the app, jobs and schedule. Leave false until images are pushed and secret values are added."
  type        = bool
  default     = false
}

variable "app_image" {
  description = "Full app image, e.g. us-central1-docker.pkg.dev/PROJECT/dice/app:v1.2.3. Only the first deploy uses this; later deploys change the image outside Terraform."
  type        = string
  default     = null
}

variable "ocr_image" {
  description = "Full OCR image, e.g. us-central1-docker.pkg.dev/PROJECT/dice/opendataloader-hybrid:abc1234."
  type        = string
  default     = null
}

variable "s3_bucket_name" {
  description = "This environment's R2 bucket for documents (made by hand)."
  type        = string
}

variable "s3_endpoint_url" {
  description = "R2 S3 endpoint, https://ACCOUNT_ID.r2.cloudflarestorage.com."
  type        = string
}

variable "enable_db_backup" {
  description = "Make the nightly database backup job and its secrets. Staging leaves it off (its database is disposable)."
  type        = bool
  default     = true
}

variable "invoker_iam_disabled" {
  description = "Let requests reach the app by turning off Cloud Run's invoker check, instead of granting `allUsers`. Needed where the organization policy blocks `allUsers` and has not been overridden for the project (staging)."
  type        = bool
  default     = false
}

variable "delete_images_older_than_days" {
  description = "Delete images in the registry older than this many days. Null keeps them all."
  type        = number
  default     = null
}

variable "keep_recent_image_versions" {
  description = "Never delete the newest N versions of each image, whatever their age. Null sets no such rule."
  type        = number
  default     = null
}

variable "image_cleanup_dry_run" {
  description = "Only log which images the cleanup rules would delete, without deleting any. Use it for the first apply, check the log, then turn it off."
  type        = bool
  default     = false
}

variable "cpu_idle" {
  description = "true: the app's CPU is only allocated while it handles requests (cheaper when idle). null: leave Cloud Run's default, which keeps CPU allocated all the time."
  type        = bool
  default     = null
}

variable "startup_cpu_boost" {
  description = "Give the app extra CPU while a container starts (faster first request after idle). Billed for the boosted CPU during startup only."
  type        = bool
  default     = false
}

variable "enable_origin_lock" {
  description = "Make the origin lock secret (ADR 0036). Only needed when Cloudflare sits in front of the app."
  type        = bool
  default     = true
}

variable "db_backup_bucket_name" {
  description = "The private R2 bucket for nightly database backups (made by hand; not the documents bucket). Needed when enable_db_backup is true."
  type        = string
  default     = null

  validation {
    condition     = var.db_backup_bucket_name != null || !var.enable_db_backup
    error_message = "Set db_backup_bucket_name, or set enable_db_backup = false."
  }
}

variable "db_backup_schedule" {
  description = "When the nightly database backup runs (cron)."
  type        = string
  default     = "0 3 * * *"
}

variable "run_steps" {
  description = "Which pipeline steps are on."
  type        = map(bool)
  default = {
    RUN_PRECHECK          = true
    RUN_OCR               = true
    RUN_FONT_REPAIR       = true
    RUN_FINALIZE_METADATA = true
    RUN_LINK_TAG          = true
    RUN_ALT_TEXT          = true
    RUN_SCORING           = true
    RUN_POSTCHECK         = true
  }
}

variable "app_min_instances" {
  description = "Instances kept running when idle. 0 lets the app scale to zero."
  type        = number
  default     = 0
}

variable "app_max_instances" {
  description = "Caps cost and the number of database connections."
  type        = number
  default     = 3
}

variable "worker_schedule" {
  description = "How often the worker starts (cron)."
  type        = string
  default     = "*/20 * * * *"
}

variable "worker_schedule_time_zone" {
  description = "Time zone for the worker and backup schedules."
  type        = string
  default     = "America/Detroit"
}

variable "worker_schedule_paused" {
  description = "Keep the worker's schedule paused (it can still be run by hand)."
  type        = bool
  default     = false
}

# Timeout raised to 3 hours so long documents aren't killed (DIC-2480). Each environment
# sets its own worker and OCR sizes (see prod/main.tf and staging/main.tf); these defaults
# belong to neither.
variable "worker_timeout_seconds" {
  description = "How long one worker run may last before Cloud Run stops it."
  type        = number
  default     = 10800
}

variable "worker_cpu" {
  description = "CPUs for the worker container."
  type        = string
  default     = "1"
}

variable "worker_memory" {
  description = "Memory for the worker container, e.g. 2Gi."
  type        = string
  default     = "2Gi"
}

variable "ocr_cpu" {
  description = "Cloud Run only accepts 1, 2, 4, 6 or 8 here."
  type        = string
  default     = "4"
}

variable "ocr_memory" {
  description = "Memory for the OCR container, e.g. 12Gi."
  type        = string
  default     = "12Gi"
}
