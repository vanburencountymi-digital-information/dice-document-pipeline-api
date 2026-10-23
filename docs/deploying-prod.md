# Deploying to prod

Setting up prod on Google Cloud, and putting new versions live on it. Staging works differently; see [deploying-staging.md](deploying-staging.md). The OpenTofu files are in [`deploy/terraform/prod/`](../deploy/terraform/prod/README.md). Run `tofu` commands from that folder. Settings that apply to any deployment are explained in the [Settings reference](#settings-reference) at the end.

Prod does not update on its own: you promote a version that is already running on staging.

## Part 1: One-time setup

1. Install OpenTofu 1.10 or newer ([all install options](https://opentofu.org/docs/intro/install/)). On Ubuntu or Debian (including WSL):

   ```bash
   curl --proto '=https' --tlsv1.2 -fsSL https://get.opentofu.org/install-opentofu.sh -o install-opentofu.sh
   chmod +x install-opentofu.sh
   ./install-opentofu.sh --install-method deb
   rm install-opentofu.sh
   tofu version
   ```

   On a Mac: `brew install opentofu`. `tofu version` must show 1.10 or higher.
2. In Cloudflare, make a private R2 bucket for OpenTofu's own notes (e.g. `dice-terraform-state`) and an API token for just that bucket.
3. In `deploy/terraform/prod/`: copy `backend.hcl.example` to `backend.hcl` and `terraform.tfvars.example` to `terraform.tfvars`, and fill them in. Neither is committed.
4. Log in to Google: `gcloud auth application-default login`.
5. Set the R2 token: `export AWS_ACCESS_KEY_ID=...` and `export AWS_SECRET_ACCESS_KEY=...`.
6. `tofu init -backend-config=backend.hcl`

R2 keeps no old versions of the state file. Copy `terraform.tfstate` somewhere safe after big changes.

### First apply (two passes)

Cloud Run won't start until its images and secrets exist, so:

**Pass 1** (`deploy_cloud_run = false`): `tofu plan`, read it, then `tofu apply`. This makes everything except Cloud Run.

Then, by hand (you can also set them in cloud secrets manager)
- Add each secret's value: `printf '%s' 'VALUE' | gcloud secrets versions add dice-secret-key --data-file=-` (names are in `tofu output secret_names`).
- Put the app and OCR images in the new repository (`tofu output image_repository`): copy them from staging so prod runs exactly what was tested. See below.

#### Copying the images

Use **Cloud Shell** (the `>_` button in the Google Cloud Console): the OCR image is about 12 GB, and copying inside Google's network is much faster than through your computer.

1. Find what staging runs now (the app image tag is a version like `v5.4.0`; the OCR tag is a short code):

   ```bash
   gcloud run services describe dice-app --region us-central1 \
     --project ada-document-pipeline-staging \
     --format='value(spec.template.spec.containers[0].image)'
   gcloud run jobs describe dice-worker --region us-central1 \
     --project ada-document-pipeline-staging --format=export | grep image:
   ```

2. Install `crane`, a small tool that copies images between repositories. It stays in your Cloud Shell home folder, so this is a one-time step:

   ```bash
   mkdir -p ~/bin
   curl -sL https://github.com/google/go-containerregistry/releases/latest/download/go-containerregistry_Linux_x86_64.tar.gz | tar -xz -C ~/bin crane
   export PATH=$PATH:~/bin
   gcloud auth configure-docker us-central1-docker.pkg.dev
   ```

3. Copy both images, using the tags from step 1:

   ```bash
   S=us-central1-docker.pkg.dev/ada-document-pipeline-staging/dice
   D=us-central1-docker.pkg.dev/ada-document-pipeline-prod/dice
   crane copy $S/app:vX.Y.Z $D/app:vX.Y.Z
   crane copy $S/opendataloader-hybrid:TAG $D/opendataloader-hybrid:TAG
   ```

4. In the Console, open **Artifact Registry → dice** in the prod project and check both tags are there.

#### Allow public access

Our organization blocks making a service public by default, so `tofu apply` fails on `dice-app` with "users named in the policy do not belong to a permitted customer". The API still needs its own tokens; this only lets requests reach the app. Switch the block off for the prod project only. You need the **Organization Policy Administrator** role on the organization (ask an org admin if you don't have it):

```bash
gcloud org-policies set-policy policy.yaml
```

Run it from `deploy/terraform/prod/` (`policy.yaml` is there). Or in the Console: **IAM & Admin → Organization Policies → Domain restricted sharing → Manage policy → Override parent's policy**, add a rule with enforcement **Off**, then **Set policy**. Wait a few minutes before applying.

**Pass 2:** set `deploy_cloud_run = true` and both image names in `terraform.tfvars`, using the **prod** repository path (`…/ada-document-pipeline-prod/dice/app:vX.Y.Z`, not staging's). Then `tofu plan` and `tofu apply`.

Last: **Execute** `dice-migrate`, then create the admin login and accounts.

## Part 2: Deploying a release

OpenTofu ignores image changes on purpose, so it won't undo a promotion. Use OpenTofu for everything else (settings, sizes, schedule).

**Once, before the first run:** add the tagged address to `terraform.tfvars` and apply. The script checks the new version at this address before any users see it, and the app refuses addresses it doesn't know.

```hcl
extra_allowed_hosts = ["candidate---dice-app-XXXXXXXXXX-uc.a.run.app"]
```

Take the `dice-app-...run.app` part from `gcloud run services describe dice-app --region us-central1 --format='value(status.url)'`.

**To deploy:** open Cloud Shell in the prod project, clone the repo, and run:

```bash
deploy/scripts/deploy-prod.sh --dry-run   # prints the commands, changes nothing
deploy/scripts/deploy-prod.sh
```

With no version, it promotes whatever staging is running now and asks you to confirm. To pick one yourself, add it: `deploy/scripts/deploy-prod.sh v5.4.0`.

What it does, stopping at the first problem:

1. Finds the version staging is running and asks you to confirm it. If you named a different version, it asks before going on.
2. Copies the image from staging to prod (no rebuild).
3. Runs the database migrations.
4. Starts the new version with no users on it.
5. Checks it answers at its own address. If this fails, prod is untouched.
6. Asks `Shift 100% of traffic?` Answer `y` to go live.
7. Updates the worker.
8. Prints the command to go back to the previous version. Keep it until you're happy.

If the OCR image changed too, add `--with-ocr TAG` (the short code of the OCR image on staging).

A push-button version of this (a Cloud Build trigger that needs approval, [DIC-2338](https://linear.app/dicelabs/issue/DIC-2338) and [DIC-2339](https://linear.app/dicelabs/issue/DIC-2339)) comes after go-live.

## Settings reference

Every deployment (staging, prod, anywhere else) runs the same image two ways:

- **App:** the image's default command. Serves the API and the admin, with its styling.
- **Worker:** `python manage.py run_queued_tasks`, started every 20 minutes ([ADR 0023](adrs/0023-scheduled-batch-worker.md)). It exits at once if nothing is queued. Without a worker, uploads are accepted but never processed. (Locally, `docker-compose` runs `python manage.py db_worker`, which stays on.)

On each deploy, run `python manage.py migrate` once, before the new version gets traffic. Also run `python manage.py prune_db_task_results` on a schedule (e.g. daily) to clear old task records.

The app **and** the worker need these:

| Setting | What it's for |
|---|---|
| `SECRET_KEY` | Long random string. Keep it secret. |
| `DATABASE_URL` | Postgres connection string. |
| `ALLOWED_HOSTS` | The domain(s) the app is served from, comma-separated. |
| `PUBLIC_BASE_URL` | The app's public address, e.g. `https://pipeline.example.org`. Used in webhook download links. |
| `CSRF_TRUSTED_ORIGINS` | The same address when you use a custom domain. Without it, admin login fails with "CSRF verification failed". |
| `S3_BUCKET_NAME`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` (+ other `S3_*`) | Where uploaded and finished PDFs are kept ([ADR 0018](adrs/0018-s3-compatible-storage-backend.md)). |
| `SENTRY_DSN`, `SENTRY_ENVIRONMENT` | Error reporting, e.g. environment `staging` or `production`. |
| `RUN_PRECHECK`, `RUN_OCR`, … `RUN_POSTCHECK` | Which pipeline steps are on. |
| `OPENDATALOADER_HYBRID_URL` | Address of the OCR service. Keep it private: the worker reaches it, the internet doesn't. |
| `ANTHROPIC_API_KEY` | Only if `RUN_ALT_TEXT=True`. |
| `TRUSTED_PROXY_HOPS` | `1` on Cloud Run. Leave unset locally. If it's missing in a deployment, every caller shares one throttle counter ([ADR 0031](adrs/0031-api-throttling.md)). |
| `ORIGIN_LOCK_SECRET` | Optional. With Cloudflare in front, set a long random string and have Cloudflare send it as an `X-Origin-Secret` header. Requests without it get a 403, so nobody can skip Cloudflare through the raw `run.app` address ([ADR 0036](adrs/0036-origin-lock.md)). |
| `REQUIRE_HTTPS` | Leave unset (on). Only local development sets it to `False`. |
| `WEBHOOK_ALLOW_PRIVATE_URLS` | Local use only. Lets webhooks go to a receiver on your own machine. Never in staging or prod. |

Leave `DEBUG` alone (off by default) outside local development.

If a value is missing, the app still starts but sends a warning to Sentry ([ADR 0035](adrs/0035-startup-config-warnings.md)). That covers no `S3_BUCKET_NAME`, alt text on without `ANTHROPIC_API_KEY`, OCR on without `OPENDATALOADER_HYBRID_URL`, and HTTPS on without `ORIGIN_LOCK_SECRET`. Sentry only shows them if `SENTRY_DSN` is set.
