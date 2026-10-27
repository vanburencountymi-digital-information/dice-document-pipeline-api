# Cloudflare Access in front of the Django admin (DIC-2347): anyone opening `/admin/` must first
# get a one-time PIN by email, and only the addresses in `admin_emails` are sent one. Django's own
# login still follows. Needs the Access permissions on the API token (README.md and
# docs/deploying-prod.md) and Zero Trust switched on for the account.

# Lets people log in with a code sent to their email. If the account already has this login
# method, import it first (docs/deploying-prod.md).
resource "cloudflare_zero_trust_access_identity_provider" "one_time_pin" {
  account_id = var.cloudflare_account_id
  name       = "One-time PIN"
  type       = "onetimepin"
  config     = {}
}

# Who gets in. A named list rather than a whole domain, so a new address is a reviewed change.
resource "cloudflare_zero_trust_access_policy" "admin" {
  account_id = var.cloudflare_account_id
  name       = "Admin users"
  decision   = "allow"
  include    = [for email in var.admin_emails : { email = { email = email } }]
}

# The gate itself. The path has no trailing slash, so it covers `/admin/` and everything under it.
resource "cloudflare_zero_trust_access_application" "admin" {
  account_id = var.cloudflare_account_id
  name       = "Django admin"
  type       = "self_hosted"

  destinations = [{
    type = "public"
    uri  = "${var.public_hostname}/admin"
  }]

  allowed_idps              = [cloudflare_zero_trust_access_identity_provider.one_time_pin.id]
  auto_redirect_to_identity = true
  session_duration          = "8h"

  policies = [{
    id         = cloudflare_zero_trust_access_policy.admin.id
    precedence = 1
  }]
}
