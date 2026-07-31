# 10. Split tag construction into its own `build_tags` step

## Status

Accepted. Amends [ADR 0003](0003-pipeline-steps-and-branching.md)'s step sequence (six steps become seven) and [ADR 0004](0004-ocr-tagging-engine.md)'s "one tagging-stage call stays the shape" reasoning (OpenDataLoader is still pinned for OCR/extraction — see that ADR — just no longer for PDF construction).

## Context

ADR 0004 assumed OpenDataLoader's hybrid mode could OCR a document and produce a `tagged-pdf` output in one call, since its tagging pass always runs its own internal extraction/OCR first. That assumption turned out to be wrong: `hybrid` + `format=tagged-pdf` (and plain `format=pdf`) silently drop OCR-recovered content instead of writing it into the output. Confirmed as a genuine bug in the bundled Java CLI itself, not a config or Docker issue — reproduced by invoking the CLI jar directly against the hybrid server, no Python wrapper or app code involved. `format=json` on the identical run, by contrast, returns the same underlying document model fully and correctly populated, including real recovered text and correct PDF/UA tag roles (`P`, `H1`, `Figure`). So the document model is right; only OpenDataLoader's own PDF-writing step is broken.

v1 (`dice-document-pipeline` repo) doesn't have a working precedent for this either — its own "Tagged PDF" pipeline calls OpenDataLoader in pure native mode on the original source PDF, with hybrid OCR feeding an entirely separate, non-PDF product. It never combined the two, so there's no existing solution to reuse here, just its pikepdf `finalize_tags`-equivalent fixes, which remain valid and unaffected by this decision.

## Options Considered

1. Wait for OpenDataLoader to fix this upstream — no control over timeline, and blocks the `ocr` step's original design entirely in the meantime.
2. Decouple OCR from tagging using a different OCR tool entirely (e.g. `OCRmyPDF`/Tesseract) feeding OpenDataLoader's native mode — avoids the bug, but downgrades OCR/layout quality from Docling/EasyOCR (the AI backend) to Tesseract, in exactly the dimension this pipeline chose hybrid mode for in the first place.
3. Keep OpenDataLoader's hybrid engine for OCR + layout analysis (still the best available FOSS option — no credible FOSS alternative surfaced in a broader engine survey; commercial alternatives are out of scope), take its `format=json` output (proven complete and correctly structured), and build the actual tagged PDF ourselves from that JSON. If chosen, which library actually builds the PDF is its own decision:
    1. `pikepdf` — no high-level struct-tree API; would mean hand-constructing raw PDF dictionaries (`StructTreeRoot`, marked-content sequences, parent trees) by hand.
    2. `fpdf2` — has some struct-tree support, but its own docs mark that module internal/unstable, not something to build compliance-critical output on.
    3. `iText7` — mature, documented PDF/UA-tagging support, but AGPL-or-commercial licensed; this repo has no `LICENSE` file, so there's no existing open-source commitment to check that against.
    4. `Apache PDFBox` — Apache 2.0, no copyleft question at all, and the same library OpenDataLoader itself is built on (see [ADR 0007](0007-java-version.md)) — already proven capable of producing valid, veraPDF-passing tagged output, since that's what it's doing under the hood every time OpenDataLoader's native mode succeeds.

## Decision

Option 3, with Apache PDFBox (option 3.4) as the library. Keep OpenDataLoader's hybrid engine doing 100% of the OCR and layout-detection work — still the best available tool for that job — but stop asking it for `tagged-pdf`/`pdf` output, which is broken. Take its `format=json` output instead and construct the actual tagged PDF ourselves via a new Java utility built on Apache PDFBox, called through a new `TaggedPdfWriterAdapter`.

This becomes a new pipeline step, `build_tags`, inserted between `ocr` and the renamed `finalize_metadata` step (see below):

`precheck` → `ocr` (extraction only now — via `OpenDataLoaderAdapter.extract()`, `format=json`) → `build_tags` (constructs the real `StructTreeRoot`/marked-content structure via `TaggedPdfWriterAdapter`) → `finalize_metadata` (patches `MarkInfo`/`Lang`/title/tab-order gaps left by `build_tags`, same four fixes v1 already validated) → `alt_text` → `link_tag` → `postcheck`.

`finalize_tags` (ADR 0003's original name for this step, backed by `TaggingService.finalize_tags` in the plan) is renamed to `finalize_metadata`, backed by `MetadataService`, as part of this same decision: none of its four fixes touch tag structure at all (`Lang` and title are document metadata, `/Tabs` is page navigation, and `MarkInfo` is a boolean flag *about* tagging status, not tag content) — "tags" was never an accurate name for this step, it's just more visible now that `build_tags` exists right next to it doing the thing "tags" actually implied.

Not folded into the `ocr` step as a second sub-action of `OCRService`, even though that would avoid a schema migration: OCR/extraction and PDF construction are different techniques (reading structured data out of a document vs. writing a new document from structured data), and `OCRService` naming it all "OCR" would be exactly as inaccurate as the original single-call design this ADR is replacing. A dedicated `TagBuilderService` (matching the `-Service` naming convention: named for its outcome, not the adapter it calls, same relationship `OCRService` has to `OpenDataLoaderAdapter`) owns the new step.

A separate step with its own `RUN_BUILD_TAGS` kill switch also gives a clean, low-regret exit path: if OpenDataLoader ever ships a working `hybrid` + `format=tagged-pdf` combination upstream, retiring this workaround is a config flip (`RUN_BUILD_TAGS=False`, point `OCRService` back at `format=tagged-pdf` directly) rather than untangling a workaround that had been merged into `OCRService` itself. Per-step kill switches were already this repo's standing convention (see `implementation_plan.md`'s Decisions) specifically so a step's code can exist without being permanently load-bearing — this is that same property applied to an upstream-bug workaround, not just a not-yet-built feature.

A proof-of-concept validated this is viable before committing further: a hand-built PDFBox program producing a minimal tagged PDF (heading + paragraph, real embedded/subsetted font, `StructTreeRoot`, marked-content-linked structure elements, `MarkInfo`, `ViewerPreferences/DisplayDocTitle`, XMP metadata with the PDF/UA identification schema) passed `verapdf --flavour ua1` with zero failures.

## Consequences

- New `RemediationArtifact.step` choice (`build_tags`), a migration, and a new `RUN_BUILD_TAGS` setting (off by default, per the existing per-step kill-switch convention).
- `OpenDataLoaderAdapter.tag()` is renamed to `extract()` and returns a JSON path instead of a tagged-PDF path — it no longer tags anything itself, per this decision.
- New adapter, `TaggedPdfWriterAdapter`, wrapping a new custom Java utility (Apache PDFBox) — not a third-party CLI like `VeraPDFAdapter`/`OpenDataLoaderAdapter` wrap, but built and maintained in this repo, so it needs its own build step in the main `Dockerfile` (compiling/bundling the jar) rather than just a `pip install`.
- The new Java utility only needs to handle the element types actually present in OpenDataLoader's JSON output (paragraphs, headings with levels, list items, images/figures) — not arbitrary PDF content in general, since its only input is that JSON schema.
- Font fidelity to the original document is not preserved — the JSON schema doesn't carry embeddable font resources, only text/position/size, so the new writer embeds a single bundled font (DejaVu Sans) for all recovered text. Acceptable: this only affects content that needed OCR recovery in the first place (i.e., content with no usable original font to preserve anyway), and text accuracy/structure matters more than font fidelity for the accessibility purpose this pipeline serves.
- `finalize_tags`/`TaggingService` (ADR 0003's names) become `finalize_metadata`/`MetadataService` — another `RemediationArtifact.step` rename, migration, and `RUN_*` setting rename (`RUN_FINALIZE_TAGS` → `RUN_FINALIZE_METADATA`), alongside `build_tags`'s new ones. Low-risk to rename now specifically because this step isn't built yet and `RUN_FINALIZE_TAGS` defaults off — no real data exists under the old enum value.
