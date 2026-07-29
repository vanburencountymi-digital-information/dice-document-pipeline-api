# 3. Remediation pipeline: step sequence, branching, and service shape

## Status

Accepted

## Context

The `process_remediation` job is currently a placeholder, and we are ready to define the pipeline architecture moving forward.

Initially, our hope was to use base interface classes that would then call adapters to avoid lock-in with a specific service.

So, for example, a base class of OCRService everywhere it's called in a view or service class, and then an adapter or client class used in instantiation (DoclingOCRService).

However, investigation into the best possible automated tagging solutions revealed that OpenDataLoader--our frontrunner for tagging--cannot split OCR and tagging calls easily (although it can be done, depending on how the server is instantiated); additionally, given its strong performance in benchmarks, it is likely the preferred solution for a number of different functions.

Another decision tree involved alt-text; although OpenDataLoader's OCR service *does* supply alt-text, it uses an ultra-lightweight model that likely is not able to tag images to a sufficient enough standard to meet WCAG guidelines.

## Options Considered
1. Move forward with pinning OpenDataLoader as a dependency, which will tightly couple our codebase to the product, which would result in a larger lift if we ever wanted to move forward with a different OCR or tagging service.
2. Move forward with separate interface classes, essentially skipping unused classes with a no-op.

## Decision

Proceed with Java + OpenDataLoader as a real, pinned system dependency, with hybrid mode(--hybrid docling-fast)

### Steps and Service Shape

Steps should each be independently skippable and failable, and a failed step marks the whole job as failed.

The original source URI is never changed to preserve content hash, but each step produces an artifact that writes a new URI and passes it forward as the next steps input.

1. `PrecheckService()` - calls VeraPDFAdapter on the original upload. Compliant → `mark_complete`, done.
2. `OCRService()` - calls OpenDataLoaderAdapter, runs OCR in hybrid mode and tags the file.
3. `TaggingService.finalize_tags()` - fixup on step 2's output that calls PikePDFAdapter, supplies mandatory `MarkInfo`/`Lang`/title/tab-order.
4. `AltTextService()` - calls ClaudeVisionAdapter(), should be skipped if no `/StructTreeRoot` or no untagged non-decorative figures.
5. `LinkService()` - call PikePdfAdapter to provide struct-tree linking for `/Link` annotations.
6. `PostCheckService()` - recall veraPDF on  step 5's output. Compliant → `mark_complete`. If fails, mark the remediation job as failed.

## Consequences
- In the event we want to move away from OpenDataLoader, we will have to build new service classes into the pipeline.
- Hybrid mode requires the `opendataloader-pdf-hybrid` server running alongside the main CLI — an operational dependency beyond just installing the pip package + Java that needs accounting for in local dev setup and the Cloud Run image/process model.
- `--ocr-engine` choice is still open and needs to be made.
- `Remediation` now needs a per-step outcome record — a `RemediationArtifact`-shaped table (`remediation`, `step`, `status`, `output_uri`, `error`) instead of one flat `output_uri` field.
