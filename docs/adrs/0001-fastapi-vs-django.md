# 1. Web framework: FastAPI (v1) vs. Django for the rebuild

## Status

Accepted

## Context

v1 (`dice-document-pipeline`) is a FastAPI app (`POST /jobs`, `GET /jobs/{id}`, SSE progress, `/health`) backed by an explicitly-placeholder `InMemoryJobStore` — lost on restart. It does have real, working adapters for the hard part of the problem: `workers/docling_pdf.py` (Docling OCR), `workers/opendataloader_adapter.py` (auto-tagging), `workers/adobe_pdf_services.py` (evaluated, not chosen), and a GCS storage adapter. The engine bake-off is done; only the app around it needs rebuilding.

The rebuild's actual requirements go beyond "run a job, report status": multi-tenant orgs, service accounts, authenticated API access, and durable/operable state.

## Options

**Keep FastAPI** — reuses the adapters with zero porting risk, and is minimal. But everything else (durable job state, auth, admin visibility) has to be assembled from scratch, one library at a time.

**Django + DRF** — heavier, and requires porting the adapters into the new app structure. In exchange it gives, out of the box:
- **Storage**: `django.core.files.storage`, swappable `FileSystemStorage` (local) / `GoogleCloudStorage` (Cloud Run) — no hand-rolled GCS shim.
- **Tasks**: Django 6's `django.tasks` replaces the in-memory store with a real `Remediation` model + ORM persistence (see [ADR 0002](0002-task-execution-framework.md)).
- **Auth**: `django.contrib.auth` + DRF `TokenAuthentication`, already wired to `ServiceAccount.token`.
- **Security**: CSRF, clickjacking, password validation, HTTPS/HSTS — configured, not assembled.
- **ORM + migrations**, **admin site** (free ops visibility), and DRF serializers/viewsets for the API layer.
- Mature ecosystem (`django-environ`, `django-storages`, `whitenoise`) for the remaining production concerns.

## Decision

Rebuild on Django + DRF. Port the v1 adapters (Docling, OpenDataLoader, GCS) into `remediation`'s service/task layer post a review of engine-choice. One-time porting cost < ongoing cost of integrating every necessary functionality in a piecemeal way.

## Consequences

- Org/auth/job-state modeling and admin visibility come essentially free.
- Adapters are preserved by porting, not rewritten (currently untested in the new app — see `remediation`).
- SSE progress streaming needs to be rebuilt against Django's async views/ASGI (supported since Django 4.1, expanded in 6) rather than carried over from FastAPI as-is.
- Storage and tasks can both follow the same swappable-abstraction pattern rather than a vendor SDK or custom Protocol. Although the intention is to deploy to GCS, avoiding vendor lock-in is an imperative long-term goal for the department and project.
