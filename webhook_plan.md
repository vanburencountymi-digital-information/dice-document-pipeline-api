# Webhook delivery + download endpoint for finished remediations

## Context

Right now the API has no way for a caller to actually retrieve a remediated PDF, or be notified when one's ready — `RemediationSerializer` only exposes status metadata (`id`, `document_id`, `status`, `error`, timestamps), and there's no concept of "the final file" persisted anywhere (each pipeline stage writes its own `RemediationArtifact.output_uri`, but nothing designates which one is the last).

The planned integration: a WordPress site uploads a PDF to `/api/submit-document/`, our worker runs the six-stage pipeline, and WordPress needs to get the finished file back without polling forever. Decided in conversation: **notify + pull**, not push-the-file — the worker POSTs a small signed JSON webhook when the job finishes (to a `callback_url` supplied at submission), and the receiver does a `GET` against a link in that payload to fetch the actual PDF. This avoids coupling webhook delivery reliability to a large binary transfer, and forces building the (currently missing) download endpoint as part of the same effort rather than as separate follow-up work.

## Approach

### 1. `Remediation` gets two new fields (`remediation/models.py`)

```python
callback_url = models.URLField(blank=True, default="")
final_output_uri = models.CharField(max_length=500, blank=True, default="")
```

`callback_url` is **optional** — not every submission is necessarily webhook-driven (e.g. a future admin-panel submission path with no receiver to call back), so the webhook is opt-in per submission rather than mandatory. `send_webhook_notification` (section 4) no-ops if it's blank. It's captured once at submission and never changed afterward — a dedup-hit resubmission (existing `get_or_create_from_upload` behavior) ignores any newly-supplied `callback_url` on an existing row, same as it already ignores everything else about a resubmission that isn't "check current state." This mirrors ADR 0009's existing philosophy (resubmitting doesn't reconfigure or restart a job).

`document-status`/`download_url` (sections 5-6 below) work regardless of whether `callback_url` was set — a webhook-driven caller can still poll, and a poll-only caller can still discover the download link once complete.

`final_output_uri` is set explicitly when a job completes, rather than inferred at read time by walking `RemediationArtifact` rows and guessing which was the last *transforming* stage (verification steps like `postcheck` pass `pdf_uri` through unchanged, so "last artifact" isn't always "last artifact with a real transform"). Explicit-and-persisted avoids that inference logic existing in two places (download view + webhook payload builder) and avoids getting it subtly wrong.

Needs a migration — **you run `makemigrations`/`migrate` yourself**, same as every other model change this session.

### 2. `ServiceAccount` gets a webhook signing secret (`accounts/models.py`, `accounts/services.py`)

```python
webhook_secret = models.CharField(max_length=64, blank=True, default="")
```

Auto-generated in `ServiceAccountService.create()` via `secrets.token_hex(32)`, same moment the auth `Token` is created — same out-of-band-sharing model as the token already uses (nothing currently exposes the token's value through an API either; whoever runs account setup shares it directly). `accounts/tests/factories.py`'s `ServiceAccountFactory` needs the same `@factory.post_generation` treatment its `token` hook already gets.

Needs its own migration in the `accounts` app.

### 3. `remediation/webhook_client.py` (new) — `WebhookClient`

Not part of the `adapters/` hierarchy — it's not a pipeline-stage adapter (no `RemediationArtifact` tracking, no swap-in-a-different-implementation shape), so it gets its own small module and its own exception type rather than reusing `AdapterError`.

```python
class WebhookDeliveryError(Exception):
    pass


class WebhookClient:
    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def notify(self, callback_url: str, *, secret: str, payload: dict) -> None:
        """POSTs `payload` as signed JSON. Raises `WebhookDeliveryError` on any failure
        (connection error, timeout, non-2xx) — one attempt; the caller (`send_webhook_notification`)
        owns retry via re-enqueueing, not this method.
        """
        body = json.dumps(payload).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        try:
            response = httpx.post(
                callback_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": f"sha256={signature}",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(f"webhook delivery to {callback_url} failed: {exc}") from exc
```

`WebhookClient.notify()` itself stays single-attempt (one POST, raises on failure) — retry is handled one layer up, at the task level (below), not inside the client.

Uses `httpx` (already a dependency — pulled in transitively by `anthropic`), no new package needed.

### 4. `remediation/tasks.py` — dispatch the webhook, retry via Django's own `run_after` re-enqueue

`django.tasks` has no automatic retry yet (confirmed directly against the installed source — `Task`'s only scheduling knobs are `priority`/`queue_name`/`run_after`/`backend`; full retry support is reportedly coming in Django 6.2, not yet released). It does have a first-party primitive worth building manual retry on instead of a hand-rolled sleep loop: `task.using(run_after=<datetime>)` re-enqueues a task to run at/after a specific time. That's the idiom used here.

```python
MAX_WEBHOOK_ATTEMPTS = 5
WEBHOOK_BACKOFF_BASE_SECONDS = 30

@task()
def send_webhook_notification(remediation_id: str, attempt: int = 1) -> None:
    remediation = RemediationService().get(remediation_id)
    if not remediation.callback_url:
        return

    payload = {
        "remediation_id": str(remediation.id),
        "document_id": remediation.content_hash,
        "status": remediation.status,
    }
    if remediation.status == Remediation.JobStatus.COMPLETE:
        payload["download_url"] = urljoin(
            settings.PUBLIC_BASE_URL, reverse("document-download", args=[remediation.content_hash])
        )
    elif remediation.status == Remediation.JobStatus.FAILED:
        payload["error"] = remediation.error

    try:
        WebhookClient().notify(
            remediation.callback_url,
            secret=remediation.service_account.webhook_secret,
            payload=payload,
        )
    except WebhookDeliveryError:
        if attempt >= MAX_WEBHOOK_ATTEMPTS:
            raise  # visible as a permanently FAILED TaskResult
        delay = WEBHOOK_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        send_webhook_notification.using(
            run_after=timezone.now() + timedelta(seconds=delay)
        ).enqueue(remediation_id, attempt=attempt + 1)
```

**Honest caveat, not glossed over**: `ImmediateBackend` (this project's only backend today) ignores `run_after` entirely — checked its `enqueue()` directly; it calls `_execute_task()` unconditionally with no time check. So locally, this retries up to `MAX_WEBHOOK_ATTEMPTS` back-to-back with no real delay between attempts — still useful for a truly transient blip (a DNS hiccup, a momentary connection reset), but not real exponential backoff. That only becomes real once a backend that honors `run_after` is in use — the eventual Cloud Tasks swap (ADR 0002), already tracked as backlog, not something to build here.

Async note: Django 6 tasks genuinely support `async def` task functions natively (confirmed in `Task.call()` — it branches on `iscoroutinefunction` and bridges via `asgiref`), so `send_webhook_notification` could legitimately use `httpx.AsyncClient` instead of sync `httpx.post`. Not doing that here — nothing else in this codebase is async yet, and introducing the first async code path for one task feels like a separate decision worth making deliberately rather than as a side effect of this feature. Flagging it as a real, available option if async becomes a broader direction later.

`process_remediation` enqueues this as a **separate task**, not an inline call, from a `finally` block wrapped around the existing `try`/`except`/`else` — **not** a line placed "after" that block. The generic `except Exception` branch re-raises (needed so an unexpected failure still surfaces as a `TaskResult` failure, not a silently-swallowed one), and `NotCompliant`/`AlreadyCompliant` don't — so a line placed after the whole try/except/else would only ever run for the branches that don't re-raise. `finally` is the one placement that runs on every exit path regardless, including one that re-raises:

```python
try:
    for step_service_cls in PIPELINE_STEPS:
        ...
        pdf_uri = step_service.run(remediation, pdf_uri=pdf_uri)

except AlreadyCompliant:
    service.mark_complete(remediation)

except NotCompliant as exc:
    service.mark_failed(remediation, f"postcheck: not PDF/UA-1 compliant: {exc}")

except Exception as exc:
    service.mark_failed(remediation, str(exc))
    raise

else:
    service.mark_complete(remediation)

finally:
    send_webhook_notification.enqueue(str(remediation.id))
```

By the time `finally` runs, the relevant `mark_complete`/`mark_failed` call has already committed the row's final `status`/`error`, so `send_webhook_notification` always reads current state regardless of which branch got there. Enqueued unconditionally, every time — the "is there actually a callback to notify?" check lives once, inside `send_webhook_notification` itself (`if not remediation.callback_url: return`), rather than duplicated in `process_remediation` too. Separate task = webhook delivery failure can't affect the remediation's own recorded status, and it's independently retryable/observable later without touching pipeline logic. The success (`else`) branch's `mark_complete` needs updating to accept and persist `final_output_uri=pdf_uri` (the loop's accumulated value); the `AlreadyCompliant` branch passes `final_output_uri=remediation.source_pdf_uri` (nothing transformed it).

### 5. `api/views.py` + `api/urls.py` — the download endpoint

```python
class DocumentDownloadView(ServiceAccountRequiredMixin):
    def get(self, request: Request, content_hash: str) -> FileResponse:
        remediation = RemediationService().latest_for_document(self.service_account, content_hash)
        if remediation is None or remediation.status != Remediation.JobStatus.COMPLETE:
            raise Http404
        return FileResponse(
            default_storage.open(remediation.final_output_uri),
            content_type="application/pdf",
            filename=remediation.original_filename or "document.pdf",
            as_attachment=True,
        )
```

`GET /api/document-download/<content_hash>/`, added to `api/urls.py` right after `document-status`. Same auth (`ServiceAccountRequiredMixin`) and same tenant-scoping (`latest_for_document`) as `DocumentStatusView` — a caller can only ever download their own service account's documents. Not-complete and wrong-tenant both 404 identically, matching `document-status`'s existing no-existence-leak behavior.

### 6. `remediation/serializers.py` — expose `download_url` for polling callers too

Add a computed field to `RemediationSerializer` (`SerializerMethodField`) that returns the same `document-download` URL when `status == COMPLETE`, else `None`. Keeps the webhook and polling flows consistent — a caller not using webhooks still discovers the link through `document-status`.

### 7. `RemediationUploadSerializer` + `CreateRemediationView` — accept optional `callback_url`

`RemediationUploadSerializer` gains `callback_url = serializers.URLField(required=False, allow_blank=True)`. `CreateRemediationView.post()` passes it through: `get_or_create_from_upload(..., callback_url=upload.validated_data.get("callback_url", ""))`.

One wrinkle worth naming: on a dedup-hit (existing row, `created=False`), a `callback_url` supplied on *this* request gets silently ignored (per point 1 above — the row's original `callback_url`, or lack of one, wins). That's consistent with dedup's existing "resubmission reports current state, doesn't reconfigure" behavior, but it does mean a second caller submitting the same document with a *different* callback would never get notified at their URL. Not solving that here (would mean supporting multiple subscribers per document — out of scope, see below) — just flagging it as a known sharp edge.

### 8. `config/settings.py` — one new setting

```python
PUBLIC_BASE_URL = env.str("PUBLIC_BASE_URL", default="http://localhost:8000")
```

Used to build absolute `download_url`s from inside a task (no request object available there) and in the serializer's `SerializerMethodField` (via the same helper, not `request.build_absolute_uri`, so both call sites build the URL identically).

## Tests

- **`accounts/tests/test_services.py`**: `ServiceAccountService.create()` generates a non-empty `webhook_secret`.
- **`remediation/tests/test_services.py`**: `callback_url` passed through on create/`get_or_create_from_upload`; a dedup-hit resubmission with a different `callback_url` doesn't change the existing row's; `mark_complete` persists `final_output_uri`.
- **`remediation/tests/test_webhook_client.py`** (new): `WebhookClient.notify` sends the correct signature (verify HMAC independently in the test) and payload; raises `WebhookDeliveryError` on connection error / timeout / non-2xx; succeeds silently on 2xx. Mock `httpx.post` (`@patch("remediation.webhook_client.httpx.post", autospec=True)`).
- **`remediation/tests/test_tasks.py`**: `process_remediation` always enqueues `send_webhook_notification` (mock the task, assert `.enqueue` called with the right id) in every terminal branch (already-compliant, postcheck-failed, generic exception, success). `send_webhook_notification` itself: no-ops when `callback_url` is blank; builds the right payload for complete/failed and calls `WebhookClient.notify` with the service account's secret when it isn't (mock `WebhookClient`); on `WebhookDeliveryError` with attempts remaining, re-enqueues itself via `.using(run_after=...)` with `attempt` incremented (mock `send_webhook_notification.using`, assert the call); re-raises once `MAX_WEBHOOK_ATTEMPTS` is reached instead of re-enqueueing.
- **`api/tests/test_remediation_views.py`**: `CreateRemediationView` passes `callback_url` through to the service (extend the existing `@patch("api.views.RemediationService", ...)` pattern), and omitting it still succeeds (it's optional). New `DocumentDownloadViewTests` class mirroring `DocumentStatusViewTests`' shape: 200 + correct file bytes/headers for a complete job scoped to the right tenant, 404 for not-found/not-complete/wrong-tenant.

## Explicitly out of scope

- Real timed backoff for webhook retries — `run_after` re-enqueueing is wired up, but `ImmediateBackend` ignores it (see the caveat in section 4), so actual delayed backoff only takes effect once a `run_after`-honoring backend is in use (the eventual Cloud Tasks swap, ADR 0002).
- Multiple callback URLs / subscribers per document — one `callback_url` per `Remediation`, fixed at creation.
- Rate limiting on `/api/submit-document/` — already tracked as its own backlog item in `implementation_plan.md`, not part of this change.
- Async task functions — Django 6 supports them natively, but nothing else in this codebase is async yet; not introducing that here (see section 4's async note).

## Verification

1. `python manage.py makemigrations accounts remediation && python manage.py migrate` (you run this).
2. `python manage.py test accounts remediation api` — all new + existing tests pass.
3. `pre-commit run --all-files` clean.
4. Real end-to-end check via Docker: submit a document with a `callback_url` pointing at a throwaway local listener (e.g. `python -m http.server` or `webhook.site`), confirm the signed POST arrives with a working `download_url`, and confirm `GET` on that URL returns the actual finished PDF.
