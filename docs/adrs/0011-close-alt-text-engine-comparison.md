# 11. Close the alt-text engine comparison without running it

## Status

Accepted. Amends [ADR 0005](0005-alt-text-engine.md)'s revisit trigger and [ADR 0006](0006-engine-comparison-tracking.md)'s planned first use — neither is rewritten; this ADR is the record of the amendment.

## Context

ADR 0005 picked Claude Vision for the `alt_text` stage without testing OpenDataLoader's built-in SmolVLM (256M-parameter) output against it, and set a revisit trigger: run that comparison for real once `AltTextService`/`ClaudeVisionClient` had a working implementation. ADR 0006 designed `EngineComparisonSample`, a general tracking table, with alt-text as its first intended user.

`AltTextService` now exists (built, tested, and validated against a real document). At that point — the trigger ADR 0005 named — the comparison was reconsidered rather than run.

## Decision

Do not run the SmolVLM-vs-Claude-Vision comparison, and do not build `EngineComparisonSample` for it. A 256M-parameter model is very unlikely to produce WCAG-standard image descriptions — a strong enough inference from model scale alone that the ~30-40-record manual review ADR 0006 scoped for this comparison isn't worth doing to confirm it empirically. Claude Vision is the settled engine for this stage, not a provisional one pending a test.

## Consequences

- ADR 0005's revisit trigger is closed. Re-open only if a genuinely different, meaningfully more capable lightweight captioning model appears (not just "SmolVLM is still there and free"), or if Claude Vision's cost/latency becomes a real operational problem at volume — either would warrant a new ADR, not reopening this one.
- `EngineComparisonSample`/`RUN_ENGINE_COMPARISON_LOGGING` (ADR 0006) is not built. ADR 0006's design remains available for some *other* future engine comparison (a different pipeline stage, a different pair of engines) — this decision closes out its originally-planned first user, not the mechanism itself.
- `finalize_tags` does not need `--enrich-picture-description` enabled for this reason (it was never needed for that stage's actual job — tagging — only for the now-cancelled comparison).
