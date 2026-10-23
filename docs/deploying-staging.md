# Deploying to staging (Google Cloud Run)

Setting up staging, done almost entirely in the [Google Cloud Console](https://console.cloud.google.com) (the website). PDFs are stored in [Cloudflare R2](https://developers.cloudflare.com/r2/). Once it's set up, **every merge to `main` deploys to staging automatically** ([ADR 0022](adrs/0022-automated-staging-deploys.md)).

> Not yet run end to end. Console labels change from time to time; if a button isn't named exactly as written, look for the closest match. Steps marked *(check)* use options we couldn't test ahead of time.

Related: the image ([ADR 0021](adrs/0021-deployable-image.md)), the worker ([ADR 0020](adrs/0020-database-backed-task-worker.md)), storage ([ADR 0018](adrs/0018-s3-compatible-storage-backend.md)).

## How it fits together

```
Internet ──► dice-app (Cloud Run service, public)   ─┐
                                                     ├─► Neon (Postgres)
dice-worker (Cloud Run job, run by hand or scheduler)─┤
  ├── worker container: manage.py run_queued_tasks   └─► Cloudflare R2 bucket (PDFs)
  └── ocr container: opendataloader-hybrid :5002 (localhost only)
        ▲ started by Cloud Scheduler
```

- **dice-app** — the website and API people call.
- **dice-worker** — processes uploads in the background. When it runs, it checks for queued uploads and exits straight away if there are none (seconds, almost free). If there are, it waits for OCR to load, processes them all, then exits ([ADR 0023](adrs/0023-scheduled-batch-worker.md)). OCR runs next to it as a second ("sidecar") container that only the worker can reach — never the internet.
- The app and worker run the **same image**; only their command differs.

**This isn't Google Cloud Tasks.** The upload queue lives in our own Postgres database ([ADR 0020](adrs/0020-database-backed-task-worker.md)); we don't use Google's task service, and its API never needs turning on. The worker is an ordinary Cloud Run *job* (a program that runs, finishes and exits) that Cloud Scheduler starts on a timer.

**Timing:** the schedule is set up but kept **paused** on staging, so uploads wait until someone **Executes** `dice-worker` ([ADR 0029](adrs/0029-staging-worker-paused-by-default.md)). Once it runs, add a minute or two for OCR to load. Callers get a webhook when it's done. Webhook retries also go out on the next run. If you resume the schedule, a run starts every 20 minutes.

**Cost:** roughly **$10/month** for staging at a few documents a day. The database is on [Neon](https://neon.com)'s free plan ($0) and the worker only costs anything while it's actually processing ([ADR 0028](adrs/0028-staging-database-on-neon.md)).

**Why paused, and why 20 minutes if resumed:** each worker run's "anything queued?" check wakes the database for about 5 minutes. The free plan allows 100 compute-hours a month; every 20 minutes uses about half. Running by hand uses far less. If you resume and usage heads for the limit, change the schedule to every 30 minutes.

**Backups:** Neon's free plan can only restore to a point in the last **6 hours** — there are no nightly backup files. Don't keep anything on staging you'd mind losing.

**How deploys work:** merge a PR → GitHub tags a new release (`vX.Y.Z`) → Cloud Build builds the app image, runs database migrations, then updates the app and worker. If migrations fail, it stops and nothing new is deployed.

## What already exists, and the names used here

| Thing | Name | Status | Terraform |
|---|---|---|---|
| Google Cloud project | `ada-document-pipeline-staging` | Exists | Import |
| Region | `us-central1` | Use for everything | — |
| Turned-on services (APIs) | see step 1 | Create (step 1) | `google_project_service` |
| Runtime service account | `dice-staging-runtime` | Create (step 2) | `google_service_account` + IAM |
| Build service account | `dice-staging-deployer` | Create (step 2) | `google_service_account` + IAM |
| Image repository | `dice` | Create (step 3) | `google_artifact_registry_repository` |
| Build triggers | `dice-staging-deploy`, `dice-ocr-build` | Create (step 3) | `google_cloudbuild_trigger` (GitHub connection stays manual) |
| Neon project / database / user | `dice-staging` / `dice_document_pipeline` / `dice_app` | Create (step 4) | Neon provider, or manual |
| R2 bucket and API token | `ada-pipeline-test-bucket` | Exists (token: step 5) | Cloudflare provider, or manual |
| Secrets | `dice-*` (step 6) | Create (step 6) | `google_secret_manager_secret` (values stay manual) |
| Migrations job / app / worker job | `dice-migrate` / `dice-app` / `dice-worker` | Create (steps 8–10) | `google_cloud_run_v2_job` / `_service` |
| Worker schedule | `dice-worker` trigger (paused) | Create (step 10) | `google_cloud_scheduler_job` |
| Admin login, first account | — | Create (step 11) | Manual |

The Terraform column is a starting point: [DIC-2329](https://linear.app/dicelabs/issue/DIC-2329) decides whether we use it.

Make sure `ada-document-pipeline-staging` is selected in the project dropdown at the top left of the Console before each step.

The bucket's name says "test" — fine for staging. Production should get its own bucket, just as it gets its own database.

## 1. Turn on the services

**APIs & Services → Enable APIs and services**, then enable each of: **Cloud Run Admin API**, **Artifact Registry API**, **Secret Manager API**, **Cloud Build API**, **Cloud Scheduler API**.

## 2. Create the service accounts

1. **IAM & Admin → Service Accounts → Create service account**: `dice-staging-runtime` (what the app and worker run as). Grant it: **Secret Manager Secret Accessor**.
2. Create `dice-staging-deployer` (what builds run as, and what starts the worker on its schedule). Grant it: **Artifact Registry Writer**, **Cloud Run Developer**, **Logs Writer**. *(check)*
3. Open `dice-staging-runtime` → **Permissions** → grant `dice-staging-deployer` the role **Service Account User** — this lets the build deploy things that run as the runtime account. Grant it on this account only, not project-wide.
4. Check the deployer has every role it needs. Without **Cloud Run Developer** (`roles/run.developer`), the worker's schedule fails with `PERMISSION_DENIED` (`status code: 7`). In Cloud Shell:

   ```bash
   gcloud projects get-iam-policy ada-document-pipeline-staging \
     --flatten="bindings[].members" \
     --filter="bindings.members:dice-staging-deployer" \
     --format="value(bindings.role)"
   ```

   The list must include `roles/run.developer`. When you add a role, `gcloud` may ask about a condition: choose **None**. An expired condition would stop the role from ever applying.

## 3. Image repository and automatic builds

1. **Artifact Registry → Create repository**: name `dice`, format **Docker**, region `us-central1`.
2. **Cloud Build → Repositories → Connect repository** → GitHub → pick `vanburencountymi-digital-information/dice-document-pipeline-api`. An organization admin may need to approve Google's "Cloud Build" GitHub App.
3. **Cloud Build → Triggers → Create trigger** — the automatic deploy:
   - Name `dice-staging-deploy`, region `us-central1`.
   - Event: **Push new tag**. Tag: `^v.*$`.
   - Configuration: **Cloud Build configuration file**, `cloudbuild.yaml`.
   - Service account: `dice-staging-deployer`.
4. **Create trigger** again — the OCR image, run by hand only:
   - Name `dice-ocr-build`, region `us-central1`.
   - Event: **Manual invocation**, branch `main`.
   - Configuration file: `cloudbuild-ocr.yaml`. Service account: `dice-staging-deployer`.
5. **Build the first images:** on the Triggers page, **Run** `dice-ocr-build`, then **Run** `dice-staging-deploy` (pick the latest `v…` tag). OCR takes a while (~12 GB).
   **Both runs will fail at their last steps this first time** — they try to update the app, worker job and migrations job, which you create in steps 8–10. That's expected: what matters now is that the build and push steps succeed. Check **Artifact Registry → dice** shows an `app` and an `opendataloader-hybrid` image.

## 4. Create the database

Staging's database is on [Neon](https://neon.com) (free plan), not Google Cloud. Neon runs on AWS, but you don't need an AWS account.

1. In the Neon console, **Create project**: name `dice-staging`, Postgres 18, region **AWS US East (Ohio) `aws-us-east-2`** (closest to `us-central1`). Database `dice_document_pipeline`, user `dice_app`.
2. **Settings → Compute**: set minimum and maximum to **0.25 CU**, so it can't grow and use up the free hours faster. Leave scale-to-zero on (the default, after 5 minutes idle).
3. **Connect**: turn **Connection pooling off** and copy the **direct** connection string. It ends in `?sslmode=require`. Keep that part.

## 5. Get keys for the R2 bucket (Cloudflare dashboard)

1. **R2 Object Storage → `ada-pipeline-test-bucket` → Settings**: confirm public access is **off**. Documents are downloaded through the app, never straight from the bucket.
2. **R2 Object Storage → Manage API tokens → Create API token**: permissions **Object Read & Write**, applied to **specific buckets only** → `ada-pipeline-test-bucket`.
3. Copy the **Access Key ID**, **Secret Access Key**, and **S3 endpoint** (`https://ACCOUNT_ID.r2.cloudflarestorage.com`). The secret is shown only once.

## 6. Store the secrets

**Security → Secret Manager → Create secret**, one per row:

| Secret name | Value |
|---|---|
| `dice-secret-key` | A long random string (50+ characters, e.g. from a password manager's generator). |
| `dice-database-url` | The Neon direct connection string (step 4), ending in `?sslmode=require`. |
| `dice-s3-access-key` | R2 Access Key ID (step 5). |
| `dice-s3-secret-key` | R2 Secret Access Key (step 5). |
| `dice-sentry-dsn` | Your Sentry DSN. |
| `dice-anthropic-api-key` | Your Anthropic key (only needed if alt text is on). |

Paste Neon's string exactly as given. Neon passwords are already safe to use in a web address.

## 7. Settings every piece needs

You'll enter these in steps 8–10, on each form's **Variables & Secrets** tab. What each setting means: [Settings reference](deploying-prod.md#settings-reference).

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
| `TRUSTED_PROXY_HOPS` | `1` (Cloud Run adds one `X-Forwarded-For` entry; leaving it at 0 makes every caller share one throttle counter) |
| `RUN_PRECHECK`, `RUN_OCR`, `RUN_FONT_REPAIR`, `RUN_FINALIZE_METADATA`, `RUN_LINK_TAG`, `RUN_ALT_TEXT`, `RUN_SCORING`, `RUN_POSTCHECK` | `True` for each step you want on |

Also on every form: service account **`dice-staging-runtime`**.

Don't set `DEBUG` — it's off by default, which is what staging needs.

Images for steps 8–10 (from step 3):
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
5. On the job's page → **Triggers** → **Add scheduler trigger**: frequency `*/20 * * * *` (every 20 minutes), time zone `America/Detroit`, service account `dice-staging-deployer` (it's allowed to start jobs). *(check)* On staging, **Pause** the trigger straight after creating it and run the worker by hand (see [Pausing the worker](#pausing-the-worker)).
   - The time zone can default to UTC. Check it on the trigger's page; to fix it from Cloud Shell: `gcloud scheduler jobs update http dice-worker-scheduler-trigger --location us-central1 --time-zone America/Detroit` (use your trigger's real name).
6. Test the job by hand:
   1. `dice-worker` → **Execute**. Or: `gcloud run jobs execute dice-worker --region us-central1 --wait`.
   2. **The start can be slow.** The OCR image is ~12 GB and each run starts on a fresh machine, so it took 4–6 minutes to start when we first tried it. "Running" with blank logs meanwhile is normal.
   3. Open the run → **Tasks** → task 0 → **View logs**. Logs arrive 30–60 seconds late.
   4. **It passed if:** status is Succeeded, exit code 0, the logs say "Nothing queued.", and the task ends within about a minute of that line. If it runs on until the 60-minute timeout, the OCR container isn't stopping with the worker. *(check)*
   5. **If it failed,** the last line of the error in the logs (or Sentry) is the reason. Common ones:

      | Error says | Likely cause |
      |---|---|
      | `password authentication failed` | `dice-database-url` has the wrong password. |
      | `connection is insecure` / SSL required | The URL is missing `?sslmode=require`. |
      | `could not translate host name` / `invalid port` | The URL is mistyped (re-copy Neon's direct string). |
      | `permission denied` on a secret | `dice-staging-runtime` is missing **Secret Manager Secret Accessor**. |
7. Test the schedule (only once, then **Pause** it again): fire it with `gcloud scheduler jobs run dice-worker-scheduler-trigger --location us-central1`, then run `gcloud run jobs executions list --job dice-worker --region us-central1 --limit 2`. A new run should appear, started by `dice-staging-deployer`. If nothing appears, `gcloud scheduler jobs describe dice-worker-scheduler-trigger --location us-central1` shows why (`status code: 7` is the permission problem from step 2).

The container names `worker` and `ocr` matter — the pipeline updates them by name.

## 11. Create your admin login and an API account

1. **Admin login** — the only step that needs a terminal. In **Cloud Shell**, follow [Django shell on staging](#appendix-django-shell-on-staging) up to the shell prompt, then run `python manage.py createsuperuser` and answer the questions. Type `exit` when done.
2. Log in at `APP_URL/admin/` → **Organizations** → **Add**, enter a name, and save.
3. Go to **Service accounts** → **Add**, pick the organization, enter a name for the account (e.g. `test-service-account`) and a name for its first token (e.g. `first-token`), and save. The token appears at the top of the page, **only this once** — save it.
4. Open the account to see its **webhook secret**, if you'll use webhooks.

More in the [Admin guide](admin-guide.md).

## 12. Test it end to end

1. `APP_URL/admin/` — the login page should be styled, and logging in should work (the login page alone doesn't touch the database).
2. Upload a PDF with Postman to `APP_URL/api/submit-document/` with your token and a [webhook.site](https://webhook.site) `callback_url` (see [Testing the API](../README.md#testing-the-api)).
3. **Execute** `dice-worker` (or wait for the next run, if the schedule is resumed). Its **Logs** show: work found, OCR ready, the pipeline running, the webhook sent, then the run ending.
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

On staging the trigger is kept paused, so the worker only runs when you start it. To control it:

- **Pause:** **Cloud Scheduler** → the `dice-worker` trigger → **Pause**.
- **Resume:** same place → **Resume**.
- **Run right now:** `dice-worker` → **Execute**.

While paused, the app stays up: uploads are accepted and come back `queued`, and finished documents can still be downloaded. Nothing is processed or sent until it resumes, then everything queued goes through in order.

If a run is stopped mid-document (e.g. it hits the 60-minute timeout), that document stays at `running` — nothing picks it back up automatically yet — so resubmit it with `force=true`.

Run it by hand after a new deploy or during manual testing. To go back to automatic runs, **Resume** the trigger.

## Turning staging off completely

For longer quiet stretches. The Neon database goes to sleep by itself whenever nothing uses it, so the only thing to stop is the schedule that wakes it:

- **Off:** **Cloud Scheduler** → the `dice-worker` trigger → **Pause**. Neon then uses no compute hours unless someone uses the app or API.
- **On:** same place → **Resume**.
- **Run once without resuming:** **Cloud Run → Jobs → `dice-worker` → Execute** (e.g. after a deploy).

Nothing goes down and there's nothing to restart. The app stays up, uploads are accepted and stay `queued`, and deploys still work (migrations wake the database).

## Notes for Terraform

- Cloud Build changes the image on `dice-app` and on the worker job's `worker` container (and `ocr` when rebuilt) on every deploy. Terraform must ignore image changes on those, or it will roll back deploys.
- Secret **values** (step 6) stay out of Terraform and its state. Terraform creates the empty secrets only.
- The worker's schedule is kept paused on staging. Terraform needs to match, or it will resume it.
- Settings in step 7, and the sizes in steps 9–10 (memory, CPU, 60-minute timeout), are the values to copy. Worker timeout and CPU are due to change ([DIC-2480](https://linear.app/dicelabs/issue/DIC-2480)).

## Later, not blocking

- Clear out old task records daily: a job running `manage.py prune_db_task_results`, triggered by **Cloud Scheduler**.
- Production: the same pipeline with its own project, database and bucket, plus a manual approval step before migrations ([ADR 0022](adrs/0022-automated-staging-deploys.md)).

## Appendix: Django shell on staging

Cloud Run can't open a shell inside a running container, so run the app image from **Cloud Shell** (the `>_` button in the Console) instead. Use it for the admin login, debugging or fixing data. You need these roles on your own Google account: Secret Manager Secret Accessor, Artifact Registry Reader (Owner/Editor also works).

1. Start the app image with a shell (run `gcloud auth login` first if asked). It connects straight to Neon, so no extra connection is needed. The first pull takes a few minutes. Use `sh`, not `bash` (the image has no bash).

   ```bash
   gcloud auth configure-docker us-central1-docker.pkg.dev --quiet

   docker run -it --rm \
     -e DATABASE_URL="$(gcloud secrets versions access latest --secret=dice-database-url)" \
     -e SECRET_KEY="$(gcloud secrets versions access latest --secret=dice-secret-key)" \
     us-central1-docker.pkg.dev/ada-document-pipeline-staging/dice/app:<tag> \
     sh
   ```

2. Run what you need, e.g. `python manage.py createsuperuser` or `python manage.py shell`.
3. Type `exit`.

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
