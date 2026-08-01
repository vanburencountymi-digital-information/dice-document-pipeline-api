# 10. Fix OpenDataLoader's hybrid+tagged-pdf bug upstream

## Status

Accepted. Replaces this ADR's original decision in full.

## Context

`opendataloader-pdf`'s hybrid mode (calls out to a Docling backend to OCR-recover text from scanned/image regions it can't natively extract) has a confirmed bug: `format=tagged-pdf`/`pdf` silently drops OCR-recovered text from the output, while `format=json` on the same run returns it fully and correctly (real recovered text, correct PDF/UA tag roles `P`/`H1`/`Figure`). The underlying document model is right; only the PDF-writing step is broken.

Whatever fix is chosen has to produce a tagged PDF that's visually identical to the source document, not just structurally accessible; that's a hard requirement. Any approach that reconstructs a PDF from OpenDataLoader's JSON output can't guarantee that: that JSON schema only carries text/position/font-size, not fonts, colors, backgrounds, or vector art, so anything built from it alone is structurally accessible but visually a different document.

## Options Considered

1. **Pre-OCR with `ocrmypdf` (Tesseract), then run OpenDataLoader in native mode.** Sidesteps the bug entirely — native mode's tagged-pdf writer is the not-broken code path, and by the time OpenDataLoader sees the file, `ocrmypdf`'s invisible text layer is indistinguishable from native text. Rejected as the primary path: Tesseract benchmarks 7-10 points below EasyOCR on non-clean scans (a real regression risk, unverified on our actual failing documents), and this would give up Docling's ML-based layout analysis (heading/paragraph/list/figure classification) for the OCR-recovered portion of every document.
2. **Build a downstream tool that reconstructs tagged PDFs from OpenDataLoader's JSON output**, doing content-stream surgery to preserve visual fidelity (locate existing operators for natively-extracted content and wrap them, add invisible text only for genuinely OCR-only content) rather than redrawing everything from scratch. Would keep hybrid mode's OCR+layout quality, but requires reimplementing operator-correlation logic OpenDataLoader already has internally, and means permanently maintaining a downstream patch tool for the life of this project.
3. **Fork `opendataloader-pdf` (Apache 2.0, `opendataloader-project/opendataloader-pdf`, healthy/active/Hancom-backed), fix the bug directly, pin our Docker build to the patched fork, contribute the fix upstream, and revert to the normal PyPI release once merged.**

Source analysis of the real `opendataloader-pdf` codebase confirmed why the bug happens and that option 3 is tractable:
- The tagged-pdf writer (`AutoTaggingProcessor`) never draws new content from scratch. `ChunksWriter.getTokens()` re-parses each page's actual content-stream bytes into a token list, and only wraps a content-drawing operator in `BDC`/`EMC` marked-content tags when a `StreamInfo` entry exists whose `operatorIndex` matches that operator's position in the freshly re-parsed stream. Native mode and hybrid mode converge on this exact same writer; native mode is a proven, working code path, not a separate unverified implementation.
- `HybridDocumentProcessor.enrichSingleTextNode()` is the exact drop point: when no Java-extracted text chunk overlaps a backend/OCR text node's bounding box, it records the node as `"ocr-fallback"` and moves on — leaving that node's `StreamInfo` list permanently empty. With no `StreamInfo`, the writer has nothing to wrap, and the content is silently dropped. This is a genuine coverage gap, not a deliberate exclusion.
- The maintainers have already scoped this exact fix: a doc comment on the logging step that currently just records these fallback candidates states verbatim that a later phase will "actually insert invisible text operators into the content stream for these elements." The fix this ADR describes is that already-planned next phase, not a speculative patch.
- Direct upstream precedent exists for the same fix *shape* applied to a different content type: issue/PR #546 fixed the identical class of problem for images — when the hybrid backend detects a figure with no matching native image chunk, a synthetic chunk is built anchored to a matching operator, routed through the normal write path.
- `opendataloader-pdf` is Apache 2.0, healthy and actively maintained (Hancom-backed, daily commits, responsive maintainers, real contribution process) — no licensing concern (unlike AGPL, already a rejection reason for iText7 and a consideration for `ocrmypdf`'s Ghostscript dependency), and forking/patching is mechanically cheap: a plain Maven build producing a shaded jar.

## Decision

Option 3. Fork `opendataloader-pdf`, fix the hybrid-mode text-drop bug in `HybridDocumentProcessor` (synthesizing invisible-text content-stream operators and matching `StreamInfo` entries for OCR-only content, so the existing `ChunksWriter`/`AutoTaggingProcessor` machinery picks them up with no changes to those classes), build our own patched jar, and override the bundled jar `opendataloader_pdf`'s Python wrapper invokes with it in our Docker build. Contribute the fix back upstream as a PR; once merged and released, drop the fork and go back to a plain `pip install opendataloader-pdf==<version>`.

`OpenDataLoaderAdapter.extract()` goes back to calling `hybrid="docling-fast"`, `format="tagged-pdf"` directly and returns the final tagged PDF in one call — the single-call design ADR 0004 originally assumed, before this bug was discovered.

## Consequences

- Rename `finalize_tags`/`TaggingService` to `finalize_metadata`/`MetadataService`, since none of that step's four pikepdf fixes (`MarkInfo`/`Lang`/title/tab-order) actually touch tag structure — "tags" was never an accurate name for it.
- New temporary maintenance cost: our Docker build now depends on a forked/patched `opendataloader-pdf` jar instead of the plain PyPI release, until our fix is merged upstream. This needs periodic rebasing against upstream's (frequent) commits, and should be tracked as a live TODO until we can revert to the stock package.
- Font fidelity, background/vector-art preservation, and exact visual layout are all preserved automatically, since native mode tags the existing content stream in place rather than reconstructing one. Purely-scanned pages with no embedded font resources at all need a bundled fallback font to reference for the synthesized invisible text.
