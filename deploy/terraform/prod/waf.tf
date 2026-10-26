# Cloudflare's own protection in front of the Worker (DIC-2346): managed WAF rules and a rate
# limit on `/api/`. Needs `Zone WAF: Edit` on the API token (README.md).
#
# Request body size: Cloudflare already refuses bodies over 100 MB on the Free and Pro plans, and
# that is the limit we want, so there is no rule for it. If the app's own maximum upload size
# (a later change) is set lower, add a custom rule here for that size.

# Cloudflare's free managed rules. A zone can have only one ruleset per phase, so if the zone
# already has one, import it first (README.md).
resource "cloudflare_ruleset" "managed_waf" {
  zone_id = var.cloudflare_zone_id
  name    = "Managed WAF"
  kind    = "zone"
  phase   = "http_request_firewall_managed"

  rules = [{
    action      = "execute"
    description = "Cloudflare Free Managed Ruleset"
    expression  = "true"
    enabled     = true
    action_parameters = {
      id = "77454fe2d30c4220b5701f6fdfb893ba"
    }
  }]
}

locals {
  # Callers that skip the rate limit, e.g. the WordPress host during the backfill. Empty by default.
  rate_limit_skip = length(var.backfill_ips) == 0 ? "" : " and not ip.src in {${join(" ", var.backfill_ips)}}"
}

# The second layer behind the app's own limits (ADR 0031: 120 requests a minute per IP). The Free
# plan allows one rule, counting per IP in a 10-second window, with a 10-second block. 40 requests
# in 10 seconds is more than a person or a normal upload client sends, and looser than the app's own
# limit, so the app still answers first for ordinary callers.
resource "cloudflare_ruleset" "rate_limit" {
  zone_id = var.cloudflare_zone_id
  name    = "API rate limit"
  kind    = "zone"
  phase   = "http_ratelimit"

  rules = [{
    action      = "block"
    description = "Rate limit /api/ per IP"
    expression  = "starts_with(http.request.uri.path, \"/api/\")${local.rate_limit_skip}"
    enabled     = true
    ratelimit = {
      characteristics     = ["ip.src"]
      period              = 10
      requests_per_period = var.api_rate_limit_per_10s
      mitigation_timeout  = 10
    }
  }]
}
