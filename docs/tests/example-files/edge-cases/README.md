# Edge-case fixtures

Small, fast-reproducing PDF slices for testing specific pipeline bugs — as opposed to
`../original/`'s full real-world documents, which can take many minutes to run through the OCR
pipeline end to end.

## Current fixtures: OCR-fallback font-resolution crash

Source: a real 47-page submission ("0. 08-18-26 BOC Meeting Packet.pdf" — a compiled packet of
unrelated documents) that crashed the whole remediation pipeline with a `NullPointerException`
during auto-tagging. 14 pages were flagged during triage as needing OCR fallback with "0 OCR
words available." Only one pair of pages (12, 13) actually triggers a real bug.

The other 12 flagged pages are genuine false positives — and visually inspecting all of them
showed they're really just **3 recurring content categories**, not 12 distinct cases: signature
images, scanned letters with a seal/crest graphic, and dense checkbox-grid retirement-plan forms
(the same two MERS forms, recurring across 5 separate page clusters). Kept one fixture per
category below rather than all 9 originally-tested clusters — the dropped ones
(`pages19-21`/`pages22-24`/`pages24-26`, all the same MERS form as `pages26-29`/`pages32-35`; and
`pages43-47`, a redundant combination of the signature and seal-graphic patterns already covered
separately) added no new coverage.

Each fixture is a cluster of adjacent flagged pages, bracketed with a 1-page buffer on either
side. Regenerate them with `generate_edge_cases.py` (needs `pikepdf`, run from the repo root via
the project `.venv`) — it also documents the dropped clusters, so re-adding one back is a
one-line change if it's ever needed again.

| Fixture | Content | Verdict |
|---|---|---|
| `pages7-9-benign-ocr-fallback.pdf` | Signature image (page 8) | Benign — nothing to OCR, correct behavior |
| `pages11-14-crash-ocr-fallback.pdf` | CV page + browser-screenshot page (pages 12-13) | **Crashes** — the actual bug, see below |
| `pages17-19-benign-ocr-fallback.pdf` | Scanned MI state license letter with a seal graphic (page 18) | Benign |
| `pages26-29-benign-ocr-fallback.pdf` | MERS retirement-plan checkbox-grid forms (pages 27-28) | Benign |
| `pages32-35-benign-ocr-fallback.pdf` | Same MERS form pattern, ends in a blank/unsigned signature block (pages 33-34) | Benign |

## Second bug: font ToUnicode-offset defect (separate from the crash above)

The real document also fails `postcheck` (PDF/UA-1) for a second, unrelated reason: certain
embedded fonts have no `/ToUnicode` CMap and no internal `cmap` table, but their character codes
turn out to be shifted from true Unicode by a **constant, per-font offset** — confirmed by hand on
two fonts in this document. See the font-repair plan for the full root-cause writeup and fix
design. Unlike the OCR-fallback fixtures above, these are single-page fixtures with **no buffer
needed** — the defect is a static property of the font object itself, not document-context
sensitive.

| Fixture | Font / offset | Verified |
|---|---|---|
| `page9-broken-tounicode-offset29.pdf` | `LONENE+Arial-BoldMT`, offset **+29** | veraPDF: rule 7.21.7 (44 checks) + 7.21.4.2 (1 check) fail on this single page |
| `page29-broken-tounicode-offsetneg1.pdf` | `CANKNL+HelveticaNeueLTStd-Lt`, offset **-1** | veraPDF: rule 7.21.7 (27 checks) fails on this single page |

**The bug** (fixed — see `~/opendataloader-pdf` branch `fix/ocr-fallback-font-cache-npe`):
`HybridDocumentProcessor.ensureOcrFallbackFont` checked whether its synthesized OCR-fallback font
was already present via `PDResources#getFont(...) != null` *before* adding it — that premature
query poisoned `PDResources`'s internal font-resolution cache, so the font was never resolvable
later at tagging time even though it had genuinely been added to the underlying dictionary. Fixed
by checking the raw COS `/Font` dictionary directly instead of going through `PDResources#getFont`
before the entry exists. Full root-cause writeup:
`~/.claude/plans/fully-investigate-this-bug-woolly-stardust.md`.

## Fast-iteration testing method (discovered this session)

A full 47-page real document can take **~23 minutes** to run through the pipeline (mostly
backend OCR). Don't wait on that while debugging — use these tiers instead, fastest first:

1. **Tier 1 (near-instant)**: run the CLI with **no `--hybrid` flag** at all — pure native
   tagging, no docling network round-trip. If a bug is in the tagging phase rather than
   OCR/hybrid-specific, this reproduces it in well under a second.
   ```
   java -jar opendataloader-pdf-cli.jar <file>.pdf --output-dir <tmp> --format tagged-pdf
   ```
2. **Tier 2 (~1 min/page)**: force every page through the backend regardless of triage, using the
   real `--hybrid-mode full` flag (*"skip triage, all pages to backend"*):
   ```
   java -jar opendataloader-pdf-cli.jar <file>.pdf --output-dir <tmp> --format tagged-pdf \
     --hybrid docling-fast --hybrid-mode full --hybrid-url http://opendataloader-hybrid:5002
   ```
3. **Tier 3 (still seconds, but important)**: if a single page — isolated, even with
   `--hybrid-mode full` forced — doesn't reproduce a hybrid-fallback-shaped bug, **try a small
   (2-4 page) contiguous slice instead**. Confirmed empirically this session: isolating page 12
   alone (the actual crashing page) does *not* reproduce the bug, with or without forced
   full-hybrid routing — the underlying OCR-recall/triage behavior is sensitive to document-wide
   context in ways that don't survive single-page isolation. A 2-page slice (pages 12+13
   together) reproduces it reliably every time. This is *why* every fixture above keeps its
   1-page buffer rather than being trimmed down further.

The running `dice-document-pipeline-api-app-1` container already has a JDK 21 + the CLI jar
baked in — no separate container needed for this tier of testing:
```
docker exec dice-document-pipeline-api-app-1 java -jar \
  /usr/local/lib/python3.12/site-packages/opendataloader_pdf/jar/opendataloader-pdf-cli.jar ...
```

For changes to the Java source itself (not just black-box CLI testing), a throwaway
Maven+JDK container works well and doesn't touch the host repo:
```
docker run -d --name odl-debug-build --network dice-document-pipeline-api_default \
  -v ~/opendataloader-pdf:/host-repo:ro maven:3.9-eclipse-temurin-21 sleep infinity
docker exec odl-debug-build git config --global --add safe.directory '*'
docker exec odl-debug-build git clone /host-repo /work
```
(`--network dice-document-pipeline-api_default` is required for the container to reach the
`opendataloader-hybrid` service by name.) Clone into the container's own writable layer, not a
bind mount of the host repo, so edits during debugging never touch the real working tree.

**A note on backend load**: the `opendataloader-hybrid` container has no memory limit override
in `docker-compose.yml` and got OOM-killed once this session after many large sequential
requests. If a request fails fast with "Hybrid server is not available" rather than after the
usual ~1-3 minutes, check `docker ps -a` for an `Exited (137)` status before assuming it's a code
bug — restart with `docker compose up -d opendataloader-hybrid` and retry.
