# 6. General mechanism for tracking engine/service comparisons

## Status

Accepted

## Context

[ADR 0005](0005-alt-text-engine.md) picked Claude Vision for the `alt_text` stage without having tested OpenDataLoader's built-in SmolVLM (256M-parameter) alt-text output against it, and set a revisit trigger: run that comparison for real once `AltTextService`/`ClaudeVisionClient` has a working implementation. Rather than build a one-off table just for that comparison, this ADR designs the tracking mechanism generally enough to reuse for any future pipeline stage's engine comparison (e.g. a future OCR-engine or tagging-engine bake-off) — the alt-text case is the first user of it, not the only one it's designed for.

SmolVLM's description is already computed as a byproduct of the `finalize_tags` stage when OpenDataLoader is run with `--enrich-picture-description` — it isn't a separate API call or a separate adapter, just an output we'd otherwise discard. `RemediationArtifact` isn't the right place to store any of this: that table records one row per pipeline stage per attempt (`step`, `status`, `output_uri`, `error`) for operational lineage, not per-item vendor-comparison payloads, and adding comparison fields to it would conflate a production audit trail with a research/bake-off mechanism.

## Decision

Add a general, permanent table, `EngineComparisonSample`, gated by a setting (`RUN_ENGINE_COMPARISON_LOGGING`, following the existing per-stage `RUN_*` kill-switch pattern) rather than always-on or built ad hoc per comparison:

- `remediation` (FK to `Remediation`)
- `step` (`RemediationArtifact.Step` choices) — which pipeline stage this comparison belongs to
- `service_1` — name of the first service/engine being compared
- `service_2` — name of the second service/engine being compared
- `description_1` — `service_1`'s output for the compared item (first use case: SmolVLM's description, captured from the `finalize_tags` stage's `--enrich-picture-description` output)
- `description_2` — `service_2`'s output for the same item (first use case: the `alt_text` stage's real `ClaudeVisionClient` output for the same figure)
- `item_reference` — whatever identifies the specific compared item within its stage (for alt-text, a figure's page number + bounding box, or an index into OpenDataLoader's picture list)
- `item_type` — a category label for stratified sampling (for alt-text: chart/graph, photo, diagram/flowchart, table-rendered-as-image, icon/simple graphic); initially blank, assigned during manual review
- `created_at`

First use: alt-text, per ADR 0005's revisit trigger. Sampling target for that run: roughly 30-40 records, stratified across `item_type` categories with at least 5-8 examples per category, pulled from real documents actually flowing through the pipeline (or a realistic staging traffic mirror) rather than synthetic test PDFs — the goal is coverage of the actual distribution of figure types this pipeline will see, not just a raw count. Future comparisons (a different stage, a different pair of engines) reuse this same table and set their own sampling target; this ADR doesn't fix a sample size for anyone but the alt-text case.

Once enough records exist for a representative sample, review happens manually (a person reading both outputs against whatever quality bar applies to that stage) — this ADR doesn't decide the review process itself, only how the data gets captured for it.

## Consequences

- `finalize_tags` needs `--enrich-picture-description` enabled while `RUN_ENGINE_COMPARISON_LOGGING` is on for the alt-text comparison specifically — it's not otherwise needed for that stage's actual job (tagging). Other stages' future engine comparisons will have their own equivalent one-off requirements to produce a second output worth comparing against.
- `EngineComparisonSample` and `RUN_ENGINE_COMPARISON_LOGGING` are permanent, not torn down after the alt-text comparison — the setting gets turned off once a given comparison run has enough records, then back on whenever a future engine swap needs its own bake-off.
- No behavior change to whichever stage is being compared: outputs are captured as a side effect of stages that already run; nothing about a stage's actual production output changes because comparison logging is on.
