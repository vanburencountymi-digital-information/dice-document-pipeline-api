# The Cloudflare Worker that sits between the public hostname and Cloud Run (ADR 0041).
# Cloud Run routes by `Host`, so a plain Cloudflare proxy to the `run.app` address can't work.
# Needs `CLOUDFLARE_API_TOKEN` in the environment (see README.md).
provider "cloudflare" {}

locals {
  worker_name = "ada-api-proxy"
}

# Only once the app exists: the Worker's target is the app's `run.app` host.
resource "cloudflare_workers_script" "proxy" {
  count = var.deploy_cloud_run ? 1 : 0

  account_id     = var.cloudflare_account_id
  script_name    = local.worker_name
  content_file   = "${path.module}/worker/index.js"
  content_sha256 = filesha256("${path.module}/worker/index.js")
  main_module    = "index.js"

  bindings = [{
    name = "ORIGIN_HOST"
    type = "plain_text"
    text = trimprefix(module.dice.app_url, "https://")
  }]

  # `ORIGIN_LOCK_SECRET` is set by hand so it stays out of state (README.md). Without this,
  # the next apply would drop it.
  keep_bindings = ["secret_text"]
}

# Creates the DNS record and the certificate for the hostname. Any existing DNS record for it
# must be deleted first.
resource "cloudflare_workers_custom_domain" "proxy" {
  count = var.deploy_cloud_run ? 1 : 0

  account_id = var.cloudflare_account_id
  zone_id    = var.cloudflare_zone_id
  hostname   = var.public_hostname
  service    = cloudflare_workers_script.proxy[0].script_name
}
