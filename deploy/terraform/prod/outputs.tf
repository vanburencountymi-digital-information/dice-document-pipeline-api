output "app_url" {
  description = "The app's run.app address (add its hostname to extra_allowed_hosts if it is used directly)."
  value       = var.deploy_cloud_run ? google_cloud_run_v2_service.app[0].uri : null
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${local.repo_id}"
}

output "secret_names" {
  description = "Secrets to fill in by hand."
  value       = values(local.secrets)
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}
