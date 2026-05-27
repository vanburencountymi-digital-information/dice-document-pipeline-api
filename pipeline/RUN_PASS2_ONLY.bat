@echo off
title ADA Remediation Pipeline - Pass 2 Only
cd /d "%~dp0"

echo.
echo ============================================================
echo   ADA REMEDIATION PIPELINE  -  Pass 2 Only
echo   Van Buren County DICE Department
echo ============================================================
echo.
echo   Runs ada_remediate.py ONLY (skips Acrobat Pass 1).
echo   Use this when Pass 1 has already been completed.
echo.
echo   Eligible documents: acrobat_pass_status = complete
echo                        pipeline_pass_status = pending
echo.
echo   Press any key to begin, or Ctrl+C to cancel.
pause > nul

echo.
py -3 ada_remediate.py

echo.
echo ============================================================
echo   PASS 2 COMPLETE
echo   Remediated files : remediation_work\remediated\
echo   Compliance logs  : remediation_work\logs\
echo   Status CSV       : remediation_status.csv
echo ============================================================
echo.
pause
