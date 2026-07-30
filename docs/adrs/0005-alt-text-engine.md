# 5. Alt-text engine: Claude Vision

## Status

Accepted

## Context

The `alt_text` stage generates descriptions for untagged, non-decorative figures — the bar is WCAG-standard image descriptions, not just "some text in the alt attribute."

OpenDataLoader, which already backs the `ocr`/`finalize_tags` stage (see [ADR 0004](0004-ocr-tagging-engine.md)), also produces alt text as part of its own internal pipeline via `--enrich-picture-description`, backed by SmolVLM (256M parameters) — confirmed from OpenDataLoader's own docs. What's not confirmed: OpenDataLoader makes no WCAG or accessibility-standard claim for this output either way, and no head-to-head comparison against Claude Vision has actually been run. The concern that a 256M-parameter model won't clear the WCAG bar is a reasonable inference from model scale, not a tested result. Using OpenDataLoader's built-in output would mean no separate API call and no separate adapter.

## Options Considered

1. Claude Vision, via a dedicated `AltTextService`/`ClaudeVisionClient` — a real API call per figure, independent of the `ocr`/`finalize_tags` stage's engine.
2. OpenDataLoader's built-in alt-text output (SmolVLM, 256M parameters) — free (already computed as part of the `finalize_tags` stage), untested against the WCAG bar in either direction.

## Decision

Use Claude Vision (`AltTextService`/`ClaudeVisionClient`) for alt-text generation at this time. Getting a working pipeline off the ground at a high quality standard is the priority, and Claude Vision is the safer bet for that without needing to run a comparison test first — v1's own bake-off already validated it end-to-end. Actually testing SmolVLM's output quality is real work (assembling a representative set of figures, evaluating descriptions against the WCAG bar) that isn't worth blocking initial implementation on.

This isn't being treated as a permanent lock-in the way OpenDataLoader is in ADR 0004 — image-captioning models are improving quickly, and OpenDataLoader's built-in SmolVLM (or another lightweight alternative) is worth trialing as a real, tested quality/cost/latency comparison against Claude Vision once the `alt_text` stage has a working implementation to test against. Alt-text stays its own separate, swappable stage/adapter regardless (see [ADR 0003](0003-pipeline-steps-and-branching.md) and [ADR 0004](0004-ocr-tagging-engine.md)), so that comparison doesn't require touching any other stage.

## Consequences

- `AltTextService` depends on the Anthropic API (`ClaudeVisionClient`) — a per-figure network call, with the cost and latency that implies, gated by its own `RUN_ALT_TEXT` setting like every other stage.
- No coupling to OpenDataLoader for this stage: swapping the `ocr`/`finalize_tags` engine later doesn't affect alt-text, and vice versa.
- Revisit trigger, not a fixed timeline: once `AltTextService`/`ClaudeVisionClient` has a working implementation, run an actual comparison against OpenDataLoader's SmolVLM output on real documents before treating either direction as settled. Re-trial is warranted if that test shows SmolVLM clears the WCAG bar closely enough that the operational simplicity of skipping a separate API call is worth the quality tradeoff, or if Claude Vision's API cost/latency becomes a real problem at volume. Until that test happens, Claude Vision is the default and only implemented adapter for this stage.
- That comparison will be tracked via a general, reusable engine-comparison mechanism, not built ad hoc when the revisit happens — see [ADR 0006](0006-engine-comparison-tracking.md) for the tracking methodology and sample size.
