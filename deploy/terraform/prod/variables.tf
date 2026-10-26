# The settings prod sets in terraform.tfvars. Everything else uses the module's defaults
# (modules/dice/variables.tf explains each one).
variable "project_id" { type = string }
variable "create_project" {
  type    = bool
  default = false
}
variable "billing_account" {
  type    = string
  default = null
}
variable "org_id" {
  type    = string
  default = null
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "public_hostname" { type = string }
variable "extra_allowed_hosts" {
  type    = list(string)
  default = []
}
variable "deploy_cloud_run" {
  type    = bool
  default = false
}
variable "app_image" {
  type    = string
  default = null
}
variable "ocr_image" {
  type    = string
  default = null
}
variable "s3_bucket_name" { type = string }
variable "s3_endpoint_url" { type = string }
variable "db_backup_bucket_name" { type = string }
