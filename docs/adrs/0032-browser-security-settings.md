# 32. Browser security settings

## Status

Accepted. Amends [ADR 0021](0021-deployable-image.md), which left HSTS and the https redirect off.

## Context

ADR 0021 left these off because Cloud Run only serves https. That reasoning doesn't cover other hosts, and we want to be able to move (see the portability aim in earlier ADRs). They also cost nothing to turn on, and `check --deploy` was still warning about them.

## Decision

- **On whenever the new `REQUIRE_HTTPS` setting is True** (the default), along with the HTTPS-only login/CSRF cookies (previously a separate `SECURE_COOKIES` setting, now removed). It defaults to on, so a deployment that sets nothing is secure; only local development sets it to False (in `.env`; documented in the README and `.env.example`). The settings it controls sit together in one block in `config/settings.py`. It is separate from `DEBUG` so that error pages and security are decided independently.
  - `SECURE_SSL_REDIRECT`: plain-http requests are sent to https. Required everywhere except local development.
  - `SECURE_HSTS_SECONDS` (env-overridable): starts at one hour (3600), 0 when `REQUIRE_HTTPS` is False. Browsers can't be told to forget HSTS early, so we start short and raise it (up to a year) once https has been clean for a while.
  - `SECURE_CONTENT_TYPE_NOSNIFF` is on (already Django's default, now set in the file) and `SECURE_REFERRER_POLICY` is `same-origin`.
- **Not turned on:** HSTS for all subdomains, and HSTS preload. Both are effectively permanent and affect more than this app, so `security.W005` and `security.W021` are silenced on purpose.
- **CI** runs `manage.py check --deploy --fail-level WARNING` with `DEBUG=False` and a realistic secret key, so a new warning fails the build. The test settings turn the redirect off, since the test client uses plain http.
