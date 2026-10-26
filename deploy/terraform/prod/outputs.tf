output "app_url" {
  description = "The app's run.app address (add its hostname to extra_allowed_hosts if it is used directly)."
  value       = module.dice.app_url
}

output "image_repository" {
  value = module.dice.image_repository
}

output "secret_names" {
  description = "Secrets to fill in by hand."
  value       = module.dice.secret_names
}

output "runtime_service_account" {
  value = module.dice.runtime_service_account
}

output "deployer_service_account" {
  value = module.dice.deployer_service_account
}

output "db_backup_service_account" {
  value = module.dice.db_backup_service_account
}
