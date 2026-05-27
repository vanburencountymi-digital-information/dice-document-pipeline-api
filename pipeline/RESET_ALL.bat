@echo off
title Reset All — Full Clean — Van Buren County ADA Pipeline
cd /d "%~dp0"

echo.
echo ══════════════════════════════════════════════════════════════
echo   RESET ALL  —  Full Clean
echo   ADA Remediation Pipeline — Van Buren County
echo ══════════════════════════════════════════════════════════════
echo.
echo   This performs a COMPLETE reset of the pipeline.
echo   Both Acrobat pass and Python pass will be re-run from scratch.
echo.
echo   What gets cleared:
echo     - Deletes:  remediation_status.csv  (rebuilt from downloads/ on next run)
echo     - Clears:   remediation_work\remediated\
echo     - Clears:   remediation_work\logs\
echo.
echo   What is KEPT:
echo     - remediation_work\downloads\   (source PDFs are never touched)
echo.
echo   NOTE: Because remediation_status.csv is deleted, the NEXT
echo   run will re-run BOTH passes (Acrobat + Python) on all files.
echo   This is the expensive option — use RESET_PIPELINE.bat instead
echo   if you only need to re-run Pass 2.
echo.
echo ══════════════════════════════════════════════════════════════
echo   ARE YOU SURE?  Press any key to continue, or Ctrl+C to cancel.
echo ══════════════════════════════════════════════════════════════
pause > nul

echo.
echo   Performing full reset...
echo.

py -3.11 _reset_all.py

echo.
echo ══════════════════════════════════════════════════════════════
echo   RESET COMPLETE
echo   Run RUN_PIPELINE.bat to start a full fresh pipeline run.
echo ══════════════════════════════════════════════════════════════
echo.
pause
