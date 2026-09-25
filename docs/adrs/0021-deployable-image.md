# 21. Deployable image

## Status

Accepted

## Context

We're deploying to Google Cloud (staging first, then production). The app image was a development image: it ran Django's `runserver`, which isn't built for real traffic, and nothing served static files once `DEBUG=False`, so the admin — which we use to manage service accounts — would render without its CSS/JS. There was also no single list of the settings a deploy needs.

## Decision

- **One image for every deployed environment.** Staging and production run the same built image; only environment variables differ. What passed in staging is exactly what goes to production. Local dev uses the same image too, with docker-compose overriding the command to `runserver`.
- **gunicorn** is the image's default command, bound to `$PORT` (Cloud Run sets this) with Cloud Run's recommended starting settings: 1 worker, 8 threads, gunicorn's own timeout off. Tunable without a rebuild via `GUNICORN_CMD_ARGS`.
- **WhiteNoise serves static files from inside the app.** `collectstatic` runs at image build; WhiteNoise serves the compressed, fingerprinted files with long cache headers. No separate static file host, CDN, or bucket — this is an API, and its only static files are the admin's, so a separate host would add a deploy step and moving parts for no real gain. Media (PDFs) still goes to the storage bucket, unchanged.
- **Proxy-aware settings.** Cloud Run handles HTTPS and forwards plain HTTP, so Django trusts the `X-Forwarded-Proto` header (`SECURE_PROXY_SSL_HEADER`). `SECURE_COOKIES` (HTTPS-only session/CSRF cookies) defaults to on whenever `DEBUG` is off, so deploys are secure without setting anything; local dev (`DEBUG=True`) keeps plain-http login working. `CSRF_TRUSTED_ORIGINS` is env-driven and empty by default.
- **Postgres everywhere, `DATABASE_URL` required.** The SQLite fallback is gone: local dev (docker-compose's `db` service), CI, staging and production all use Postgres, so nothing behaves differently between them. A deploy that forgets `DATABASE_URL` fails at startup rather than silently writing to a throwaway database inside the container, which on Cloud Run would be lost on restart and not shared with the worker.
- **`DEBUG=False` in staging as well as production.** Debug pages expose code, settings and data to whoever triggers an error; staging uses Sentry (`SENTRY_ENVIRONMENT=staging`) to see errors instead, and stays as close to production as possible.

## Consequences

- The worker runs the same image with `python manage.py db_worker` ([ADR 0020](0020-database-backed-task-worker.md)); the OCR service keeps its own image.
- `python manage.py check --deploy` will still warn about HSTS and SSL redirect. Cloud Run already serves HTTPS only, so these are left off on purpose; revisit if we ever deploy somewhere that also serves plain HTTP.
- If static files ever grow large or get heavy public traffic, they can move to a bucket/CDN later by changing the `staticfiles` storage setting — no other code changes.
- Tests use plain static file storage (`config/test_settings.py`), since they don't run `collectstatic`.
