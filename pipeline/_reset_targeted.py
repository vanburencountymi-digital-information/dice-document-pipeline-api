"""
Targeted reset: resets pipeline_pass_status to pending ONLY for documents
that had unembedded font, form tooltip, or missing bookmark issues.
All other completed docs are left untouched.
"""
import csv, shutil
from pathlib import Path

rem_dir  = Path("remediation_work/remediated")
log_dir  = Path("remediation_work/logs")
csv_file = Path("remediation_status.csv")

if not csv_file.exists():
    print("ERROR: remediation_status.csv not found.")
    raise SystemExit(1)

with open(csv_file, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)

reset_count = 0
for row in rows:
    items = row.get("manual_review_items", "").lower()
    if "font" in items or "form" in items or "tooltip" in items or "bookmark" in items or "contrast" in items or "heading" in items:
        # Remove the remediated PDF so it gets regenerated
        rem_pdf = rem_dir / row["filename"]
        if rem_pdf.exists():
            rem_pdf.unlink()

        # Remove the log file so it gets regenerated
        log_stem = Path(row["filename"]).stem
        log_file = log_dir / f"{log_stem}_remediation_log.txt"
        if log_file.exists():
            log_file.unlink()

        # Reset Pass 2 fields only
        row["pipeline_pass_status"] = "pending"
        row["alt_text_images"]      = "0"
        row["compliance_score"]     = ""
        row["compliance_grade"]     = ""
        row["manual_review_items"]  = ""
        row["log_path"]             = ""
        reset_count += 1

with open(csv_file, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

print(f"  Reset {reset_count} document(s) with font/form/bookmark/contrast issues.")
print(f"  All other {len(rows) - reset_count} completed documents left untouched.")
print()
print("  Run:  py -3 ada_remediate.py")
