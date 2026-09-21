# 18. S3-compatible storage backend

## Status

Accepted

## Context

Storage is currently `FileSystemStorage`, writing straight to local disk (`MEDIA_ROOT`). That doesn't survive a real deploy: a multi-instance host doesn't guarantee the container handling a later pipeline step can see a file an earlier step (or the original upload) wrote to a different instance's local disk. Storage needs to be a real, shared backend before any of that can work.

`django-storages` supports this and allows us avoid vendor-locking to GCS.

Additionally, every pipeline step's `run()` currently calls `default_storage.path(pdf_uri)` to hand local tools (veraPDF, pikepdf, PyMuPDF, OpenDataLoader) a real filesystem path. No `S3Storage` or bucket storage implement `.path()` — there's no local filesystem path for a remote object — so this was never the "zero application code change" swap it was once assumed to be.

## Decision

Use `django-storages[s3]`, configured against S3-compatible endpoint with HMAC auth. Settings are generic S3-API naming throughout (`S3_BUCKET_NAME`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, plus `S3_REGION_NAME`/`S3_SIGNATURE_VERSION`/`S3_ADDRESSING_STYLE` for providers that need them, e.g. Cloudflare R2 requires `region_name="auto"`) — so a future move to any other S3-compatible provider (AWS S3, Cloudflare R2, DigitalOcean Spaces, Backblaze B2, self-hosted Garage) is a config change, not a code change. `STORAGES["default"]` falls back to `FileSystemStorage` whenever `S3_BUCKET_NAME` is unset, keeping local dev unchanged.

`ArtifactService` gained two methods that replace every `default_storage.path()` call:
- `local_input_copy(pdf_uri)` — a context manager that downloads the input to a real local temp file via `default_storage.open()` (a method every backend implements), for local tools to operate on.
- `persist_output(local_path, dest_uri)` — uploads a local tool's output back via `default_storage.save()`, deleting any existing object at `dest_uri` first so a rerun overwrites in place rather than `Storage.save()`'s default behavior of suffixing a new name on collision — this matters because ADR 0008's paths are deterministic on purpose.

`construct_output_dir` now builds a pure storage-key-prefix string instead of a real directory path.

Local dev/CI can optionally run against a self-hosted Garage container (`docker-compose.yml`'s `garage` service — not MinIO, whose open-source repo was archived in 2026 after its console was stripped from the free tier the year before) using the same `S3Storage` backend class, exercising the real rework against something with genuinely no local filesystem path — not required, `FileSystemStorage` keeps working without it. Tests also cover the real `S3Storage` class directly via `moto` (`S3StorageIntegrationTests`), matching how `django-storages`' own test suite validates it.

## Consequences

- Every step service's `run()` changed shape: the whole unit of work (input copy, adapter call, output persist) is now one `try` block, since remote storage I/O can fail in ways pure string math (the old `.path()` calls) never could.
- New test infrastructure: `write_fake_pdf`/`fake_adapter_output` helpers (real bytes are now required behind any URI a test exercises), and a `NoPathStorage` in-memory `Storage` subclass proving the rework doesn't depend on `.path()` at all — subclassing `FileSystemStorage` for this would be wrong, since its own `exists()`/`open()`/`save()`/`delete()` are implemented in terms of `.path()` internally.
- The HMAC key pair is a new operational responsibility: generated via Cloud Console → Cloud Storage → Settings → Interoperability, tied to a service account scoped to just this bucket, stored and rotated like any other secret.
- No file retention/cleanup policy exists yet (tracked separately) — this is now a real, billed cost instead of invisible local-disk accumulation.
