@echo off
title Install ADA Pipeline Dependencies
cd /d "%~dp0"

echo.
echo ══════════════════════════════════════════════════════════════
echo   INSTALL DEPENDENCIES  —  ADA Remediation Pipeline
echo   Van Buren County DICE Department
echo ══════════════════════════════════════════════════════════════
echo.
echo   This installs all Python libraries the pipeline needs.
echo   Run this ONCE before the first use. You do NOT need to
echo   re-run it every time you run the pipeline.
echo.
echo   Requires an internet connection.
echo   Press any key to begin, or Ctrl+C to cancel.
pause > nul
echo.

:: Verify py launcher is available
py -3 --version > nul 2>&1
if %errorlevel% neq 0 (
    echo   ERROR: Python 3 not found via 'py' launcher.
    echo.
    echo   Install Python from https://www.python.org/downloads/
    echo   During install, check "Add Python to PATH".
    echo   After installing, close and re-open this window, then re-run.
    pause
    exit /b 1
)

echo   Python found:
py -3 --version
echo.

:: ── Core PDF libraries ────────────────────────────────────────────────────
echo   Installing core PDF libraries...
py -3 -m pip install pikepdf pypdf --break-system-packages --upgrade --quiet
if %errorlevel% neq 0 echo   WARNING: pikepdf/pypdf install had errors

:: ── Acrobat COM automation ────────────────────────────────────────────────
echo   Installing pywin32 (Acrobat COM automation)...
py -3 -m pip install pywin32 --break-system-packages --upgrade --quiet
if %errorlevel% neq 0 echo   WARNING: pywin32 install had errors

:: ── Image processing and OCR ──────────────────────────────────────────────
echo   Installing image processing libraries (pdf2image, Pillow, pytesseract)...
py -3 -m pip install pdf2image Pillow pytesseract --break-system-packages --upgrade --quiet
if %errorlevel% neq 0 echo   WARNING: image library install had errors

:: ── Anthropic (Claude alt text) ───────────────────────────────────────────
echo   Installing Anthropic SDK (Claude Haiku alt text generation)...
py -3 -m pip install anthropic --break-system-packages --upgrade --quiet
if %errorlevel% neq 0 echo   WARNING: anthropic install had errors

:: ── Office document libraries (if needed later) ───────────────────────────
echo   Installing Office document libraries (python-docx, openpyxl)...
py -3 -m pip install python-docx openpyxl requests --break-system-packages --upgrade --quiet
if %errorlevel% neq 0 echo   WARNING: office library install had errors

:: ── Post-install: register pywin32 COM type library ──────────────────────
echo.
echo   Registering pywin32 COM support...
py -3 -m win32com.client.makepy 2> nul
if %errorlevel% neq 0 (
    echo   NOTE: win32com makepy returned a non-zero exit code.
    echo   This is usually harmless — the library still works.
)

echo.
echo ══════════════════════════════════════════════════════════════
echo   VERIFICATION
echo ══════════════════════════════════════════════════════════════
py -3 -c "import pikepdf; import pypdf; import win32com.client; import anthropic; print('  All core libraries verified OK')" 2>&1
echo.

echo ══════════════════════════════════════════════════════════════
echo   INSTALLATION COMPLETE
echo ══════════════════════════════════════════════════════════════
echo.
echo   MANUAL STEPS STILL REQUIRED:
echo.
echo   1. SET YOUR API KEY:
echo      Copy .env.example (in the repo root) to .env
echo      Fill in:  ANTHROPIC_API_KEY=sk-ant-...your-key-here...
echo      Then load it before running:
echo        PowerShell: Get-Content ..\\.env ^| ForEach-Object { $k,$v = $_ -split '=',2; [System.Environment]::SetEnvironmentVariable($k,$v) }
echo      Never edit the API key directly into ada_remediate.py.
echo.
echo   2. VERIFY POPPLER PATH:
echo      Poppler should be installed at:
echo        C:\poppler\Library\bin
echo      If it is somewhere else, set POPPLER_PATH in .env (see .env.example).
echo      Download Poppler for Windows: https://github.com/oschwartz10612/poppler-windows/releases
echo.
echo   3. VERIFY TESSERACT PATH (OCR is handled by Acrobat, but pytesseract):
echo      Tesseract should be at:
echo        C:\Program Files\Tesseract-OCR\tesseract.exe
echo      If not, set TESSERACT_PATH in .env (see .env.example).
echo      Download: https://github.com/UB-Mannheim/tesseract/wiki
echo.
echo   4. PLACE PDF FILES:
echo      Copy all source PDFs to:
echo        remediation_work\downloads\
echo.
echo   Then run RUN_PIPELINE.bat to start.
echo.
pause
