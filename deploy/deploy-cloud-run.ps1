# DICE Document Pipeline — Cloud Run GPU deploy script
#
# Prerequisites:
#   - Cloud Build image pushed to Artifact Registry (run cloudbuild.yaml first)
#   - ANTHROPIC_API_KEY secret populated in Secret Manager
#
# Usage:
#   .\deploy\deploy-cloud-run.ps1
#   .\deploy\deploy-cloud-run.ps1 -ImageTag "abc123"   # deploy a specific commit SHA

param(
    [string]$ImageTag = "latest",
    [string]$Project = "core-db-475718",
    [string]$Region = "us-central1",
    [string]$ServiceName = "dice-document-pipeline"
)

$image = "us-central1-docker.pkg.dev/$Project/dice-pipeline/dice-document-pipeline:$ImageTag"

Write-Host "Deploying $image to Cloud Run ($Region)..."

gcloud run deploy $ServiceName `
    --image=$image `
    --project=$Project `
    --region=$Region `
    --service-account="dice-pipeline-worker@$Project.iam.gserviceaccount.com" `
    --gpu=1 `
    --gpu-type=nvidia-l4 `
    --cpu=8 `
    --memory=32Gi `
    --concurrency=1 `
    --min-instances=0 `
    --max-instances=3 `
    --timeout=600 `
    --no-allow-unauthenticated `
    --set-env-vars="USE_GCS=true,GCS_INBOUND_BUCKET=dice-pipeline-inbound,GCS_ARTIFACTS_BUCKET=dice-pipeline-artifacts,GCS_PREFIX=jobs,USE_DOCLING=true,CORS_ORIGINS=*" `
    --set-secrets="ANTHROPIC_API_KEY=PS_ANTHROPIC_API_KEY:latest"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Deploy complete. Service URL:"
    gcloud run services describe $ServiceName --project=$Project --region=$Region --format="value(status.url)"
} else {
    Write-Host "Deploy failed. Check Cloud Run logs:"
    Write-Host "  gcloud run services logs read $ServiceName --project=$Project --region=$Region --limit=50"
}
