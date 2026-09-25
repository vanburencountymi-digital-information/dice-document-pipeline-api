# 22. Automated staging deploys

## Status

Accepted

## Context

Checks and deploys were manual. The Django tests ran on every pull request, but lint and type checks only ran through local pre-commit hooks, which can be skipped, and nothing stopped a PR from merging with failures. Deploying to staging ([ADR 0021](0021-deployable-image.md)'s image on Google Cloud Run) meant building and pushing images by hand, running migrations, then updating each service — easy to get wrong or skip a step.

Each environment has its own database (staging's is separate from production's), so migrations always run on staging first: a bad migration breaks staging, not production.

## Decision

- **Pull requests must pass two checks to merge:** `test` (the Django suite on Postgres) and `lint` (the same pre-commit hooks developers run locally: ruff, ruff-format, mypy, file checks). Each has its own workflow file (`.github/workflows/test.yml`, `.github/workflows/lint.yml`); the block itself is a GitHub setting on the `main` ruleset ("Require status checks to pass").
- **Release tags deploy staging automatically.** Merging to `main` makes `release.yml` push a `vX.Y.Z` tag ([ADR 0012](0012-automatic-semantic-versioning.md)), which triggers Cloud Build (`cloudbuild.yaml`) to: build and push the app image, versioned by the tag → run migrations → update the app → update the worker job's `worker` container ([ADR 0023](0023-scheduled-batch-worker.md)). We use Cloud Build because staging is already on Google Cloud; the steps themselves are plain `docker` and `gcloud` commands.
- **Migrations run once per deploy, as a Cloud Run job, before any new code is live** — never at container startup, where several containers would race to run them. If they fail, the build stops and nothing new is deployed.
- **Deploy steps only update images** (`update`, not `deploy`), keeping the settings, secrets and schedule configured in the Console.
- **The OCR image is built on demand** (`cloudbuild-ocr.yaml`, run by hand): it's ~12 GB, slow to build, and rarely changes.
- **Production, later:** the same pipeline with its own trigger, project, database and bucket, plus Cloud Build's manual approval step before it runs.

## Consequences

- **Migrations must stay backward compatible for one release.** During a deploy, the previous version keeps running for a moment after migrations finish. Adding tables or columns is always safe; renaming or removing something is split across two releases (stop using it first, remove it in the next).
- The Cloud Run app, worker job and migrations job are created once by hand ([docs/deploying-staging.md](../deploying-staging.md)); the pipeline only updates them. Container names `worker` and `ocr` in the worker job are relied on.
- A broken pipeline isn't a dead end: the same steps can be run by hand (the doc's appendix).
- CI's lint job installs the mypy hook's own dependencies, so it takes a few minutes the first time; later runs reuse a cache.
