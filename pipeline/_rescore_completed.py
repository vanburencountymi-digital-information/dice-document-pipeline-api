"""
_rescore_completed.py
─────────────────────
Re-scores all pipeline_pass_status=complete documents using the current
scoring logic in ada_remediate.py, without re-running any remediation.

Steps per document:
  1. Locate the remediated PDF in remediation_work/remediated/
  2. Re-run assess_document() to get a fresh issues dict
  3. Get page count
  4. Re-run score_document() with the updated thresholds
  5. Rewrite the compliance score/grade/manual_review_items in the CSV
  6. Patch the score/grade line in the existing log file

Run from the folder that contains remediation_status.csv.
"""

import csv, io, os, sys
from pathlib import Path

# ── Locate ada_remediate next to this script ──────────────────────────────────
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ada_remediate import assess_document, score_document   # type: ignore

# ── Paths ──────────────────────────────────────────────────────────────────────
CSV_PATH  = HERE / "remediation_status.csv"
REM_DIR   = HERE / "remediation_work" / "remediated"
LOGS_DIR  = HERE / "remediation_work" / "logs"

# ── Read CSV (NUL-safe) ────────────────────────────────────────────────────────
raw = CSV_PATH.read_bytes().replace(b"\x00", b"")
text = raw.decode("utf-8", errors="replace")
reader = csv.DictReader(io.StringIO(text))
fieldnames = reader.fieldnames
rows = list(reader)

completed = [r for r in rows if r.get("pipeline_pass_status") == "complete"]
print(f"Re-scoring {len(completed)} completed document(s)...\n")

changes: list[tuple[str, str, str, str, str]] = []   # fname, old_score, new_score, old_grade, new_grade

for r in completed:
    fname    = r["filename"]
    rem_path = REM_DIR / fname

    if not rem_path.exists():
        print(f"  ⚠ SKIP  {fname} — remediated file not found")
        continue

    # ── Re-assess ──────────────────────────────────────────────────────────────
    try:
        issues = assess_document(rem_path)
    except Exception as e:
        print(f"  ⚠ SKIP  {fname} — assess_document failed: {e}")
        continue

    # Page count from pikepdf (same logic the main pipeline uses)
    try:
        import pikepdf
        with pikepdf.open(str(rem_path)) as pdf:
            pc = len(pdf.pages)
    except Exception:
        pc = 1

    # Preserve contrast_check_passed from prior run if logged
    # (we don't re-run Claude Vision here — just keep whatever was stored)
    # Parse it from the existing log if available
    log_path = LOGS_DIR / f"{rem_path.stem}_remediation_log.txt"
    contrast_passed = False
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if "[PASS    ] Color contrast" in log_text:
            contrast_passed = True
    issues["contrast_check_passed"] = contrast_passed

    # ── Re-score ───────────────────────────────────────────────────────────────
    new_score, new_grade, new_manual = score_document(issues, pc)

    old_score = r.get("compliance_score", "?")
    old_grade = r.get("compliance_grade", "?")

    # ── Update row in memory ───────────────────────────────────────────────────
    r["compliance_score"]    = str(new_score)
    r["compliance_grade"]    = new_grade
    r["manual_review_items"] = " | ".join(new_manual)

    changed = (old_score != str(new_score) or old_grade != new_grade)
    marker  = "↑" if changed else "="
    print(f"  {marker} {fname}")
    if changed:
        print(f"      {old_score} {old_grade}  →  {new_score} {new_grade}")
        changes.append((fname, old_score, str(new_score), old_grade, new_grade))

    # ── Patch the log file score line ─────────────────────────────────────────
    if log_path.exists() and changed:
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            new_lines = []
            for line in lines:
                if line.startswith("Compliance Score:") or line.startswith("Compliance Grade:"):
                    if "Score:" in line:
                        new_lines.append(f"Compliance Score: {new_score}/100\n")
                    else:
                        new_lines.append(f"Compliance Grade: {new_grade}\n")
                else:
                    new_lines.append(line)
            log_path.write_text("".join(new_lines), encoding="utf-8")
        except Exception as e:
            print(f"      ⚠ could not patch log: {e}")

# ── Write updated CSV ──────────────────────────────────────────────────────────
out = io.StringIO()
writer = csv.DictWriter(out, fieldnames=fieldnames)
writer.writeheader()
writer.writerows(rows)
CSV_PATH.write_text(out.getvalue(), encoding="utf-8")

# ── Summary ────────────────────────────────────────────────────────────────────
print(f"\n{'═'*60}")
print(f"  Re-scored : {len(completed)} documents")
print(f"  Changed   : {len(changes)}")
if changes:
    grade_counts: dict[str, int] = {}
    for _, _, _, _, ng in changes:
        grade_counts[ng] = grade_counts.get(ng, 0) + 1
    print(f"  New grades: {dict(sorted(grade_counts.items()))}")
print(f"{'═'*60}")
print("  CSV and logs updated. No remediation was re-run.")
