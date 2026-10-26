terraform {
  # We run this with OpenTofu (`tofu`). 1.10+ for `use_lockfile` (state locking on R2).
  required_version = ">= 1.10"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Staging's own state file, kept apart from prod's. Pass the details with
  # `tofu init -backend-config=backend.hcl` (see backend.hcl.example).
  backend "s3" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
