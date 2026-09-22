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
