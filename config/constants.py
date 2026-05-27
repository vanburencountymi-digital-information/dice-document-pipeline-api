"""
Pipeline configuration — county identity, model, paths.

To adapt this pipeline for a different county:
  1. Edit the county identity block below.
  2. Verify the tool paths match your Windows installation.
  3. Set ANTHROPIC_API_KEY in your environment (or .env file) — never hardcode it here.
"""

import os
from pathlib import Path

# ── County identity ───────────────────────────────────────────────────────────
COUNTY_NAME         = "Van Buren County"
COUNTY_DEPARTMENT   = "DICE Department"
COUNTY_AUTHOR       = "Van Buren County"
COUNTY_SUBJECT      = "Van Buren County Government Document"
COUNTY_PRODUCER     = "Van Buren County ADA Remediation Pipeline"

# ── Claude model ──────────────────────────────────────────────────────────────
CLAUDE_MODEL        = "claude-haiku-4-5-20251001"

# ── Pipeline working directories ──────────────────────────────────────────────
# Paths are relative to the pipeline/ directory (where the scripts are run from).
# They are created automatically on first run — no manual setup needed.
DOWNLOADS_DIR       = Path("remediation_work/downloads")
REMEDIATED_DIR      = Path("remediation_work/remediated")
LOGS_DIR            = Path("remediation_work/logs")
STATUS_CSV          = Path("remediation_status.csv")

# ── External tool paths (Windows) ─────────────────────────────────────────────
# These default to the standard Windows installation locations.
# Override by setting the corresponding environment variable in .env.
POPPLER_PATH        = os.environ.get("POPPLER_PATH",     r"C:\poppler\Library\bin")
TESSERACT_PATH      = os.environ.get("TESSERACT_PATH",   r"C:\Program Files\Tesseract-OCR\tesseract.exe")
# Leave GHOSTSCRIPT_PATH empty to let the pipeline auto-detect gswin64c.exe.
GHOSTSCRIPT_PATH    = os.environ.get("GHOSTSCRIPT_PATH", "")
