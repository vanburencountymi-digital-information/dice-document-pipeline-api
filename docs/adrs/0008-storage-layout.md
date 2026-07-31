# 8. Storage layout keyed by document, not filename

## Status

Accepted

## Context

Uploaded PDFs and each pipeline stage's output need somewhere to live in storage (`default_storage`, currently `FileSystemStorage` for development purposes.) The original layout saved uploads flat, named after whatever the uploader called the file (`remediations/<filename>`), with Django's storage silently appending a random suffix on a name collision. That made two things hard: finding every attempt tied to one document (nothing grouped them), and telling a document's original apart from a different document that happened to share a filename.

## Decision

Key every path by the document's identity, not the upload's filename:

- Original: `remediations/<service_account_id>/<content_hash>/<original_filename>`
- Each stage's output: `remediations/<service_account_id>/<content_hash>/<remediation_id>/<step>/<original_filename>` (e.g. `.../ocr/document.pdf`)

`service_account_id` is included specifically to preserve existing multi-tenant isolation (`RemediationService.latest_for_document`'s dedup is already scoped per service account — two tenants uploading byte-identical files must not collide in storage either). The human-readable filename is kept as the actual on-disk name (not renamed to something generic like `original.pdf`) so the file tree stays browsable; `OCRAdapter` implementations are expected to rename their output to match the input's filename exactly if the underlying tool names it something else, so every stage's artifact is findable under the same name.

Since `Remediation` never stored the uploader's filename anywhere itself (only the storage path, which is now decoupled from it), added `Remediation.original_filename` as its own field — otherwise this is genuinely unrecoverable data, e.g. "what file was this failed job?"

## Consequences

- Because the original's path is deterministic per `(service_account, content_hash)`, `get_or_create_from_upload` checks `default_storage.exists(...)` before saving — so anything that re-creates a `Remediation` for a document whose original file is already on disk (e.g. a future retry mechanism, see [ADR 0009](0009-no-retry-mechanism-yet.md)) won't re-save, and duplicate, identical bytes.
- `RemediationSerializer` now exposes `original_filename`, so a failed job's identity is visible via the API, not just by reading storage paths directly.
- `docker-compose.yml` bind-mounts `./media:/app/media` for local dev, so this layout is directly browsable from the host rather than only via `docker exec`.
