@echo off
title Reset Pass 2 Only — Van Buren County ADA Pipeline
cd /d "%~dp0"

echo.
echo ══════════════════════════════════════════════════════════════
echo   RESET PASS 2 ONLY  —  ADA Remediation Pipeline
echo ══════════════════════════════════════════════════════════════
echo.
echo   This resets only the Python remediation pass (Pass 2).
echo   Acrobat pass results (OCR, autotag) are PRESERVED so you
echo   do not have to re-run the expensive Acrobat automation.
echo.
echo   What gets reset:
echo     - Clears:  remediation_work\remediated\
echo     - Clears:  remediation_work\logs\
echo     - Resets:  pipeline_pass_status = pending  (in CSV)
echo     - Resets:  alt_text_images, compliance_score, compliance_grade,
echo                manual_review_items, log_path
echo.
echo   What is KEPT:
echo     - remediation_work\downloads\  (source PDFs)
echo     - acrobat_pass_status column   (OCR/autotag results)
echo     - ocr_applied, autotag_applied columns
echo.
echo   Press any key to continue, or Ctrl+C to cancel.
pause > nul

echo.
echo   Resetting...
echo.

py -3.11 _reset_pipeline.py

echo.
echo ══════════════════════════════════════════════════════════════
echo   Ready for Pass 2.
echo   Run RUN_PIPELINE.bat to re-run Pass 2 only.
echo   (Pass 1 Acrobat automation will be skipped automatically
echo    since acrobat_pass_status is still marked complete.)
echo ══════════════════════════════════════════════════════════════
echo.
pause
