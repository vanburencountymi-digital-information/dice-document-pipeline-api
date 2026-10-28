# Edge-case fixtures

Small, fast-reproducing PDF slices for testing specific pipeline bugs — as opposed to
`../original/`'s full real-world documents. All of the bugs below are confirmed fixed
upstream; these fixtures now exist purely to catch a regression (see
`manage.py check_ocr_regression`), not to reproduce an active bug.

| Fixture | Bug it covers | Status |
|---|---|---|
| `pages7-9-benign-ocr-fallback.pdf` | OCR-fallback triage false positive (signature image) | Confirmed benign |
| `pages11-14-crash-ocr-fallback.pdf` | OCR-fallback `NullPointerException` during auto-tagging | Fixed (`opendataloader-pdf` branch `fix/ocr-fallback-font-cache-npe`) |
| `pages17-19-benign-ocr-fallback.pdf` | OCR-fallback triage false positive (seal/crest graphic) | Confirmed benign |
| `pages26-29-benign-ocr-fallback.pdf` | OCR-fallback triage false positive (checkbox-grid form) | Confirmed benign |
| `pages32-35-benign-ocr-fallback.pdf` | OCR-fallback triage false positive (checkbox-grid form variant) | Confirmed benign |
| `page9-broken-tounicode-offset29.pdf` | Missing `/ToUnicode`, constant +29 character-code offset | Fixed by `FontRepairService` |
| `page29-broken-tounicode-offsetneg1.pdf` | Missing `/ToUnicode`, constant -1 character-code offset | Fixed by `FontRepairService` |

## Upgrade-comparison slices (DIC-2405)

Not bug reproductions. All cut from the original 10-20-2026 BOC meeting packet that St. Joseph
County sent on DIC-2472 (`0. 10-20-2026 BOC Meeting Packet_.pdf`, 331 pages, untagged). The first
table is the pages where today's stack and the candidate upgrade (newer docling + rebased opendataloader) gave
different results in chunk 1 (pages 1-60). Page numbers are from the full packet. They let a
head-to-head run in minutes instead of hours.

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
Candidates: not yet run on both stacks, so we don't know they differ.

| Fixture | Why it was picked |
|---|---|
| `boc1020-pages251-253-forms.pdf` | Fillable form fields (the packet's only forms block); Form-children rule 7.18.4 |
| `boc1020-pages180-182-broken-text.pdf` | Type 3 fonts, garbled text, 12 images with fragmented text |
| `boc1020-pages110-112-scanned.pdf` | Three scanned pages with no text layer |
| `boc1020-pages245-247-scanned-dividers.pdf` | Scanned divider pages alternating with a text page |
| `boc1020-pages119-121-tables-no-tounicode.pdf` | Dense millage tables, fonts without a text mapping (ToUnicode) |
| `boc1020-pages86-88-stat-tables.pdf` | Number-heavy statistics tables; chunk 1 showed repeated table rows |
| `boc1020-pages272-274-caps-headings.pdf` | Plain resumes with ALL-CAPS headings; heading detection |
| `boc1020-pages163-165-image-cover.pdf` | Image-heavy cover page with almost no text |
| `boc1020-pages171-173-table-images-mix.pdf` | Cost table, personnel images and bio text together |
| `boc1020-pages324-326-form-like-appendix.pdf` | Form-like appendix layout with no actual form fields |

No baselines are recorded for these yet. Record them from today's production stack, not the
candidate, so the report shows what the upgrade changes. Add slices from the later chunks as
those runs finish.
