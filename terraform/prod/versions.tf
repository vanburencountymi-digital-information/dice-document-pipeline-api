terraform {
  # 1.10+ for `use_lockfile` (state locking on R2, which has no DynamoDB).
  required_version = ">= 1.10"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Where the state lives: a Cloudflare R2 bucket. The details (bucket, endpoint) are not
  # written here; pass them with `terraform init -backend-config=backend.hcl`
  # (see backend.hcl.example).
  backend "s3" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}
