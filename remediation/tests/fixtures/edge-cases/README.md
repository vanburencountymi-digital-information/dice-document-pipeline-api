# Edge-case fixtures

Small, fast-reproducing PDF slices for testing specific pipeline bugs — as opposed to
`../original/`'s full real-world documents. All of the bugs below are fixed in our forks
(not all are merged upstream yet); these fixtures now exist purely to catch a regression
(see `manage.py check_ocr_regression`), not to reproduce an active bug.

| Fixture | Bug it covers | Status |
|---|---|---|
| `pages7-9-benign-ocr-fallback.pdf` | OCR-fallback triage false positive (signature image) | Confirmed benign |
| `pages11-14-crash-ocr-fallback.pdf` | OCR-fallback `NullPointerException` during auto-tagging | Fixed (`opendataloader-pdf` branch `fix/ocr-fallback-font-cache-npe`) |
| `pages17-19-benign-ocr-fallback.pdf` | OCR-fallback triage false positive (seal/crest graphic) | Confirmed benign |
| `pages32-35-benign-ocr-fallback.pdf` | OCR-fallback triage false positive (checkbox-grid form variant) | Confirmed benign |
| `page9-broken-tounicode-offset29.pdf` | Missing `/ToUnicode`, constant +29 character-code offset | Fixed by `FontRepairService` |
| `page29-broken-tounicode-offsetneg1.pdf` | Missing `/ToUnicode`, constant -1 character-code offset | Fixed by `FontRepairService` |

## Upgrade-comparison slices (DIC-2405)

Not bug reproductions. All cut from the original 10-20-2026 BOC meeting packet that St. Joseph
County sent on DIC-2472 (`0. 10-20-2026 BOC Meeting Packet_.pdf`, 331 pages, untagged). The
packet itself is too big to commit; ask the team for the file if you need to cut more slices.
The first table is the pages where the production stack and the candidate upgrade (newer
docling + rebased opendataloader) gave different results in chunk 1 (pages 1-60). Page numbers
are from the full packet. They let a head-to-head run in minutes instead of hours.

| Fixture | What differed (today's stack vs candidate) |
|---|---|
| `boc1020-pages2-4-upgrade-diff.pdf` | Agenda columns tagged as a table vs plain text; title lines lose their headings; one list split in three |
| `boc1020-pages9-16-upgrade-diff.pdf` | Heading and list counts, some words, tag order |
| `boc1020-pages18-21-upgrade-diff.pdf` | Words and headings |
| `boc1020-pages24-27-upgrade-diff.pdf` | Page 24 is a scanned letter: no text before, readable after. Page 26 bold labels (Physical Address, etc.) lose their headings |
| `boc1020-pages38-42-upgrade-diff.pdf` | Words, headings, tag order |
| `boc1020-pages45-50-upgrade-diff.pdf` | Headings (one gained, others lost), tag order |
| `boc1020-pages52-57-upgrade-diff.pdf` | Words, headings, tag order |

The second table is pages picked from the source because they have something chunk 1 lacks.
We don't yet know whether the two stacks differ on them.

| Fixture | Why it was picked |
|---|---|
| `boc1020-pages251-253-forms.pdf` | Fillable form fields (the packet's only forms block); Form-children rule 7.18.4 |
| `boc1020-pages180-182-broken-text.pdf` | Type 3 fonts, garbled text, 12 images with fragmented text |
| `boc1020-pages110-111-scanned.pdf` | Two scanned pages with no text layer |
| `boc1020-pages245-247-scanned-dividers.pdf` | Scanned divider pages alternating with a text page |
| `boc1020-pages119-121-tables-no-tounicode.pdf` | Dense millage tables, fonts without a text mapping (ToUnicode) |
| `boc1020-pages86-88-stat-tables.pdf` | Number-heavy statistics tables; chunk 1 showed repeated table rows |
| `boc1020-pages272-274-caps-headings.pdf` | Plain resumes with ALL-CAPS headings; heading detection |
| `boc1020-pages163-165-image-cover.pdf` | Image-heavy cover page with almost no text |
| `boc1020-pages171-173-table-images-mix.pdf` | Cost table, personnel images and bio text together |
| `boc1020-pages324-326-form-like-appendix.pdf` | Form-like appendix layout with no actual form fields |

## Baselines and page readings

- **Baselines.** All 17 `boc1020-*` slices have a report in `../quality_baseline/`, recorded from
  the production stack (v5.10.0: docling fork 2.118.0, opendataloader-pdf 2.5.0). Always record
  baselines from production, never from the candidate, so a comparison shows what the upgrade
  changes. Time-per-step values vary between runs; the compare ignores them.
- **Page readings.** Four slices also have a transcription of a page, made by reading the page
  image and checked by a person, in `../ocr_truth/`. Page numbers count inside the slice.

  | Slice | Page tested | What it checks |
  |---|---|---|
  | `boc1020-pages2-4-upgrade-diff.pdf` | packet p4 | reading order (title block, label/value rows, 12-item list) |
  | `boc1020-pages24-27-upgrade-diff.pdf` | packet p24 | scanned letter: words |
  | `boc1020-pages45-50-upgrade-diff.pdf` | packet p46 | table: numbers (not order) |
  | `boc1020-pages110-111-scanned.pdf` | packet p110 | scanned infographic: words and numbers (not order) |

- **Running them.** `make check-pdf-quality` runs the real pipeline on every slice, scores the
  pages that have a reading, compares with the baselines and fails if anything got worse. It
  needs the hybrid container running. See "Upgrading the Document Pipeline" in the root README.

Add slices from the later chunks (2-6) as those runs finish.
