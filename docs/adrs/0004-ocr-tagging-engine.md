# 4. OCR/tagging engine: OpenDataLoader as a pinned dependency

## Status

Accepted

## Context

Initially, our hope was to use base interface classes that would then call adapters to avoid lock-in with a specific service — a generic `OCRService` interface everywhere it's called in a view or service class, with a concrete adapter (e.g. `DoclingOCRService`) supplied at instantiation.

Investigation into the best available automated-tagging solutions found that OpenDataLoader — our frontrunner for tagging — cannot cleanly split OCR and tagging into two independent calls: its tagging step always runs its own internal extraction/OCR pass first. It also benchmarks strongly enough, across more than just tagging, that it's likely to end up backing multiple pipeline stages, not just one. Enforcing a generic OCR/tagging interface split would mean fighting how the tool actually works, for no real independence benefit today.

Alt-text was a related but separate question: OpenDataLoader's own pipeline does supply alt text, but via an ultra-lightweight model unlikely to meet WCAG-standard image descriptions — so alt-text generation stays its own separate, swappable stage regardless of this decision (see [ADR 0003](0003-pipeline-steps-and-branching.md)).

## Options Considered

1. Pin OpenDataLoader as a real, direct dependency — couples the codebase to it; a future engine swap means rewriting whichever service classes it backs.
2. Keep generic interface classes for OCR/tagging with adapters underneath, treating OpenDataLoader as just one adapter behind a swappable interface, mirroring the original plan.

## Decision

Pin Java + OpenDataLoader (`opendataloader-pdf`, hybrid mode via `--hybrid docling-fast`) as a real, named dependency rather than hiding it behind a generic interface. The architectural boundary moves from "OCR provider" to "document-processing pipeline stage": because OCR and tagging are tightly integrated inside OpenDataLoader, separating them behind independent interfaces would add abstraction without buying real flexibility.

## Consequences

- Moving away from OpenDataLoader later means writing new service classes for whichever stage(s) it currently backs, not swapping out an adapter underneath a stable interface. This is the ADR that gets superseded if that happens — the pipeline architecture in [ADR 0003](0003-pipeline-steps-and-branching.md) doesn't need to change alongside it.
- Hybrid mode requires the `opendataloader-pdf-hybrid` server running alongside the main CLI — an operational dependency beyond installing the pip package + Java, which needs accounting for in local dev setup and the Cloud Run image/process model.
- Not an absolute wall between OCR and tagging, though: Docling's OCR model only fills in bitmap-covered regions lacking usable text and drops any OCR-generated cell that overlaps existing programmatic text, and the hybrid server also exposes a `disable_ocr`/`--no-ocr` startup flag specifically for "input already has a reliable text layer" — so an externally-OCR'd (or born-digital) PDF handed to the tagging stage won't get double-OCR'd. That's a real seam (bring-your-own-OCR-engine upstream is technically possible), just not one being built on now — one tagging-stage call stays the shape.
- `--ocr-engine` choice within hybrid mode is still open — see `implementation_plan.md` Backlog.
- Alt-text generation is out of scope for this decision; it's a separate stage/adapter regardless of which engine backs OCR/tagging.
