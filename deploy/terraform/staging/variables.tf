# The settings staging sets in terraform.tfvars. Everything else uses the module's defaults
# (modules/dice/variables.tf explains each one).
variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "extra_allowed_hosts" {
  description = "Other hostnames the app must accept, e.g. its older dice-app-....run.app address."
  type        = list(string)
  default     = []
}
variable "app_image" {
  description = "Only used if the app doesn't exist yet. Later deploys change the image outside OpenTofu."
  type        = string
  default     = null
}
variable "ocr_image" {
  description = "Only used if the worker job doesn't exist yet."
  type        = string
  default     = null
}
variable "s3_bucket_name" {
  description = "Staging's own R2 bucket for documents."
  type        = string
}
variable "s3_endpoint_url" { type = string }
