# Integration Guide — Wiring a Service into the Pipeline API

This doc covers everything a consumer service (dice-portal, a WordPress plugin,
a future admin tool) needs to submit jobs and display progress to users.

## Service URL

Cloud Run service: `dice-document-pipeline` in `core-db-475718 / us-central1`

```powershell
# Get the live URL
gcloud run services describe dice-document-pipeline \
  --project=core-db-475718 --region=us-central1 \
  --format="value(status.url)"
```

The service is deployed with `--no-allow-unauthenticated`. Callers must present
a Google-signed OIDC token.

## Authentication

All requests need an `Authorization: Bearer <id-token>` header.

### From another Cloud Run service (e.g. dice-portal)

```python
import google.auth.transport.requests
import google.oauth2.id_token

PIPELINE_URL = "https://dice-document-pipeline-<hash>-uc.a.run.app"

def _id_token() -> str:
    req = google.auth.transport.requests.Request()
    return google.oauth2.id_token.fetch_id_token(req, PIPELINE_URL)

headers = {"Authorization": f"Bearer {_id_token()}"}
```

Add `google-auth` to the calling service's requirements. The service account
running dice-portal must have `roles/run.invoker` on the pipeline service:

```powershell
gcloud run services add-iam-policy-binding dice-document-pipeline \
  --project=core-db-475718 --region=us-central1 \
  --member="serviceAccount:<dice-portal-sa>@core-db-475718.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

### From local dev / curl

```powershell
$token = gcloud auth print-identity-token
curl -H "Authorization: Bearer $token" https://<service-url>/health
```

## Endpoints

### Health check

```
GET /health
→ {"status": "ok"}
```

### Submit a job

```
POST /jobs
Content-Type: multipart/form-data

Fields:
  file             PDF file (required)
  title            Human-readable document title (required)
  document_type    minutes | ordinance | resolution | report | form | other (required)
  jurisdiction_id  UUID of the organization from core.organizations (required)
  archival_object_id  UUID — if omitted, a new UUID is generated
  sensitivity_class   public (default) | internal | restricted
```

**Response `202 Accepted`:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

**Example (Python `httpx`):**
```python
import httpx

async def submit_pdf(pdf_path, title, document_type, jurisdiction_id):
    async with httpx.AsyncClient() as client:
        with open(pdf_path, "rb") as f:
            response = await client.post(
                f"{PIPELINE_URL}/jobs",
                headers={"Authorization": f"Bearer {_id_token()}"},
                data={
                    "title": title,
                    "document_type": document_type,
                    "jurisdiction_id": jurisdiction_id,
                },
                files={"file": (pdf_path.name, f, "application/pdf")},
                timeout=30,
            )
    response.raise_for_status()
    return response.json()["job_id"]
```

### Poll job status

```
GET /jobs/{job_id}
→ JobStatusResponse (see schema below)
```

**JobStatusResponse schema:**
```json
{
  "job_id": "uuid",
  "status": "queued | running | complete | manual_review | failed",
  "title": "string",
  "document_type": "string",
  "jurisdiction_id": "uuid",
  "created_at": "ISO-8601",
  "started_at": "ISO-8601 | null",
  "completed_at": "ISO-8601 | null",
  "processing_seconds": 42.7,
  "page_count": 12,
  "compliance_score": 74,
  "compliance_grade": "C",
  "remediated_pdf_uri": "gs://dice-pipeline-artifacts/jobs/uuid.pdf",
  "html_uri": "gs://dice-pipeline-artifacts/jobs/uuid.html",
  "markdown_uri": "gs://dice-pipeline-artifacts/jobs/uuid.md",
  "log_uri": "gs://dice-pipeline-artifacts/jobs/uuid.txt",
  "manual_review_items": [],
  "external_api_calls": [],
  "error": null
}
```

**Status meanings:**
| Status | Meaning |
|---|---|
| `queued` | Accepted, not yet started |
| `running` | Docling + assessment in progress |
| `complete` | Done, all outputs uploaded, score ≥ threshold |
| `manual_review` | Done, but one or more items need human review |
| `failed` | Pipeline error — see `error` field |

### Stream live progress (SSE)

```
GET /jobs/{job_id}/events
Accept: text/event-stream
```

Opens a Server-Sent Events stream. Each event is a JSON object:

```json
{"stage": "downloading", "message": "Downloading source PDF"}
{"stage": "converting",  "message": "Running Docling — OCR and layout analysis"}
{"stage": "converted",   "message": "Docling complete — 12 page(s), 3847 words"}
{"stage": "assessing",   "message": "Scoring ADA compliance"}
{"stage": "uploading",   "message": "Uploading results to storage"}
{"stage": "complete",    "message": "Job complete — score 74 (C)"}
```

Terminal stages that close the stream: `complete`, `manual_review`, `failed`.

**JavaScript (dice-portal or WP plugin):**
```javascript
function watchJob(jobId, onEvent, onDone) {
  const es = new EventSource(`${PIPELINE_URL}/jobs/${jobId}/events`, {
    // For authenticated Cloud Run, proxy through your own backend
    // or use a signed URL — EventSource does not support custom headers natively.
  });

  es.onmessage = (e) => {
    const event = JSON.parse(e.data);
    onEvent(event);
    if (["complete", "manual_review", "failed"].includes(event.stage)) {
      es.close();
      onDone(event);
    }
  };

  es.onerror = () => es.close();
  return es;
}
```

> **Auth note for browser SSE:** `EventSource` does not support `Authorization`
> headers. The simplest pattern for dice-portal is to proxy the SSE stream
> through a portal endpoint that adds the Google ID token server-side, then
> expose an unauthenticated (but session-gated) `/api/pipeline/jobs/{id}/events`
> route in the portal. See the portal integration section below.

## Recommended dice-portal Integration Pattern

```
Browser  →  portal /api/pipeline/jobs/:id/events  →  pipeline /jobs/:id/events
                (adds Google ID token)
```

1. Portal backend receives the upload from the browser form
2. Portal calls `POST /jobs` on the pipeline with the PDF and metadata
3. Portal stores `job_id` in its DB alongside the document record
4. Browser polls or connects to the portal's proxied SSE endpoint
5. Portal proxies the pipeline SSE stream, adding auth header
6. On `complete`: portal stores `html_uri`, `markdown_uri` from job status,
   then triggers WordPress CPT creation via the existing WP sync mechanism

## Document Types

Use these strings for `document_type` — consistent across portal and pipeline:

| Value | Description |
|---|---|
| `minutes` | Board / council meeting minutes |
| `agenda` | Meeting agenda or agenda packet |
| `ordinance` | County or township ordinance |
| `resolution` | Board resolution |
| `report` | Annual report, audit, financial report |
| `form` | Public-facing form |
| `policy` | Policy or procedure document |
| `other` | Anything else |

## Cost Estimation (DIC-561)

After a batch run, collect cost data from job status responses:

```python
# processing_seconds × Cloud Run GPU rate ÷ page_count
GPU_RATE_PER_SEC = 0.001706  # L4 + 8vCPU + 32GB in us-central1 (2026-06)

cost_per_job = job["processing_seconds"] * GPU_RATE_PER_SEC
cost_per_page = cost_per_job / job["page_count"]
```

## GCS Output URIs

Artifacts land in `gs://dice-pipeline-artifacts/jobs/{job_id}.*`.
To serve the HTML to WordPress, the portal service account needs
`roles/storage.objectViewer` on that bucket, or generate a signed URL.
