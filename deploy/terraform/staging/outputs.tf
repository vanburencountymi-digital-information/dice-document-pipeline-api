output "app_url" {
  description = "The app's run.app address."
  value       = module.dice.app_url
}

output "image_repository" {
  value = module.dice.image_repository
}

output "secret_names" {
  description = "Secrets to fill in by hand."
  value       = module.dice.secret_names
}
