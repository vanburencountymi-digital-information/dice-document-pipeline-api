variable "project_id" {
  description = "ID of the prod Google Cloud project (created here if create_project is true)."
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
  description = "Public hostname people use, without https://, e.g. api.example.org."
  type        = string
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
  description = "The prod R2 bucket for documents (made by hand)."
  type        = string
}

variable "s3_endpoint_url" {
  description = "R2 S3 endpoint, https://ACCOUNT_ID.r2.cloudflarestorage.com."
  type        = string
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
  type    = number
  default = 1
}

variable "app_max_instances" {
  description = "Caps cost and the number of database connections."
  type        = number
  default     = 3
}

variable "worker_schedule" {
  type    = string
  default = "*/20 * * * *"
}

variable "worker_schedule_time_zone" {
  type    = string
  default = "America/Detroit"
}

variable "worker_schedule_paused" {
  description = "Keep the worker's schedule paused (it can still be run by hand)."
  type        = bool
  default     = false
}

# Starting values copied from staging. The timeout and CPU are due to change (DIC-2480).
variable "worker_timeout_seconds" {
  type    = number
  default = 3600
}

variable "worker_cpu" {
  type    = string
  default = "1"
}

variable "worker_memory" {
  type    = string
  default = "2Gi"
}

variable "ocr_cpu" {
  type    = string
  default = "3"
}

variable "ocr_memory" {
  type    = string
  default = "12Gi"
}
