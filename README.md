# Dice Document Pipeline API

A Django API that takes an uploaded PDF and checks it against accessibility standards (WCAG 2.1 AA / PDF/UA-1). Upon failure, it runs the document through an automated remediation pipeline (OCR, tagging, metadata fixes, alt text, link repair) before re-checking it.

## Setup

Docker only — the current implementation depends on Postgres, Java/veraPDF, and the OpenDataLoader hybrid server.

### 1. Docker

```bash
cp .env.example .env   # then set SECRET_KEY (see comment in the file); DATABASE_URL is prefilled
```

Other make commands include:

```bash
make init             # first time only convenience method: build the images, migrate, then start the app
make build            # rebuild the images (needed after ANY app source change, not just requirements*.txt/Dockerfile* — the app image doesn't live-mount source)
make migrate          # apply migrations to the running Postgres
make migrations       # generate new migration files from model changes (rebuild after)
make up               # start the app (without rebuilding or migrating)
make down             # stop everything
make recreate         # a convenience method that bundles down, build, and up - use when you edit .env on an already-running container

make shell            # shell inside the app container
make pyshell          # Django shell (manage.py shell) inside the app container
make test             # run the test suite

make verapdf-version  # confirm veraPDF/Java installed correctly
```

The app runs at `http://localhost:8000`.

### 2. Install the git hooks (if making changes)

Linting and type checking (ruff + mypy) run automatically on commit via pre-commit, and commit messages are checked against [Conventional Commits](https://www.conventionalcommits.org/) (`fix:`/`feat:`/etc. — see ADR 0012, this is what drives automatic version tagging).

Both run on your host machine's `git commit`, not inside Docker:

```bash
pip install pre-commit
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

> [!CAUTION]
> This be careful with squash-merging PRs - make sure the new commit message follows conventional commits, so that a new version is correctly issued.

### 3. Optional: use S3-compatible storage locally

By default, files are stored on local disk (`./media`). To instead exercise the same storage backend staging uses (ADR 0018) — a real object store, not local disk — bring up the bundled [Garage](https://garagehq.deuxfleurs.fr/):

```bash
make garage
```

Garage is deliberately excluded from plain `make up`/`make recreate` (it's behind a Compose profile) — `make garage` is the only thing that starts it, and it also pulls the pinned image and force-recreates the container, so it's safe to re-run any time you want a clean restart.

Garage needs its single-node "cluster" layout applied and a bucket + key created once before it's usable — unlike a lot of S3-compatible tools this can't be a single command, since Garage is built around a distributed-cluster model even when you're only running one node. Run these once:

```bash
# Find your node's ID (a long hex string) — it has no role assigned yet.
docker compose exec garage /garage status

# Assign it a single-node layout and apply it (bump --version if this isn't the first apply).
docker compose exec garage /garage layout assign -z dc1 -c 1G <node ID from above>
docker compose exec garage /garage layout apply --version 1

# Create a bucket, a key, and grant the key access.
docker compose exec garage /garage bucket create dice-local
docker compose exec garage /garage key create dice-local-key
docker compose exec garage /garage bucket allow --read --write --owner dice-local --key dice-local-key

# Print the access key ID / secret access key you'll need below.
docker compose exec garage /garage key info dice-local-key --show-secret
```

> [!NOTE]
> These are the current Garage v2.4.1 CLI commands per its own quick-start docs. If a command errors, check `docker compose exec garage /garage --help` (or the subcommand's own `--help`) — exact flags occasionally shift between Garage releases.

Then add the printed access/secret key to `.env` and `make recreate`:

```bash
S3_BUCKET_NAME=dice-local
S3_ENDPOINT_URL=http://garage:3900
S3_ACCESS_KEY=<printed above>
S3_SECRET_KEY=<printed above>
S3_REGION_NAME=garage
```

You can check if garage is correctly hooked up, or if an object exists inside of garage, with `default_storage`:
```
make pyshell
from django.core.files.storage import default_storage
default_storage.__class__ # Should be storages.backends.s3.S3Storage
default_storage.exists("remediations/<service_account_id>/<remediation_id>/filename.pdf")
# Like: default_storage.exists("remediations/1/402da5654f5cb986c12577a5f2c3aa8b415440c52fcb32bebd81958190a2da43/Wikipedia-Article.pdf")
# should return `True` if the file exists and `False` if it doesn't
```

## Pipeline steps

If all steps are enabled via environment variable, a document upload runs through the following steps in order (see [ADR 0003](docs/adrs/0003-pipeline-steps-and-branching.md) and [ADR 0010](docs/adrs/0010-fix-opendataloader-hybrid-tagging-upstream.md)):

1. `PrecheckService` → Verify with veraPDF
2. `OCRService` → OCR + build tagged PDF via OpenDataLoader
3. `FontRepairService` → Repair font `ToUnicode`/`CIDSet` (pikepdf)
4. `MetadataService` → Fix metadata (pikepdf) — `MarkInfo`/`Lang`/title/tab-order
5. `LinkService` → Tag links (pikepdf)
6. `AltTextService` → Alt text (Claude Vision)
7. `ScoringService` → Heuristic compliance score, non-blocking (pikepdf/PyMuPDF)
8. `PostCheckService` → Verify with veraPDF


## Testing the API

The API authenticates requests with a token tied to a `ServiceAccount`. To set one up:

1. Create an admin login (first time only): `docker compose run --rm app python manage.py createsuperuser`, then log in at `http://localhost:8000/admin/`.
2. Create an organization and service account. Open a Django shell (`make pyshell`) and run:

    ```python
    from accounts.models import Organization
    from accounts.services import ServiceAccountService

    org = Organization.objects.create(name="Test Org")
    account, token = ServiceAccountService().create(org, "test-service-account")
    print(token)  # save this — it can't be shown again
    print(account.webhook_secret)  # only needed if you use webhooks, to check they came from us
    ```

3. Need another token later (e.g. you lost it)? In the admin, go to **Service accounts**, tick the account, choose **Issue a new token** from the action menu, and click **Go**. The token appears in the message at the top of the page, only that once. Older tokens keep working.

### With Postman

#### Submit a document

1. New request: `POST http://localhost:8000/api/submit-document/` (or whatever URL you've deployed to)
2. Headers: Key: `Authorization`, Value: `Token <the token you printed above>`
3. Body: Choose `form-data` radio button. Add key `file`, change its type (in next column) from "Text" to `File`, and pick a PDF.
   - Optional: add a key `callback_url` (type Text) with the URL to notify when the job finishes, e.g. `https://example.com/webhooks/remediation`. See [Get a webhook instead of polling](#get-a-webhook-instead-of-polling) below.
4. Send. The response is the remediation job, with `status` `queued`. The worker processes it in the background; check its status (below) or register a webhook to find out when it's done.

The response will contain an `id` field with the remediation job id and a `document_id` that is a hashed key for your document; save this if you want to check status later.

#### Resubmit a document

A document that has been processed by the pipeline is hashed, so that future attempts of the same file by the same organization return the previous result without re-running the whole pipeline.

If you would like to force re-running the pipeline, such as during testing, click the radio button for `form-data` and next to the `file` key, add a second key, `force`, type Text, value `true`.

#### Check a submitted document's status

Submissions return right away with `status` `queued`; the worker processes them in the background. To see how a job is doing, use the `document-status` endpoint:

1. New request: `GET http://localhost:8000/api/document-status/<document_id>/`, using the `document_id` from the submit response.
2. Same `Authorization` header as above.

You should see `status` in the response. `queued` means it's waiting for the worker, and `running` means it's in progress.

#### Download the finished document

1. New request: `GET http://localhost:8000/api/document-download/<document_id>/`, using the `document_id` from the submit response.
2. Same `Authorization` header as above.

This returns the actual PDF file, not JSON. It works even if the job `FAILED` — a partially-fixed document is still returned if any remediation happened before the failure.

#### Get a webhook instead of polling

Add a `callback_url` key (type Text) to the submit request's `form-data` body. When the job finishes, we'll POST a small JSON notice to that URL (with a `download_url` you can `GET` right away) instead of you having to poll `document-status`. Multiple different callers can each submit the same document with their own `callback_url` and all get notified independently.

## Worker Queue

`make up` also starts a `worker` container. Uploads don't get processed inside the upload request; they're put in a queue (a table in Postgres), and the worker picks them up and runs the pipeline in the background ([ADR 0020](docs/adrs/0020-database-backed-task-worker.md)). Watch it with `docker compose logs -f worker`. If uploads sit at `queued`, the worker isn't running.

Finished task records pile up in that table over time. Clear out old ones with `docker compose run --rm app python manage.py prune_db_task_results`; in a real deployment, run on a schedule.

## Deploying

Staging runs on Google Cloud Run — setup is in [docs/deploying-staging.md](docs/deploying-staging.md). After setup, every merge to `main` deploys to staging automatically ([ADR 0022](docs/adrs/0022-automated-staging-deploys.md)).

Deploy via Docker. Each deploy runs the image two ways:

- **App** — the image's default command (gunicorn). Serves the API and the admin, including the admin's styling (no separate file server needed).
- **Worker** — same image, run one of two ways; without it, uploads are accepted but never processed:
  - **Scheduled** (staging on Cloud Run): command `python manage.py run_queued_tasks`, started every 15 minutes. It exits straight away if nothing is queued, so it costs almost nothing when idle ([ADR 0023](docs/adrs/0023-scheduled-batch-worker.md)).
  - **Always on** (local docker-compose, or any host where that's simpler): command `python manage.py db_worker`, restarted automatically if it crashes.

On each deploy, run `python manage.py migrate` once, before the new version starts taking traffic. Also run `python manage.py prune_db_task_results` on a schedule (e.g. daily) to clear out old task records.

Settings a deploy must set (app **and** worker):

| Setting | What it's for |
|---|---|
| `SECRET_KEY` | Long random string; keep it secret. See `.env.example` for how to generate one. |
| `ALLOWED_HOSTS` | The domain(s) the app is served from, comma-separated. |
| `PUBLIC_BASE_URL` | The app's public address, e.g. `https://pipeline.example.org` — used in webhook download links. |
| `DATABASE_URL` | Postgres connection string. |
| `S3_BUCKET_NAME`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` (+ other `S3_*` as needed) | Where uploaded and finished PDFs are stored ([ADR 0018](docs/adrs/0018-s3-compatible-storage-backend.md)). |
| `SENTRY_DSN`, `SENTRY_ENVIRONMENT` | Error reporting, e.g. environment `staging` or `production`. |
| `RUN_PRECHECK`, `RUN_OCR`, … `RUN_POSTCHECK` | Which pipeline steps are switched on. |
| `OPENDATALOADER_HYBRID_URL` | Address of the OCR service. Must be private — reachable by the worker, not the internet. |
| `ANTHROPIC_API_KEY` | Only if `RUN_ALT_TEXT=True`. |
| `CSRF_TRUSTED_ORIGINS` | Optional — only if the admin is reached through a different domain than the app itself. |

The following are set by defaults: `DEBUG`: `False`,

## Dependency Upgrades

### Setting a Retry Floor

By default, re-running the same document from the same organization returns the result from the previous run unless `force=True` has been passed in with the `POST`.

However, upgrading a major dependency might mean that jobs that previously failed would now pass with the current pipeline. You can set the retry floor to
any version of the pipeline that has previously run a remediation job via the admin panel. If a file from a previously failed job that was run on a version of the pipeline older than the floor is sent by the same organization, it will automatically be retried to see if the version change has corrected whatever previously caused the file to fail.

### OpenDataloader and Docling

This repo is currently pinned to forked versions of OpenDataLoader and Docling due to the need for bug-fixes not present in the original files. We periodically check to see if the main images have been upgraded to include those bugfixes; once they have, we will pin to main branch.

**Whenever you bump a major dependency in `Dockerfile.opendataloader-hybrid`/`requirements-opendataloader-hybrid.txt`** (torch, the Docling fork, `opendataloader-pdf` itself), run the OCR regression check before and after to confirm real OCR/tagging output didn't silently change:

```bash
make check-ocr-regression
```

This runs the real `opendataloader-hybrid` OCR/tagging step against a couple of committed fixture PDFs (`remediation/tests/fixtures/original/`) and compares a text/structure fingerprint against a committed baseline (`remediation/tests/fixtures/ocr_regression_baseline/`). It's deliberately **not** part of `make test`/CI — it needs the real container running and is meant to be a manual check around a dependency bump, not a per-PR gate.

If a dependency bump causes a real, deliberately-verified output change (e.g. an upstream bug fix), regenerate the baseline instead of treating the mismatch as a failure:

```bash
make check-ocr-regression ARGS=--record
```

### VeraPDF

This repo currently validates against PDF/UA-1, not the newer PDF/UA-2, because PDF/UA-2 targets PDF 2.0's structure model and none of the available tooling (OpenDataLoader, pikepdf) produces PDF 2.0 output yet.

If the veraPDF version in the `Dockerfile` is ever upgraded, re-run `manage.py extract_verapdf_profile --jar-path <path to the new cli jar>` to refresh `remediation/adapters/verification/pdfua1_catalog.json`, then review `remediation/adapters/verification/severity.py`'s severity tables against whatever changed before bumping `BUILT_AGAINST_VERAPDF_VERSION`.


---- Notes for later, ignore for now----

## Historical Data

## Problem Definition

In the first half of 2026, Jerry Happel and Drake Olejniczak from the [Van Buren County Digital Information Department](https://vanburencountymi.gov/departments/departments-offices/digital-information/) were facing an interesting and pressing problem: the [Van Buren County website](https://vanburencountymi.gov) contained almost 4000 media files, including thousands of PDF files that would not meet the WCAG 2.1 Level AA technical standard required by ADA Title II web and mobile accessibility compliance.

## History: the v1 prototype

Their first answer, [Dice Document Pipeline](https://github.com/vanburencountymi-digital-information/dice-document-pipeline), proved AI-assisted remediation could work at high quality: it drove Adobe Acrobat Pro through COM automation to OCR and auto-tag PDFs on a Windows desktop, then ran a Python pass for Claude-assisted alt text and compliance scoring. It batch-remediated over a thousand documents successfully, but needed a Windows desktop with Acrobat installed and a person to manually scrape, download, and re-upload documents — not something that scales to an ongoing pipeline.

This repo is a from-scratch rebuild as a proper API: Django + DRF, no Windows/Acrobat dependency, engine choices re-evaluated rather than inherited from v1 (see the ADRs linked above for what was kept, what changed, and why).
