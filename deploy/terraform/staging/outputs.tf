output "app_url" {
  description = "The address the app accepts requests on. (Cloud Run's own dice-app-HASH-uc.a.run.app address is rejected as a disallowed host.)"
  value       = module.dice.public_url
}

output "image_repository" {
  value = module.dice.image_repository
}

output "secret_names" {
  description = "Secrets to fill in by hand."
  value       = module.dice.secret_names
}
