# 13. Pipeline continues past a failed step

## Status

Accepted

## Context

[ADR 0003](0003-pipeline-steps-and-branching.md) decided that a failed stage fails the whole `Remediation` — no partial success, no resume. In practice, that meant `remediation/tasks.py`'s single try/except around the entire step loop: any step's `AdapterError` (or any other exception) aborted the pipeline immediately. A document that made it through `precheck`/`ocr`/`font_repair` successfully but then hit a transient `alt_text` failure (a Claude API timeout, say) lost all of that completed work — the job was marked `FAILED` with nothing usable to show for it, even though most of the remediation had actually succeeded.

For a government accessibility tool, that's the wrong tradeoff. A partially-remediated document — tagged, font-repaired, metadata-fixed, just missing alt text — is materially more useful to a screen-reader user than nothing at all. `ScoringService` already worked this way (it's explicitly non-blocking, "a failure here must never block postcheck"), just not any other step.

## Decision

Every step service now catches its own failures instead of letting them propagate: `run()` wraps its adapter call(s) in a broad `except Exception`, records the failure on that step's own `RemediationArtifact` (`self.mark_failed(...)`), logs it (`LOGGER.exception(...)` — the only failure signal left once this stops surfacing as a task-level failure; a real error tracker, e.g. Honeybadger, is a deliberate near-future follow-up, not built here), and returns the input `pdf_uri` unchanged rather than raising. The pipeline always reaches `PostCheckService`.

`VerificationService` (`PrecheckService`/`PostCheckService`) needed one more distinction beyond the other steps, since it's the one whose job *is* producing a verdict rather than transforming the file: a real "not compliant" result is different from "the adapter crashed and produced no verdict at all." Both still avoid aborting the pipeline, but they can't be handled identically:

- `PrecheckService`: an adapter crash is treated as inconclusive and the pipeline just continues to remediation, the same as if precheck had genuinely found the document non-compliant. It was never the terminal check anyway.
- `PostCheckService`: there's no later check to fall back on, so an adapter crash here still has to end the job — but as `NotCompliant` with an honest message ("postcheck could not run: ...") rather than defaulting to `COMPLETE` (nothing verified the output is actually compliant) or presenting a misleading "0 rules failed" summary. Critically, raising `NotCompliant` doesn't abort the *task* — `remediation/tasks.py` catches it and calls `mark_failed` without re-raising, so the pipeline run itself still completes normally; only the `Remediation`'s own status reflects "not confirmed compliant."

This is `VerificationService.handle_verification_error` — a new template-method hook alongside the existing `handle_result`, defaulting to a no-op (matching `PrecheckService`'s "just continue" behavior) and overridden by `PostCheckService` to still raise `NotCompliant`.

`Remediation.status` is now purely a function of `AlreadyCompliant`'s early exit or postcheck's final verdict (genuine or inconclusive) — never "some unrelated step blew up before we got there."

## Consequences

- Reverses [ADR 0003](0003-pipeline-steps-and-branching.md)'s "a failed stage fails the whole Remediation, no partial success" decision for every step except the verification steps' terminal behavior, which still ends the job (just without aborting the task).
- `remediation/tasks.py`'s top-level `except Exception: mark_failed; raise` stays in place as a true last-resort net — for something outside the step abstraction entirely (e.g. `RemediationService`'s own DB calls failing) — but ordinary step failures no longer reach it.
- `RemediationArtifact.status == FAILED` for one step no longer implies the whole job failed — a caller reading a `COMPLETE` (or even `FAILED`-from-postcheck) `Remediation`'s artifacts may still see individual `FAILED` rows for steps that didn't block the rest of the pipeline. `RemediationSerializer`'s response body is how "what failed and why" stays visible to a caller, not the top-level `status` alone.
- No error tracking (Honeybadger or similar) for these now-swallowed exceptions exists yet — logging is the only signal until that's built, explicitly flagged as near-future follow-up work, not in scope here.
