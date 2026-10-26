#!/usr/bin/env bash
# Promote a release that is already running on staging to prod (DIC-2528).
# Stand-in for the push-button promotion (DIC-2338/2339), which reuses these steps later.
# Usage: deploy/scripts/deploy-prod.sh [vX.Y.Z] [--with-ocr TAG] [--dry-run]
# With no version, it promotes the version staging is running now.
# Run it in Cloud Shell: copying the images is much faster inside Google's network.
# Docs: docs/deploying-prod.md.
set -euo pipefail

REGION=us-central1
STAGING_PROJECT=ada-document-pipeline-staging
PROD_PROJECT=ada-document-pipeline-prod
REPO=dice
APP_SERVICE=dice-app
MIGRATE_JOB=dice-migrate
WORKER_JOB=dice-worker
DUMP_JOB=dice-db-dump
TAG=candidate

STAGING_REGISTRY=$REGION-docker.pkg.dev/$STAGING_PROJECT/$REPO
PROD_REGISTRY=$REGION-docker.pkg.dev/$PROD_PROJECT/$REPO

VERSION=""
OCR_TAG=""
DRY_RUN=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --with-ocr) OCR_TAG="${2:?--with-ocr needs an OCR image tag}"; shift ;;
    -*) echo "Unknown option: $1" >&2; exit 2 ;;
    *) VERSION="$1" ;;
  esac
  shift
done
if [[ -n "$VERSION" && ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 [vX.Y.Z] [--with-ocr TAG] [--dry-run]" >&2
  exit 2
fi

# Runs a command that changes something. With --dry-run it only prints it.
run() {
  if $DRY_RUN; then
    echo "[dry run] $*"
  else
    echo "+ $*"
    "$@"
  fi
}

# Asks a yes/no question. A dry run never asks, and counts as "yes".
confirm() {
  $DRY_RUN && return 0
  read -r -p "$1 [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]]
}

run_image() {
  gcloud run services describe "$APP_SERVICE" --region "$REGION" --project "$1" \
    --format='value(spec.template.spec.containers[0].image)'
}

echo "== 1. Check the version"
STAGING_RUNNING=$(run_image "$STAGING_PROJECT")
echo "Staging is running: $STAGING_RUNNING"
if [[ -z "$VERSION" ]]; then
  VERSION="${STAGING_RUNNING##*:}"
  [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo "Can't read a version from the staging image. Pass one: $0 vX.Y.Z" >&2
    exit 1
  }
  confirm "Promote $VERSION to prod?" || exit 1
elif [[ "$STAGING_RUNNING" != *":$VERSION" ]]; then
  confirm "Staging is not running $VERSION. Promote it anyway?" || exit 1
fi
crane manifest "$STAGING_REGISTRY/app:$VERSION" >/dev/null || {
  echo "app:$VERSION is not in the staging registry. Stopping." >&2
  exit 1
}
if [[ -n "$OCR_TAG" ]]; then
  crane manifest "$STAGING_REGISTRY/opendataloader-hybrid:$OCR_TAG" >/dev/null || {
    echo "opendataloader-hybrid:$OCR_TAG is not in the staging registry. Stopping." >&2
    exit 1
  }
fi
PREVIOUS_REVISION=$(gcloud run services describe "$APP_SERVICE" --region "$REGION" \
  --project "$PROD_PROJECT" --format='value(status.traffic[0].revisionName)')
SERVICE_URL=$(gcloud run services describe "$APP_SERVICE" --region "$REGION" \
  --project "$PROD_PROJECT" --format='value(status.url)')
CANDIDATE_URL="${SERVICE_URL/https:\/\//https://$TAG---}"
echo "Prod is currently serving revision: $PREVIOUS_REVISION"

APP_IMAGE=$PROD_REGISTRY/app:$VERSION

echo "== 2. Copy the image(s) to prod"
run crane copy "$STAGING_REGISTRY/app:$VERSION" "$APP_IMAGE"
if [[ -n "$OCR_TAG" ]]; then
  OCR_IMAGE=$PROD_REGISTRY/opendataloader-hybrid:$OCR_TAG
  run crane copy "$STAGING_REGISTRY/opendataloader-hybrid:$OCR_TAG" "$OCR_IMAGE"
fi

echo "== 3. Run migrations"
run gcloud run jobs update "$MIGRATE_JOB" --image "$APP_IMAGE" --region "$REGION" --project "$PROD_PROJECT"
run gcloud run jobs execute "$MIGRATE_JOB" --region "$REGION" --project "$PROD_PROJECT" --wait

echo "== 4. Deploy with no traffic"
run gcloud run services update "$APP_SERVICE" --image "$APP_IMAGE" --region "$REGION" \
  --project "$PROD_PROJECT" --no-traffic --tag "$TAG"

echo "== 5. Health check: $CANDIDATE_URL/ping/"
if $DRY_RUN; then
  echo "[dry run] curl $CANDIDATE_URL/ping/ (up to 6 tries, 10 seconds apart)"
else
  healthy=false
  for attempt in 1 2 3 4 5 6; do
    if curl -fsS --max-time 30 "$CANDIDATE_URL/ping/" | grep -q "pong"; then
      healthy=true
      break
    fi
    echo "Not ready (try $attempt of 6), waiting 10 seconds..."
    sleep 10
  done
  $healthy || {
    echo "Health check failed. Prod is still serving $PREVIOUS_REVISION. Stopping." >&2
    echo "Is $TAG---${SERVICE_URL#https://} in extra_allowed_hosts (deploy/terraform/prod)?" >&2
    exit 1
  }
  echo "Health check passed."
fi

echo "== 6. Shift traffic"
confirm "Shift 100% of traffic to $VERSION?" || {
  echo "Not shifted. Prod is still serving $PREVIOUS_REVISION." >&2
  exit 1
}
run gcloud run services update-traffic "$APP_SERVICE" --region "$REGION" \
  --project "$PROD_PROJECT" --to-tags "$TAG=100"

echo "== 7. Update the worker"
run gcloud run jobs update "$WORKER_JOB" --region "$REGION" --project "$PROD_PROJECT" \
  --container worker --image "$APP_IMAGE"
if [[ -n "$OCR_TAG" ]]; then
  run gcloud run jobs update "$WORKER_JOB" --region "$REGION" --project "$PROD_PROJECT" \
    --container ocr --image "$OCR_IMAGE"
fi

echo "== 8. Update the nightly database dump"
run gcloud run jobs update "$DUMP_JOB" --image "$APP_IMAGE" --region "$REGION" --project "$PROD_PROJECT"

echo "== 9. To roll back, run:"
echo "gcloud run services update-traffic $APP_SERVICE --region $REGION --project $PROD_PROJECT --to-revisions $PREVIOUS_REVISION=100"
