@echo off
title ADA Remediation Pipeline - Pass 1: Acrobat Pro

echo ============================================================
echo  ADA Remediation Pipeline - Pass 1: Acrobat Pro Automation
echo  Van Buren County, Michigan - DICE Department
echo ============================================================
echo.
echo  This runs acrobat_pass.py, which will:
echo    - Scan the downloads folder for any NEW PDFs
echo    - Skip files already marked complete in remediation_status.csv
echo    - Apply OCR and autotagging only to new/pending files
echo.

cd /d "%~dp0"

py -3 acrobat_pass.py

echo.
echo ============================================================
echo  Pass 1 complete.
echo  Run RUN_PIPELINE.bat next to complete Pass 2 remediation.
echo ============================================================
echo.
pause
