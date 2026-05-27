@echo off
title ADA Remediation Pipeline — Van Buren County
cd /d "%~dp0"

echo.
echo ══════════════════════════════════════════════════════════════
echo   ADA REMEDIATION PIPELINE  —  Van Buren County DICE Dept
echo ══════════════════════════════════════════════════════════════
echo.
echo   IMPORTANT BEFORE YOU START:
echo   1. Adobe Acrobat Pro must be CLOSED (script needs COM control)
echo   2. ANTHROPIC_API_KEY must be set in your environment or .env
echo   3. PDF files must be in:  remediation_work\downloads\
echo.
echo   Press any key to begin, or Ctrl+C to cancel.
pause > nul

echo.
echo ══════════════════════════════════════════════════════════════
echo   PASS 1  —  ACROBAT PRO AUTOMATION
echo   OCR, Autotag, Full Accessibility Check
echo ══════════════════════════════════════════════════════════════
echo.

py -3.11 acrobat_pass.py

if %errorlevel% neq 0 (
    echo.
    echo   ============================================================
    echo   ERROR: Pass 1 exited with an error (code %errorlevel%).
    echo   ============================================================
    echo.
    echo   Common causes:
    echo     - Acrobat Pro is open (close it and re-run)
    echo     - acrobat_pass.py has a Python error (check output above)
    echo     - No documents are pending Pass 1 (that is OK - not an error)
    echo.
    echo   If Pass 1 already finished successfully, use RUN_PASS2_ONLY.bat
    echo   to run Pass 2 without repeating Pass 1.
    echo.
    echo   This window will stay open so you can read the error above.
    pause
    exit /b %errorlevel%
)

echo.
echo ══════════════════════════════════════════════════════════════
echo   PASS 2  —  PYTHON REMEDIATION PIPELINE
echo   Metadata, Alt Text, Compliance Scoring, Logs
echo ══════════════════════════════════════════════════════════════
echo.

py -3.11 ada_remediate.py

echo.
echo ══════════════════════════════════════════════════════════════
echo   PIPELINE COMPLETE
echo   Remediated files : remediation_work\remediated\
echo   Compliance logs  : remediation_work\logs\
echo   Status CSV       : remediation_status.csv
echo ══════════════════════════════════════════════════════════════
echo.
pause
