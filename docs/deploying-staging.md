# Deploying to staging (Google Cloud Run)

Staging runs on Google Cloud Run, and PDFs are stored in [Cloudflare R2](https://developers.cloudflare.com/r2/). The Google Cloud setup is described in OpenTofu files ([`deploy/terraform/staging`](../deploy/terraform/staging/README.md)): A few pieces are made by hand, and this guide covers those. Once it's set up, **every merge to `main` deploys to staging automatically** ([ADR 0022](adrs/0022-automated-staging-deploys.md)).

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

**Timing:** the schedule is set up but kept **paused** on staging, so uploads wait until someone **Executes** `dice-worker` ([ADR 0029](adrs/0029-staging-worker-paused-by-default.md)). Once it runs, add a minute or two for OCR to load. Callers get a webhook when it's done. Webhook retries also go out on the next run. If you resume the schedule ([Pausing the worker](#pausing-the-worker)), a run starts every 20 minutes.

**Cost:** roughly **$10/month** for staging at a few documents a day. The database is on [Neon](https://neon.com)'s free plan ($0) and the worker only costs anything while it's actually processing ([ADR 0028](adrs/0028-staging-database-on-neon.md)).

**Why paused, and why 20 minutes if resumed:** each worker run's "anything queued?" check wakes the database for about 5 minutes. The free plan allows 100 compute-hours a month; every 20 minutes uses about half. Running by hand uses far less. If you resume and usage heads for the limit, change the schedule to every 30 minutes.

**Backups:** Neon's free plan can only restore to a point in the last **6 hours** — there are no nightly backup files. Don't keep anything on staging you'd mind losing.

**How deploys work:** merge a PR → GitHub tags a new release (`vX.Y.Z`) → Cloud Build builds the app image, runs database migrations, then updates the app and worker. If migrations fail, it stops and nothing new is deployed.

## What is managed where

| Thing | Name | Managed by |
|---|---|---|
| Google Cloud project | `ada-document-pipeline-staging` | Exists already; OpenTofu works inside it |
| Turned-on services (APIs), service accounts, image repository | `dice-staging-runtime`, `dice-staging-deployer`, `dice` | OpenTofu |
| Secrets | `dice-*` | OpenTofu makes them empty; **values by hand** |
| Migrations job, app, worker job, worker schedule | `dice-migrate`, `dice-app`, `dice-worker`, `dice-worker-scheduler-trigger` (paused) | OpenTofu |
| Build triggers | `dice-staging-deploy`, `dice-ocr-build` | By hand (they need the GitHub connection) |
| Neon project / database / user | `dice-staging` / `dice_document_pipeline` / `dice_app` | By hand |
| R2 bucket and API token | `ada-pipeline-staging-bucket` | By hand |
| Admin login, first account | — | By hand |

Staging has its own bucket, separate from prod's, just as it has its own database. Make sure `ada-document-pipeline-staging` is selected in the project dropdown at the top left of the Console before each by-hand step.

## Setting up (what is done by hand)

Staging was built by hand first and then adopted into OpenTofu, so this order hasn't been run on an empty project. Everything OpenTofu manages (the services, accounts, repository, secrets, app, jobs and schedule, with all their settings) is **not** listed here: see the [OpenTofu README](../deploy/terraform/staging/README.md).

### 1. Create the database

Staging's database is on [Neon](https://neon.com) (free plan), not Google Cloud. Neon runs on AWS, but you don't need an AWS account.

1. In the Neon console, **Create project**: name `dice-staging`, Postgres 18, region **AWS US East (Ohio) `aws-us-east-2`** (closest to `us-central1`). Database `dice_document_pipeline`, user `dice_app`.
2. **Settings → Compute**: set minimum and maximum to **0.25 CU**, so it can't grow and use up the free hours faster. Leave scale-to-zero on (the default, after 5 minutes idle).
3. **Connect**: turn **Connection pooling off** and copy the **direct** connection string. It ends in `?sslmode=require`. Keep that part.

### 2. Get keys for the R2 bucket (Cloudflare dashboard)

1. **R2 Object Storage → `ada-pipeline-staging-bucket` → Settings**: confirm public access is **off**. Documents are downloaded through the app, never straight from the bucket.
2. **R2 Object Storage → Manage API tokens → Create API token**: permissions **Object Read & Write**, applied to **specific buckets only** → `ada-pipeline-staging-bucket`.
3. Copy the **Access Key ID**, **Secret Access Key**, and **S3 endpoint** (`https://ACCOUNT_ID.r2.cloudflarestorage.com`). The secret is shown only once.

### 3. Connect GitHub and create the build triggers

1. **Cloud Build → Repositories → Connect repository** → GitHub → pick `vanburencountymi-digital-information/dice-document-pipeline-api`. An organization admin may need to approve Google's "Cloud Build" GitHub App.
2. **Cloud Build → Triggers → Create trigger** — the automatic deploy:
   - Name `dice-staging-deploy`, region `us-central1`.
   - Event: **Push new tag**. Tag: `^v.*$`.
   - Configuration: **Cloud Build configuration file**, `cloudbuild.yaml`.
   - Service account: `dice-staging-deployer`.
3. **Create trigger** again — the OCR image, run by hand only:
   - Name `dice-ocr-build`, region `us-central1`.
   - Event: **Manual invocation**, branch `main`.
   - Configuration file: `cloudbuild-ocr.yaml`. Service account: `dice-staging-deployer`.

### 4. Run OpenTofu, build the first images, add the secret values

1. In `deploy/terraform/staging`, follow its [README](../deploy/terraform/staging/README.md) and run `tofu apply`. The app and jobs can't start until their images and secret values exist, so on an empty project this first apply is expected to stop there.
2. **Build the first images:** on the Triggers page, **Run** `dice-ocr-build`, then **Run** `dice-staging-deploy` (pick the latest `v…` tag). OCR takes a while (~12 GB). Until the app and jobs exist, both runs fail at their last steps; what matters is that the build and push steps succeed. Check **Artifact Registry → dice** shows an `app` and an `opendataloader-hybrid` image.
3. **Add the secret values.** OpenTofu made the secrets empty. In **Security → Secret Manager**, open each one → **New version**:

   | Secret name | Value |
   |---|---|
   | `dice-secret-key` | A long random string (50+ characters, e.g. from a password manager's generator). |
   | `dice-database-url` | The Neon direct connection string (step 1), ending in `?sslmode=require`. |
   | `dice-s3-access-key` | R2 Access Key ID (step 2). |
   | `dice-s3-secret-key` | R2 Secret Access Key (step 2). |
   | `dice-sentry-dsn` | Your Sentry DSN. |
   | `dice-anthropic-api-key` | Your Anthropic key (only needed if alt text is on). |

   Paste Neon's string exactly as given. Neon passwords are already safe to use in a web address.
4. Run `tofu apply` again. It creates the app, jobs and schedule.

### 5. Create your admin login and an API account

1. **Admin login** — the only step that needs a terminal. In **Cloud Shell**, follow [Django shell on staging](#appendix-django-shell-on-staging) up to the shell prompt, then run `python manage.py createsuperuser` and answer the questions. Type `exit` when done.
2. Log in at `APP_URL/admin/` → **Organizations** → **Add**, enter a name, and save.
3. Go to **Service accounts** → **Add**, pick the organization, enter a name for the account (e.g. `test-service-account`) and a name for its first token (e.g. `first-token`), and save. The token appears at the top of the page, **only this once** — save it.
4. Open the account to see its **webhook secret**, if you'll use webhooks.

`APP_URL` is `tofu output app_url` in `deploy/terraform/staging`. More in the [Admin guide](admin-guide.md).

## Testing it

### The worker

1. `dice-worker` → **Execute**. Or: `gcloud run jobs execute dice-worker --region us-central1 --wait`.
2. **The start can be slow.** The OCR image is ~12 GB and each run starts on a fresh machine, so it took 4–6 minutes to start when we first tried it. "Running" with blank logs meanwhile is normal.
3. Open the run → **Tasks** → task 0 → **View logs**. Logs arrive 30–60 seconds late.
4. **It passed if:** status is Succeeded, exit code 0, the logs say "Nothing queued.", and the task ends within about a minute of that line. If it runs on until the 3-hour timeout, the OCR container isn't stopping with the worker.
5. **If it failed,** the last line of the error in the logs (or Sentry) is the reason. Common ones:

   | Error says | Likely cause |
   |---|---|
   | `password authentication failed` | `dice-database-url` has the wrong password. |
   | `connection is insecure` / SSL required | The URL is missing `?sslmode=require`. |
   | `could not translate host name` / `invalid port` | The URL is mistyped (re-copy Neon's direct string). |
   | `permission denied` on a secret | The runtime account lost its access to the secret. Run `tofu plan` in `deploy/terraform/staging` to see what drifted. |

### The schedule

The schedule is paused, but you can still fire it once to check it starts the job: `gcloud scheduler jobs run dice-worker-scheduler-trigger --location us-central1`, then `gcloud run jobs executions list --job dice-worker --region us-central1 --limit 2`. A new run should appear, started by `dice-staging-deployer`. If nothing appears, `gcloud scheduler jobs describe dice-worker-scheduler-trigger --location us-central1` shows why (`status code: 7` is a permission problem: run `tofu plan` to see what drifted).

### End to end

1. `APP_URL/admin/` — the login page should be styled, and logging in should work (the login page alone doesn't touch the database).
2. Upload a PDF with Postman to `APP_URL/api/submit-document/` with your token and a [webhook.site](https://webhook.site) `callback_url` (see [Testing the API](../README.md#testing-the-api)).
3. **Execute** `dice-worker` (or wait for the next run, if the schedule is resumed). Its **Logs** show: work found, OCR ready, the pipeline running, the webhook sent, then the run ending.
4. Download the result from the `download_url` in the webhook.

### The automatic deploy

Merge any small PR to `main`. Within a few minutes:
1. GitHub's release workflow creates a new `v…` tag.
2. **Cloud Build → History** shows a `dice-staging-deploy` run: build → push → migrate → deploy-app → deploy-worker, all green.
3. `dice-app` shows a new revision, and `dice-worker`'s `worker` container shows the new image. Its schedule is unchanged.

## Every later deploy

**Just merge to `main`.** Watch **Cloud Build → History**.
- **If a run fails at "migrate":** nothing new was deployed — staging keeps running the previous version. Read that step's log, fix, merge again.
- **OCR image:** only when `Dockerfile.opendataloader-hybrid` or `requirements-opendataloader-hybrid.txt` changes — **Run** `dice-ocr-build` by hand after merging.
- **If the pipeline is broken:** build, push and update the images with the [appendix commands](#appendix-building-and-deploying-by-hand). OpenTofu ignores images, so it won't undo this.

## Pausing the worker

On staging the schedule is kept paused, so the worker only runs when you start it. That setting lives in OpenTofu (`worker_schedule_paused` in `deploy/terraform/staging/main.tf`), so pausing or resuming it by hand in the Console would be undone by the next `tofu apply`.

- **Run right now:** `dice-worker` → **Execute**. Run it by hand after a new deploy or during manual testing.
- **Resume the schedule:** set `worker_schedule_paused = false`, then `tofu apply`.
- **Pause it again:** set it back to `true`, then `tofu apply`.

While paused, the app stays up: uploads are accepted and come back `queued`, and finished documents can still be downloaded. Nothing is processed or sent until the worker runs, then everything queued goes through in order.

If a run is stopped mid-document (e.g. it hits the 3-hour timeout), that document stays at `running` — nothing picks it back up automatically yet — so resubmit it with `force=true`.

## Turning staging off completely

For longer quiet stretches there is nothing to turn off. The schedule is paused by default, and the Neon database goes to sleep by itself whenever nothing uses it, so it uses no compute hours unless someone uses the app or API.

To run the worker once (e.g. after a deploy): **Cloud Run → Jobs → `dice-worker` → Execute**.

Nothing goes down and there's nothing to restart. The app stays up, uploads are accepted and stay `queued`, and deploys still work (migrations wake the database). If you resumed the schedule, pause it again as described above.

## How OpenTofu manages staging

The files are in [`deploy/terraform/staging`](../deploy/terraform/staging/README.md), with their own state, and use the same shared code as prod. Run `tofu plan` and read it before every `tofu apply`.

- **Images:** Cloud Build changes the image on `dice-app` and on the worker job's `worker` container (and `ocr` when rebuilt) on every deploy. OpenTofu ignores images, so it won't undo a deploy. The container names `worker` and `ocr` matter: the pipeline updates them by name. After a deploy, `tofu plan` shows one harmless line on `dice-app`: `revision = "dice-app-0000N-xxx" -> null`. Each deploy gives the new revision a generated name, and OpenTofu would clear it. Nothing is actually different, so there is nothing to fix.
- **Secrets:** their values stay out of OpenTofu and its state. It makes the empty secrets only.
- **Worker schedule:** kept paused. OpenTofu keeps it that way, so it won't resume it.
- **Public access:** staging is made public with Cloud Run's "invoker" setting, because our organization policy blocks the `allUsers` grant that prod uses.
- **Cost:** the app's CPU is allocated only while it handles requests (cheaper when idle), with extra CPU while a new container starts. Images older than 30 days are deleted, keeping the newest 2 of each.
- **Not in OpenTofu:** the Neon database, the R2 buckets, secret values, the Cloud Build triggers and the admin login.

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
