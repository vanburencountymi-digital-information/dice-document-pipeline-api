# Deploying to staging (Google Cloud Run)

Setting up staging, done almost entirely in the [Google Cloud Console](https://console.cloud.google.com) (the website). PDFs are stored in [Cloudflare R2](https://developers.cloudflare.com/r2/). Once it's set up, **every merge to `main` deploys to staging automatically** ([ADR 0022](adrs/0022-automated-staging-deploys.md)).

> Not yet run end to end. Console labels change from time to time; if a button isn't named exactly as written, look for the closest match. Steps marked *(check)* use options we couldn't test ahead of time.

Related: the image ([ADR 0021](adrs/0021-deployable-image.md)), the worker ([ADR 0020](adrs/0020-database-backed-task-worker.md)), storage ([ADR 0018](adrs/0018-s3-compatible-storage-backend.md)).

## How it fits together

```
Internet ──► dice-app (Cloud Run service, public)   ─┐
                                                     ├─► Cloud SQL (Postgres)
dice-worker (Cloud Run job, every 15 min)          ─┤
  ├── worker container: manage.py run_queued_tasks   └─► Cloudflare R2 bucket (PDFs)
  └── ocr container: opendataloader-hybrid :5002 (localhost only)
        ▲ started by Cloud Scheduler
```

- **dice-app** — the website and API people call.
- **dice-worker** — processes uploads in the background. Every 15 minutes it starts, checks for queued uploads, and exits straight away if there are none (seconds, almost free). If there are, it waits for OCR to load, processes them all, then exits ([ADR 0023](adrs/0023-scheduled-batch-worker.md)). OCR runs next to it as a second ("sidecar") container that only the worker can reach — never the internet.
- The app and worker run the **same image**; only their command differs.

**This isn't Google Cloud Tasks.** The upload queue lives in our own Postgres database ([ADR 0020](adrs/0020-database-backed-task-worker.md)); we don't use Google's task service, and its API never needs turning on. The worker is an ordinary Cloud Run *job* (a program that runs, finishes and exits) that Cloud Scheduler starts on a timer.

**Timing:** an upload waits up to ~15 minutes for the next run, plus a minute or two for OCR to load. Callers get a webhook when it's done, so that's fine. Webhook retries also go out on the next run.

**Cost:** roughly **$20–25/month** for staging at a few documents a day — mostly the database (~$10–12). The worker only costs anything while it's actually processing.

**How deploys work:** merge a PR → GitHub tags a new release (`vX.Y.Z`) → Cloud Build builds the app image, runs database migrations, then updates the app and worker. If migrations fail, it stops and nothing new is deployed.

## What already exists, and the names used here

| Thing | Name | Status |
|---|---|---|
| Google Cloud project | `ada-document-pipeline-staging` | Exists |
| Region | `us-central1` | Use for everything |
| R2 bucket | `ada-pipeline-test-bucket` | Exists |
| Image repository | `dice` | Create (step 3) |
| Database instance / database / user | `dice-staging` / `dice_document_pipeline` / `dice_app` | Create (step 4) |
| Runtime service account | `dice-staging-runtime` | Create (step 2) |
| Build service account | `dice-staging-deployer` | Create (step 3) |
| App / worker job / migrations job | `dice-app` / `dice-worker` / `dice-migrate` | Create (steps 8–10) |

Make sure `ada-document-pipeline-staging` is selected in the project dropdown at the top left of the Console before each step.

The bucket's name says "test" — fine for staging. Production should get its own bucket, just as it gets its own database.

## 1. Turn on the services

**APIs & Services → Enable APIs and services**, then enable each of: **Cloud Run Admin API**, **Cloud SQL Admin API**, **Artifact Registry API**, **Secret Manager API**, **Cloud Build API**, **Cloud Scheduler API**.

## 2. Create the identity the app and worker run as

1. **IAM & Admin → Service Accounts → Create service account**: `dice-staging-runtime`.
2. Grant it: **Cloud SQL Client**, **Secret Manager Secret Accessor**.

## 3. Connect GitHub and set up automatic builds

1. **Artifact Registry → Create repository**: name `dice`, format **Docker**, region `us-central1`.
2. **IAM & Admin → Service Accounts → Create service account**: `dice-staging-deployer` (what the build runs as). Grant it: **Artifact Registry Writer**, **Cloud Run Developer**, **Logs Writer**. *(check)*
3. Open `dice-staging-runtime` → **Permissions** → grant `dice-staging-deployer` the role **Service Account User** — this lets the build deploy things that run as the runtime account.
4. **Cloud Build → Repositories → Connect repository** → GitHub → pick `vanburencountymi-digital-information/dice-document-pipeline-api`. An organization admin may need to approve Google's "Cloud Build" GitHub App.
5. **Cloud Build → Triggers → Create trigger** — the automatic deploy:
   - Name `dice-staging-deploy`, region `us-central1`.
   - Event: **Push new tag**. Tag: `^v.*$`.
   - Configuration: **Cloud Build configuration file**, `cloudbuild.yaml`.
   - Service account: `dice-staging-deployer`.
6. **Create trigger** again — the OCR image, run by hand only:
   - Name `dice-ocr-build`, region `us-central1`.
   - Event: **Manual invocation**, branch `main`.
   - Configuration file: `cloudbuild-ocr.yaml`. Service account: `dice-staging-deployer`.
7. **Build the first images:** on the Triggers page, **Run** `dice-ocr-build`, then **Run** `dice-staging-deploy` (pick the latest `v…` tag). OCR takes a while (~12 GB).
   **Both runs will fail at their last steps this first time** — they try to update the app, worker job and migrations job, which you create in steps 8–10. That's expected: what matters now is that the build and push steps succeed. Check **Artifact Registry → dice** shows an `app` and an `opendataloader-hybrid` image.

## 4. Create the database

1. **SQL → Create instance → PostgreSQL**.
2. Version **PostgreSQL 18**, edition **Enterprise** (not Enterprise Plus — the cheaper machine sizes only exist on Enterprise), region `us-central1`, machine **Shared core → db-f1-micro** (the smallest; fine for staging, ~$10/month). *(check it's offered for PostgreSQL 18 — if not, pick the smallest shared-core option shown.)* Production should use a dedicated-core size.
3. When it's ready (several minutes): **Databases** tab → create `dice_document_pipeline`; **Users** tab → create `dice_app` with a password of **letters and numbers only** (it goes inside a web address later). Save it somewhere safe.
4. Its **Connection name** (Overview page) will be `ada-document-pipeline-staging:us-central1:dice-staging`.

## 5. Get keys for the R2 bucket (Cloudflare dashboard)

1. **R2 Object Storage → `ada-pipeline-test-bucket` → Settings**: confirm public access is **off**. Documents are downloaded through the app, never straight from the bucket.
2. **R2 Object Storage → Manage API tokens → Create API token**: permissions **Object Read & Write**, applied to **specific buckets only** → `ada-pipeline-test-bucket`.
3. Copy the **Access Key ID**, **Secret Access Key**, and **S3 endpoint** (`https://ACCOUNT_ID.r2.cloudflarestorage.com`). The secret is shown only once.

## 6. Store the secrets

**Security → Secret Manager → Create secret**, one per row:

| Secret name | Value |
|---|---|
| `dice-secret-key` | A long random string (50+ characters, e.g. from a password manager's generator). |
| `dice-database-url` | `postgres://dice_app:PASSWORD@/%2Fcloudsql%2Fada-document-pipeline-staging%3Aus-central1%3Adice-staging/dice_document_pipeline` |
| `dice-s3-access-key` | R2 Access Key ID (step 5). |
| `dice-s3-secret-key` | R2 Secret Access Key (step 5). |
| `dice-sentry-dsn` | Your Sentry DSN. |
| `dice-anthropic-api-key` | Your Anthropic key (only needed if alt text is on). |
| `dice-superuser-password` | A password for your admin login (step 11). |

In `dice-database-url`, replace only `PASSWORD`. The rest looks odd on purpose — on Cloud Run the database is reached through a local connection (`/cloudsql/...`), not a web address — so keep it exactly as written, including the `/` right after `@`.

## 7. Settings every piece needs

You'll enter these in steps 8–11, on each form's **Variables & Secrets** tab.

**Secrets** (**Reference a secret**, version **latest**):

| Variable | Secret |
|---|---|
| `SECRET_KEY` | `dice-secret-key` |
| `DATABASE_URL` | `dice-database-url` |
| `S3_ACCESS_KEY` | `dice-s3-access-key` |
| `S3_SECRET_KEY` | `dice-s3-secret-key` |
| `SENTRY_DSN` | `dice-sentry-dsn` |
| `ANTHROPIC_API_KEY` | `dice-anthropic-api-key` |

**Plain variables:**

| Variable | Value |
|---|---|
| `S3_BUCKET_NAME` | `ada-pipeline-test-bucket` |
| `S3_ENDPOINT_URL` | The R2 S3 endpoint (step 5) |
| `S3_ADDRESSING_STYLE` | `path` (R2 needs this) |
| `SENTRY_ENVIRONMENT` | `staging` |
| `RUN_PRECHECK`, `RUN_OCR`, `RUN_FONT_REPAIR`, `RUN_FINALIZE_METADATA`, `RUN_LINK_TAG`, `RUN_ALT_TEXT`, `RUN_SCORING`, `RUN_POSTCHECK` | `True` for each step you want on |

Also on every form: service account **`dice-staging-runtime`**, and under **Cloud SQL connections** add `dice-staging`.

Don't set `DEBUG` — it's off by default, which is what staging needs.

Images for steps 8–11 (from step 3):
- App: `us-central1-docker.pkg.dev/ada-document-pipeline-staging/dice/app:vX.Y.Z` (the tag you built)
- OCR: `us-central1-docker.pkg.dev/ada-document-pipeline-staging/dice/opendataloader-hybrid:…` (pick the one in Artifact Registry)

## 8. Create the migrations job, and run it

1. **Cloud Run → Jobs → Deploy container**: app image, name `dice-migrate`, region `us-central1`.
2. Container command `python`; arguments `manage.py`, `migrate` (one per field).
3. Add the settings (step 7). Create, then **Execute**, and wait for it to succeed.

From now on the pipeline re-runs this on every deploy, before anything else changes.

## 9. Deploy the app

1. **Cloud Run → Deploy container → Service**: app image, name `dice-app`, region `us-central1`.
2. **Authentication: Allow public access.** (Google lets requests through; the API still needs its own tokens.)
3. Add the settings (step 7). Memory 1 GiB, 1 CPU. Deploy.
4. Copy the URL at the top of the service's page (e.g. `https://dice-app-….run.app`).
5. **Edit & deploy new revision** → add `ALLOWED_HOSTS` = the URL without `https://`, and `PUBLIC_BASE_URL` = the full URL. Deploy.

Before step 5, the URL shows "Bad Request (400)" — expected.

## 10. Create the worker job and its schedule

1. **Cloud Run → Jobs → Deploy container**: name `dice-worker`, region `us-central1`.
2. **First container**, named `worker`: app image, command `python`, arguments `manage.py`, `run_queued_tasks`. The settings (step 7), plus:
   - `PUBLIC_BASE_URL` = the app's full URL (the worker sends the webhooks, which link back to the app)
   - `OPENDATALOADER_HYBRID_URL` = `http://localhost:5002`

   1 CPU, 2 GiB memory.
3. **Add container**, named `ocr`: the OCR image. No settings. 3 CPU, 12 GiB memory. *(check — starting guess; watch its memory in the Metrics tab on the first real run.)* **Don't** set a startup dependency between the two containers — the worker waits for OCR itself, and only when there's work, so empty runs stay fast.
4. Job settings: **Task timeout 60 minutes**, **Max retries 0** (the next scheduled run is the retry). Create.
5. On the job's page → **Triggers** → **Add scheduler trigger**: frequency `*/15 * * * *` (every 15 minutes), time zone `America/Detroit`, service account `dice-staging-deployer` (it's allowed to start jobs). *(check)*
6. Test it: **Execute** the job by hand. With nothing queued, it should finish within seconds with "Nothing queued." in its logs. **Also check the execution finishes, rather than running on until the 60-minute timeout** — that confirms the OCR container stops when the worker does. *(check)*

The container names `worker` and `ocr` matter — the pipeline updates them by name.

## 11. Create your admin login and an API account

Two one-off jobs, created like step 8 (same app image and settings). **Execute** each once; delete them afterwards if you like.

1. **Admin login** — job `dice-create-admin`: command `python`, arguments `manage.py`, `createsuperuser`, `--noinput`. Add variables `DJANGO_SUPERUSER_USERNAME` (your choice) and `DJANGO_SUPERUSER_EMAIL`, and secret `DJANGO_SUPERUSER_PASSWORD` → `dice-superuser-password`.
2. **API account** — job `dice-create-account`: command `python`, arguments `manage.py`, `shell`, `-c`, then this as one argument (change the names as needed):

```
from accounts.models import Organization; from accounts.services import ServiceAccountService; ServiceAccountService().create(Organization.objects.get_or_create(name="Test Org")[0], "test-service-account")
```

3. Log in at `APP_URL/admin/` → **Service accounts** → tick `test-service-account` → **Issue a new token** → **Go**. The token appears at the top of the page, **only this once** — save it. (The job also made a token, but on purpose it's never shown anywhere, so it can't end up in logs.)
4. Open the account itself to see its **webhook secret**, if you'll use webhooks.

## 12. Test it end to end

1. `APP_URL/admin/` — the login page should be styled.
2. Upload a PDF with Postman to `APP_URL/api/submit-document/` with your token and a [webhook.site](https://webhook.site) `callback_url` (see [Testing the API](../README.md#testing-the-api)).
3. Within ~15 minutes (or **Execute** `dice-worker` to go now), its **Logs** show: work found, OCR ready, the pipeline running, the webhook sent, then the run ending.
4. Download the result from the `download_url` in the webhook.

## 13. Check the automatic deploy works

Merge any small PR to `main`. Within a few minutes:
1. GitHub's release workflow creates a new `v…` tag.
2. **Cloud Build → History** shows a `dice-staging-deploy` run: build → push → migrate → deploy-app → deploy-worker, all green.
3. `dice-app` shows a new revision, and `dice-worker`'s `worker` container shows the new image. Its schedule is unchanged.

## Every later deploy

**Just merge to `main`.** Watch **Cloud Build → History**.
- **If a run fails at "migrate":** nothing new was deployed — staging keeps running the previous version. Read that step's log, fix, merge again.
- **OCR image:** only when `Dockerfile.opendataloader-hybrid` or `requirements-opendataloader-hybrid.txt` changes — **Run** `dice-ocr-build` by hand after merging.
- **By hand, if the pipeline is broken:** build and push with the appendix commands, then `dice-migrate` → **Edit** → new image → **Execute**, then **Edit & deploy new revision** on `dice-app`, and **Edit** `dice-worker`'s `worker` container image.

## Pausing the worker

The worker costs almost nothing when idle, so there's usually no need. If you want uploads to wait (e.g. while fixing something):

- **Pause:** **Cloud Scheduler** → the `dice-worker` trigger → **Pause**.
- **Resume:** same place → **Resume**.
- **Run right now:** `dice-worker` → **Execute**.

While paused, the app stays up: uploads are accepted and come back `queued`, and finished documents can still be downloaded. Nothing is processed or sent until it resumes, then everything queued goes through in order.

If a run is stopped mid-document (e.g. it hits the 60-minute timeout), that document stays at `running` — nothing picks it back up automatically yet — so resubmit it with `force=true`.

**Once production is live:** staging won't need to check every 15 minutes. Plan to either make the schedule much less frequent (edit the trigger's frequency, e.g. hourly or a few times a day), or pause it and just **Execute** the worker by hand when needed — after a new deploy, or during manual testing.

## Turning staging off completely

For longer quiet stretches (e.g. once production is live). Most of staging already costs nothing when idle — the app scales to zero and empty worker runs cost fractions of a cent — so this really means stopping the database, the one part that's billed whether it's used or not. Saves roughly $10/month; you still pay ~$2/month for the database's disk, images and secrets.

**Off:**
1. **Cloud Scheduler** → the `dice-worker` trigger → **Pause**. (Otherwise every scheduled run fails trying to reach the stopped database.)
2. **SQL** → `dice-staging` → **Stop**.

**On:**
1. **SQL** → `dice-staging` → **Start**. Wait until it shows as running (a few minutes).
2. **Cloud Scheduler** → the `dice-worker` trigger → **Resume** (or **Execute** `dice-worker` to process anything waiting right away).

While it's off, staging is fully down: the app returns errors, and nothing is lost — all data stays in the stopped database and the R2 bucket. A merge to `main` while it's off stops safely at the migration step (nothing new is deployed); once it's back on, **Run** the `dice-staging-deploy` trigger again for the latest tag.

## Later, not blocking

- Clear out old task records daily: a job running `manage.py prune_db_task_results`, triggered by **Cloud Scheduler**.
- Production: the same pipeline with its own project, database and bucket, plus a manual approval step before migrations ([ADR 0022](adrs/0022-automated-staging-deploys.md)).

## Appendix: building and deploying by hand

If Cloud Build isn't available. Needs Docker and the [`gcloud` CLI](https://cloud.google.com/sdk/docs/install) (or use **Cloud Shell**, the `>_` button at the top of the Console, which has `gcloud` built in).

```bash
export PROJECT=ada-document-pipeline-staging REGION=us-central1 VERSION=$(git describe --tags --abbrev=0)
export APP_IMAGE=$REGION-docker.pkg.dev/$PROJECT/dice/app:$VERSION
export OCR_IMAGE=$REGION-docker.pkg.dev/$PROJECT/dice/opendataloader-hybrid:$VERSION

gcloud auth configure-docker $REGION-docker.pkg.dev
docker build --build-arg PIPELINE_VERSION=$VERSION -t $APP_IMAGE .
docker push $APP_IMAGE
docker build -f Dockerfile.opendataloader-hybrid -t $OCR_IMAGE .   # only if OCR changed
docker push $OCR_IMAGE                                              # ~12 GB, slow

# The same steps cloudbuild.yaml runs:
gcloud run jobs update dice-migrate --image $APP_IMAGE --region $REGION
gcloud run jobs execute dice-migrate --region $REGION --wait
gcloud run services update dice-app --image $APP_IMAGE --region $REGION
gcloud run jobs update dice-worker --region $REGION --container worker --image $APP_IMAGE
gcloud run jobs update dice-worker --region $REGION --container ocr --image $OCR_IMAGE   # only if OCR changed

# Run the worker now
gcloud run jobs execute dice-worker --region $REGION
```
