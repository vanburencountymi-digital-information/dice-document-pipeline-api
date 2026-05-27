#!/usr/bin/env python3
"""
ADA Remediation Pipeline — Van Buren County, Michigan — DICE Department

Single-pass pipeline. Processes every row in remediation_status.csv
where pipeline_pass_status = pending.

Usage:
    py -3 ada_remediate.py

What this does per document:
    1. Embeds fonts via Ghostscript (non-destructive: src → temp)
    2. Applies pikepdf fixes (structure tree, metadata, tab order, tooltips)
    3. Runs Tesseract OCR fallback for scanned documents with no text layer
    4. Generates alt text for substantive images using Claude Haiku
    5. Detects and tags headings via Claude Vision
    6. Writes a structured per-document compliance log
    7. Calculates a compliance score and grade
    8. Updates remediation_status.csv
"""

import base64
import csv
import datetime
import hashlib
import io
import os
import re
import sys
from pathlib import Path

# ── Config import ─────────────────────────────────────────────────────────────
# Add the repo root to sys.path so 'config.constants' is importable regardless
# of the current working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.constants import (
    COUNTY_NAME, COUNTY_DEPARTMENT, COUNTY_AUTHOR, COUNTY_SUBJECT, COUNTY_PRODUCER,
    CLAUDE_MODEL, DOWNLOADS_DIR, REMEDIATED_DIR, LOGS_DIR, STATUS_CSV,
    POPPLER_PATH, TESSERACT_PATH, GHOSTSCRIPT_PATH,
)
# ─────────────────────────────────────────────────────────────────────────────

# API key — set ANTHROPIC_API_KEY in your environment or .env file.
# See .env.example at the repo root for instructions.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

TODAY = datetime.date.today().isoformat()

# ── Ghostscript auto-detection ────────────────────────────────────────────────
import glob as _glob

def _find_ghostscript() -> str:
    """Locate gswin64c.exe on Windows. Returns empty string if not found."""
    if GHOSTSCRIPT_PATH:
        return GHOSTSCRIPT_PATH
    for pat in [
        r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
        r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe",
    ]:
        hits = sorted(_glob.glob(pat))
        if hits:
            return hits[-1]
    return ""

_GS_EXE = _find_ghostscript()

# Images smaller than this in either dimension are treated as decorative
IMAGE_MIN_SIZE = 100  # pixels

# CSV field names
CSV_FIELDNAMES = [
    "filename", "title", "pipeline_pass_status",
    "ocr_applied", "alt_text_images", "compliance_score",
    "compliance_grade", "manual_review_items", "log_path", "processed_date",
]

# Compliance score deductions by issue key
DEDUCTIONS: dict[str, int] = {
    "no_structure_tree":  18,   # Critical — Acrobat autotag produced no tree
    "no_text_layer":      20,   # Critical — OCR did not produce a text layer
    "missing_alt_text":   12,   # High     — substantive images lack alt text
    "form_no_tooltips":   10,   # High     — form fields without tooltip text
    "missing_headings":   10,   # Medium   — no heading structure
    "missing_bookmarks":   6,   # Medium   — no bookmarks on long documents
    "unembedded_fonts":    6,   # Medium   — fonts not embedded
    "vague_hyperlinks":    6,   # Medium   — vague link text
    "color_contrast":      5,   # Low      — always flagged (manual check only)
    "complex_tables":      4,   # Low      — table header verification needed
}

# Grade thresholds: (minimum_score, grade)
GRADE_THRESHOLDS = [(90, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F")]

# Regex for common vague hyperlink phrases
VAGUE_LINK_RE = re.compile(
    r"^(click here|here|this link|read more|more|learn more|click|link)$",
    re.IGNORECASE,
)

# Per-session alt text cache: md5_hex(jpeg_bytes) → alt_text_string
# Prevents duplicate Claude API calls for the same county seal/logo
_alt_cache: dict[str, str] = {}


class APICreditsExhaustedError(RuntimeError):
    """Raised when the Anthropic API returns a billing / credit error.

    Caught in the main loop to stop processing immediately and leave the
    current document as pending so it can be retried after credits are
    topped up.  Distinguished from ordinary per-call failures (timeouts,
    bad images) which are handled silently inside each API helper.
    """


# Set to True the first time a credit/billing error is detected.
# All subsequent Claude API helpers check this flag first and skip
# their call, preventing a flood of identical error responses once
# credits are exhausted.
_api_credits_exhausted: bool = False


# ── Library imports with graceful degradation ─────────────────────────────────

_missing_required = []
try:
    import pikepdf
except ImportError:
    _missing_required.append("pikepdf")
try:
    from pypdf import PdfReader
except ImportError:
    _missing_required.append("pypdf")

if _missing_required:
    print(f"ERROR: Missing required libraries: {', '.join(_missing_required)}")
    print("       Run INSTALL_DEPENDENCIES.bat to install all required packages.")
    sys.exit(1)

# Optional libraries — pipeline degrades gracefully when unavailable
PDF2IMAGE_AVAILABLE = False
try:
    from pdf2image import convert_from_path
    from PIL import Image as PILImage
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    pass

ANTHROPIC_AVAILABLE = False
_anthropic_client = None
try:
    import anthropic as _anthropic_lib
    ANTHROPIC_AVAILABLE = True
    if ANTHROPIC_API_KEY:
        try:
            _anthropic_client = _anthropic_lib.Anthropic(api_key=ANTHROPIC_API_KEY)
        except Exception:
            _anthropic_client = None
except ImportError:
    pass

PYTESSERACT_AVAILABLE = False
try:
    import pytesseract as _pytesseract_lib
    PYTESSERACT_AVAILABLE = True
    # Point pytesseract at the Tesseract binary configured in the county config block
    if TESSERACT_PATH and Path(TESSERACT_PATH).exists():
        _pytesseract_lib.pytesseract.tesseract_cmd = TESSERACT_PATH
except ImportError:
    pass


# ── Utilities ─────────────────────────────────────────────────────────────────

def filename_to_title(filename: str) -> str:
    """Derive a human-readable document title from a PDF filename.

    '12731-annual-report-2024.pdf' → 'Annual Report 2024'
    'budget_summary.pdf'           → 'Budget Summary'
    """
    stem = Path(filename).stem
    title = re.sub(r"[-_]+", " ", stem)
    title = re.sub(r"^\d{4,}\s+", "", title)
    return title.title().strip()


# ── CSV manager ───────────────────────────────────────────────────────────────

class CSVManager:
    """Reads and updates the pipeline status CSV.

    Saves immediately after every row update so that a crash cannot lose
    more than one document's worth of progress.
    """

    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self.rows: list[dict] = []
        self._load()

    def _load(self):
        """Load the status CSV. Exits with a clear error if not found."""
        if not self.csv_path.exists():
            print(f"ERROR: Status CSV not found at {self.csv_path}")
            print("       Create it with filename,title,pipeline_pass_status columns.")
            sys.exit(1)
        with open(self.csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            self.rows = list(reader)

    def save(self):
        """Write all rows to the CSV file immediately."""
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.rows)

    def get_ready(self) -> list[dict]:
        """Return rows eligible for processing (pipeline_pass_status = pending)."""
        return [
            r for r in self.rows
            if r.get("pipeline_pass_status") == "pending"
        ]

    def update_row(self, filename: str, updates: dict):
        """Update a row by filename and save the CSV immediately."""
        for row in self.rows:
            if row["filename"] == filename:
                row.update(updates)
                break
        self.save()


# ── Document assessment ───────────────────────────────────────────────────────

def assess_document(pdf_path: Path) -> dict:
    """Inspect a PDF for all accessibility issues this pipeline can detect.

    Returns a dict with keys describing each check result. All keys are always
    present so callers don't need to guard with .get() defaults.
    """
    issues: dict = {
        "has_structure_tree": False,    # StructTreeRoot present
        "has_text_layer":     False,    # Meaningful extractable text
        "has_images":         False,    # Substantive XObject images found
        "images_without_alt": 0,        # Figure elements missing /Alt
        "has_forms":          False,    # AcroForm present
        "form_fields_count":  0,        # Number of form fields
        "form_fields_without_tooltips": 0,  # Fields where /TU is missing
        "has_bookmarks":      False,    # PDF outline/bookmarks present
        "page_count":         0,
        "unembedded_fonts":   [],       # List of possibly-unembedded font names
        "vague_links":        [],       # List of vague /Contents annotation texts
        "has_tables":              False,  # Table structure elements found
        "tables_without_headers":  0,   # Tables whose first row has no TH
        "has_headings":            False,  # Heading structure elements found
    }

    # ── pypdf checks (lightweight) ─────────────────────────────────────────
    try:
        reader = PdfReader(str(pdf_path))
        issues["page_count"] = len(reader.pages)

        # Text layer check
        sample_text = ""
        for page in reader.pages[:5]:
            sample_text += (page.extract_text() or "")
        issues["has_text_layer"] = len(sample_text.strip()) > 50

        # Bookmark check
        if reader.outline:
            issues["has_bookmarks"] = True

        # Heuristic heading detection from raw text
        if re.search(r"\n[A-Z][A-Z\s]{2,50}\n", sample_text):
            issues["has_headings"] = True
        if re.search(r"^\d+\.\s+[A-Z]", sample_text, re.MULTILINE):
            issues["has_headings"] = True
    except Exception:
        pass

    # ── pikepdf checks (structural) ────────────────────────────────────────
    try:
        with pikepdf.open(str(pdf_path)) as pdf:

            # ── Structure tree ─────────────────────────────────────────────
            if "/StructTreeRoot" in pdf.Root:
                issues["has_structure_tree"] = True
                struct_root = pdf.Root["/StructTreeRoot"]

                # Walk the tree to find headings, tables, and figures without alt text
                def walk(elem: object, depth: int = 0) -> None:
                    """Recursively walk a structure tree element."""
                    if depth > 20 or not isinstance(elem, pikepdf.Dictionary):
                        return
                    s_type = str(elem.get("/S", ""))
                    if s_type in ("/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6"):
                        issues["has_headings"] = True
                    if s_type == "/Table":
                        issues["has_tables"] = True
                        # Check whether the first row already has TH elements
                        try:
                            first_tr = _get_first_tr(elem, pdf)
                            n_rows   = _count_trs_in_table(elem, pdf)
                            if first_tr is not None and n_rows >= 2:
                                tr_kids = first_tr.get("/K")
                                has_th = (
                                    isinstance(tr_kids, pikepdf.Array)
                                    and any(
                                        str((c if isinstance(c, pikepdf.Dictionary)
                                             else pdf.get_object(c.objgen)
                                             ).get("/S", "")) == "/TH"
                                        for c in tr_kids
                                    )
                                )
                                if not has_th:
                                    issues["tables_without_headers"] += 1
                        except Exception:
                            pass
                    if s_type == "/Figure":
                        alt = elem.get("/Alt")
                        if alt is None or str(alt).strip() == "":
                            issues["images_without_alt"] += 1
                    kids = elem.get("/K")
                    if kids is None:
                        return
                    if isinstance(kids, pikepdf.Array):
                        for kid in kids:
                            walk(kid, depth + 1)
                    elif isinstance(kids, pikepdf.Dictionary):
                        walk(kids, depth + 1)

                walk(struct_root)

            # ── XObject image scan ─────────────────────────────────────────
            substantive_count = 0
            for page in pdf.pages:
                resources = page.get("/Resources")
                if resources is None:
                    continue
                xobjects = resources.get("/XObject", {})
                for name in xobjects:
                    try:
                        xobj = xobjects[name]
                        if str(xobj.get("/Subtype", "")) == "/Image":
                            w = int(xobj.get("/Width", 0))
                            h = int(xobj.get("/Height", 0))
                            if w >= IMAGE_MIN_SIZE and h >= IMAGE_MIN_SIZE:
                                substantive_count += 1
                    except Exception:
                        pass
            issues["has_images"] = substantive_count > 0

            # ── Font embedding check ───────────────────────────────────────
            seen_bases: set[str] = set()
            for page in pdf.pages:
                resources = page.get("/Resources")
                if resources is None:
                    continue
                fonts = resources.get("/Font", {})
                for font_key in fonts:
                    try:
                        font = fonts[font_key]
                        base = str(font.get("/BaseFont", font_key))
                        if base in seen_bases:
                            continue
                        seen_bases.add(base)
                        # A missing /FontDescriptor on Type1/TrueType usually means
                        # the font is not embedded — UNLESS it's one of the PDF
                        # base-14 fonts, which every conforming viewer must provide.
                        # These cannot be embedded (no font file ships with them)
                        # and accessibility checkers do not flag them.
                        _BASE14 = {
                            "/Courier", "/Courier-Bold", "/Courier-Oblique", "/Courier-BoldOblique",
                            "/Helvetica", "/Helvetica-Bold", "/Helvetica-Oblique", "/Helvetica-BoldOblique",
                            "/Times-Roman", "/Times-Bold", "/Times-Italic", "/Times-BoldItalic",
                            "/Symbol", "/ZapfDingbats",
                        }
                        if font.get("/FontDescriptor") is None:
                            ftype = str(font.get("/Subtype", ""))
                            if ftype in ("/Type1", "/TrueType") and base not in _BASE14:
                                issues["unembedded_fonts"].append(base)
                    except Exception:
                        pass

            # ── Form field check ───────────────────────────────────────────
            if "/AcroForm" in pdf.Root:
                issues["has_forms"] = True
                acro = pdf.Root["/AcroForm"]

                total_fields = 0
                missing_tooltips = 0

                def _count_fields(fields_arr):
                    nonlocal total_fields, missing_tooltips
                    if not isinstance(fields_arr, pikepdf.Array):
                        return
                    for item in fields_arr:
                        try:
                            fd = (item if isinstance(item, pikepdf.Dictionary)
                                  else pdf.get_object(item.objgen))
                            if fd.get("/T") is not None:   # has a field name → it's a field
                                total_fields += 1
                                tu = fd.get("/TU")
                                if not tu or str(tu).strip() == "":
                                    missing_tooltips += 1
                            kids = fd.get("/Kids")
                            if kids:
                                _count_fields(kids)
                        except Exception:
                            pass

                _count_fields(acro.get("/Fields", []))
                issues["form_fields_count"] = total_fields
                issues["form_fields_without_tooltips"] = missing_tooltips

            # ── Vague hyperlink check ──────────────────────────────────────
            for page in pdf.pages:
                annots = page.get("/Annots", [])
                for annot in annots:
                    try:
                        if (isinstance(annot, pikepdf.Dictionary)
                                and str(annot.get("/Subtype", "")) == "/Link"):
                            contents = str(annot.get("/Contents", "")).strip()
                            if VAGUE_LINK_RE.match(contents):
                                issues["vague_links"].append(contents)
                    except Exception:
                        pass

    except Exception:
        pass  # Assessment errors are non-fatal; we report what we found

    return issues


# ── pikepdf remediations ──────────────────────────────────────────────────────

def apply_xmp_and_docinfo(pdf: pikepdf.Pdf, title: str) -> list[str]:
    """Set the full XMP metadata suite and DocInfo dictionary fields.

    Sets dc:title, dc:language, dc:description, pdf:Producer, pdfuaid:part
    in XMP, and the corresponding /Title, /Subject, /Author, /Producer,
    /Creator fields in DocInfo.

    Returns a list of human-readable descriptions of fixes applied.
    """
    fixes: list[str] = []
    description = f"{COUNTY_NAME} government document: {title}"

    # XMP metadata (preferred by modern accessibility checkers)
    try:
        with pdf.open_metadata() as meta:
            meta["dc:title"]       = title
            meta["dc:language"]    = "en-US"
            meta["dc:description"] = description
            meta["pdf:Producer"]   = COUNTY_PRODUCER
            try:
                meta["pdfuaid:part"] = "1"   # PDF/UA-1 conformance claim
            except Exception:
                pass  # Older pikepdf versions may not support pdfuaid namespace
        fixes.append("XMP metadata: dc:title, dc:language, dc:description, pdf:Producer, pdfuaid:part")
    except Exception as e:
        fixes.append(f"XMP metadata: partial ({e})")

    # DocInfo (legacy — still read by many tools)
    try:
        pdf.docinfo["/Title"]    = title
        pdf.docinfo["/Subject"]  = COUNTY_SUBJECT
        pdf.docinfo["/Author"]   = COUNTY_AUTHOR
        pdf.docinfo["/Producer"] = COUNTY_PRODUCER
        pdf.docinfo["/Creator"]  = f"{COUNTY_NAME} ADA Remediation Pipeline"
        fixes.append("DocInfo: /Title, /Subject, /Author, /Producer, /Creator")
    except Exception as e:
        fixes.append(f"DocInfo: partial ({e})")

    return fixes


def set_root_accessibility_flags(pdf: pikepdf.Pdf) -> list[str]:
    """Set /Lang, MarkInfo/Marked, and ViewerPreferences/DisplayDocTitle on Root.

    These are required by PDF/UA-1 and tested by WCAG PDF accessibility checkers.
    Returns a list of human-readable descriptions of fixes applied.
    """
    fixes: list[str] = []

    try:
        pdf.Root["/Lang"] = pikepdf.String("en-US")
        fixes.append("/Lang = en-US set on document root")
    except Exception as e:
        fixes.append(f"/Lang: failed ({e})")

    try:
        if "/MarkInfo" not in pdf.Root:
            pdf.Root["/MarkInfo"] = pdf.make_indirect(pikepdf.Dictionary())
        pdf.Root["/MarkInfo"]["/Marked"] = pikepdf.Boolean(True)
        fixes.append("MarkInfo/Marked = True")
    except Exception as e:
        fixes.append(f"MarkInfo: failed ({e})")

    try:
        if "/ViewerPreferences" not in pdf.Root:
            pdf.Root["/ViewerPreferences"] = pdf.make_indirect(pikepdf.Dictionary())
        pdf.Root["/ViewerPreferences"]["/DisplayDocTitle"] = pikepdf.Boolean(True)
        fixes.append("ViewerPreferences/DisplayDocTitle = True")
    except Exception as e:
        fixes.append(f"ViewerPreferences: failed ({e})")

    return fixes


def set_tab_order(pdf: pikepdf.Pdf) -> list[str]:
    """Set /Tabs /S (Structure-order tab traversal) on every page.

    Required by PDF/UA-1 §7.2 for correct screen-reader navigation.
    Returns a list describing the fix.
    """
    try:
        for page in pdf.pages:
            page["/Tabs"] = pikepdf.Name("/S")
        return [f"/Tabs /S (structure order) set on all {len(pdf.pages)} pages"]
    except Exception as e:
        return [f"/Tabs: failed ({e})"]


def _get_first_tr(
    table_elem: pikepdf.Dictionary, pdf: pikepdf.Pdf
) -> "pikepdf.Dictionary | None":
    """Return the first TR element in a Table, handling THead/TBody/TFoot groups."""
    kids = table_elem.get("/K")
    if not isinstance(kids, pikepdf.Array):
        return None
    for kid in kids:
        try:
            k = kid if isinstance(kid, pikepdf.Dictionary) else pdf.get_object(kid.objgen)
            ks = str(k.get("/S", ""))
            if ks == "/TR":
                return k
            if ks in ("/THead", "/TBody", "/TFoot"):
                inner = k.get("/K")
                if isinstance(inner, pikepdf.Array):
                    for ik in inner:
                        try:
                            ik_d = (ik if isinstance(ik, pikepdf.Dictionary)
                                    else pdf.get_object(ik.objgen))
                            if str(ik_d.get("/S", "")) == "/TR":
                                return ik_d
                        except Exception:
                            pass
        except Exception:
            pass
    return None


def _count_trs_in_table(
    table_elem: pikepdf.Dictionary, pdf: pikepdf.Pdf
) -> int:
    """Count all TR children in a Table, including those under THead/TBody/TFoot."""
    count = 0
    kids = table_elem.get("/K")
    if not isinstance(kids, pikepdf.Array):
        return 0
    for kid in kids:
        try:
            k = kid if isinstance(kid, pikepdf.Dictionary) else pdf.get_object(kid.objgen)
            ks = str(k.get("/S", ""))
            if ks == "/TR":
                count += 1
            elif ks in ("/THead", "/TBody", "/TFoot"):
                inner = k.get("/K")
                if isinstance(inner, pikepdf.Array):
                    for ik in inner:
                        try:
                            ik_d = (ik if isinstance(ik, pikepdf.Dictionary)
                                    else pdf.get_object(ik.objgen))
                            if str(ik_d.get("/S", "")) == "/TR":
                                count += 1
                        except Exception:
                            pass
        except Exception:
            pass
    return count


def fix_form_tooltips(pdf: pikepdf.Pdf) -> list[str]:
    """Add tooltip (/TU) to every form field that is missing one.

    Copies the field's internal name (/T) into the user-visible tooltip (/TU).
    Screen readers read /TU aloud when the user tabs to the field; without it
    they read the raw technical name or say nothing at all.

    Returns a list of fix descriptions, one per field that was updated.
    """
    fixes: list[str] = []
    if "/AcroForm" not in pdf.Root:
        return fixes

    def walk(fields_arr):
        if not isinstance(fields_arr, pikepdf.Array):
            return
        for item in fields_arr:
            try:
                fd = (item if isinstance(item, pikepdf.Dictionary)
                      else pdf.get_object(item.objgen))
                t_name = fd.get("/T")
                if t_name is not None:
                    tu = fd.get("/TU")
                    if not tu or str(tu).strip() == "":
                        fd["/TU"] = t_name
                        fixes.append(f"Form field '{str(t_name)}': tooltip (/TU) set")
                kids = fd.get("/Kids")
                if kids:
                    walk(kids)
            except Exception:
                pass

    try:
        walk(pdf.Root["/AcroForm"].get("/Fields", []))
    except Exception:
        pass
    return fixes


def embed_fonts_ghostscript(pdf_path: Path) -> tuple[bool, str]:
    """[Legacy — use embed_fonts_gs_to_temp() in the main pipeline flow.]
    Runs Ghostscript font embedding in-place (overwrites pdf_path).
    Kept for backwards compatibility; not called by the current pipeline.
    """
    if not _GS_EXE:
        return False, ""
    import subprocess
    tmp = pdf_path.with_suffix(".gs_tmp.pdf")
    try:
        result = subprocess.run(
            [_GS_EXE, "-dNOPAUSE", "-dBATCH", "-dSAFER",
             "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7",
             "-dEmbedAllFonts=true", "-dSubsetFonts=true",
             f"-sOutputFile={tmp}", str(pdf_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            pdf_path.unlink()
            tmp.rename(pdf_path)
            return True, "Ghostscript font embedding succeeded (in-place)"
        if tmp.exists():
            tmp.unlink()
        return False, (result.stderr or result.stdout or "unknown error").strip()[:200]
    except Exception as exc:
        if tmp.exists():
            try: tmp.unlink()
            except Exception: pass
        return False, str(exc)


def embed_fonts_gs_to_temp(src_path: Path, tmp_path: Path) -> tuple[bool, str]:
    """Run Ghostscript font embedding from src_path → tmp_path (non-destructive).

    The source file is never modified. The caller opens tmp_path with pikepdf
    to apply further fixes (structure tree, metadata, etc.) and then saves the
    final output to its destination. tmp_path should be cleaned up by the caller.

    Why this ordering is safe:
      - src_path (downloads/) has NO structure tree (autotag was never working).
      - GS re-renders to tmp_path, embedding fonts. No structure tree to destroy.
      - pikepdf then adds the structure tree to tmp_path's content → dst_path.
      - The structure tree is created AFTER GS, so GS cannot touch it.

    Returns (success: bool, message: str).
    Returns (False, "") silently if Ghostscript is not found on this machine.
    """
    if not _GS_EXE:
        return False, ""

    import subprocess
    try:
        result = subprocess.run(
            [
                _GS_EXE,
                "-dNOPAUSE", "-dBATCH", "-dSAFER",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.7",
                "-dEmbedAllFonts=true",
                "-dSubsetFonts=true",
                f"-sOutputFile={tmp_path}",
                str(src_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
            return True, "Ghostscript font embedding succeeded"
        if tmp_path.exists():
            tmp_path.unlink()
        msg = (result.stderr or result.stdout or "unknown error").strip()[:200]
        return False, f"Ghostscript failed: {msg}"
    except subprocess.TimeoutExpired:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass
        return False, "Ghostscript timed out (>120 s)"
    except Exception as exc:
        if tmp_path.exists():
            try: tmp_path.unlink()
            except Exception: pass
        return False, str(exc)


def apply_tesseract_ocr(pdf_path: Path, page_count: int) -> "tuple[bool, int, str]":
    """Rebuild a scanned PDF with a Tesseract-generated invisible text layer.

    Called when a document still has no text layer after Acrobat Pass 1 OCR.
    For each page:
      1. Renders the page to a PIL image at 300 DPI (good OCR quality)
      2. Calls pytesseract.image_to_pdf_or_hocr(..., extension='pdf') which
         returns a one-page PDF where the image sits on top and Tesseract's
         recognised text floats as an invisible (white) overlay underneath
      3. All per-page PDFs are assembled into one multi-page PDF with pikepdf
      4. The assembled PDF overwrites pdf_path via a safe temp-file rename

    Returns (success: bool, pages_ocr'd: int, message: str).
    Returns (False, 0, reason) if pdf2image or pytesseract are unavailable.
    """
    if not PDF2IMAGE_AVAILABLE:
        return False, 0, "skipped — pdf2image not available"
    if not PYTESSERACT_AVAILABLE:
        return False, 0, "skipped — pytesseract not installed"
    if not pdf_path.exists() or page_count <= 0:
        return False, 0, "skipped — no file or zero pages"

    try:
        import pytesseract as _pytess
        if TESSERACT_PATH and Path(TESSERACT_PATH).exists():
            _pytess.pytesseract.tesseract_cmd = TESSERACT_PATH
    except ImportError:
        return False, 0, "pytesseract import failed"

    ocr_page_buffers: list[bytes] = []
    pages_ocrd = 0

    for pg_idx in range(page_count):
        # Render page at 300 DPI — higher than the 150 DPI used for Claude Vision,
        # because OCR accuracy degrades sharply below ~200 DPI on small text
        try:
            images = convert_from_path(
                str(pdf_path),
                dpi=300,
                first_page=pg_idx + 1,
                last_page=pg_idx + 1,
                poppler_path=POPPLER_PATH,
            )
            if not images:
                continue
            pil_img = images[0]
        except Exception:
            continue

        # pytesseract returns a bytes object that is a valid single-page PDF
        try:
            page_pdf_bytes = _pytess.image_to_pdf_or_hocr(pil_img, extension="pdf")
            ocr_page_buffers.append(bytes(page_pdf_bytes))
            pages_ocrd += 1
        except Exception:
            continue

    if not ocr_page_buffers:
        return False, 0, "Tesseract could not OCR any pages (check Tesseract installation)"

    # Stitch all per-page PDFs into one document and overwrite pdf_path safely
    tmp_path = pdf_path.with_suffix(".tess_tmp.pdf")
    try:
        with pikepdf.Pdf.new() as combined:
            for page_bytes in ocr_page_buffers:
                with pikepdf.Pdf.open(io.BytesIO(page_bytes)) as src:
                    combined.pages.extend(src.pages)
            combined.save(str(tmp_path))
        pdf_path.unlink()
        tmp_path.rename(pdf_path)
        return (
            True,
            pages_ocrd,
            f"Tesseract OCR: {pages_ocrd}/{page_count} page(s) — text layer added",
        )
    except Exception as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False, 0, f"failed to assemble Tesseract PDF: {exc}"


def _reapply_metadata(pdf_path: Path, title: str) -> list[str]:
    """Re-apply XMP metadata, /Lang, MarkInfo, and tab order after Tesseract rebuild.

    Tesseract's image_to_pdf_or_hocr produces a bare-minimum PDF with no
    accessibility metadata. This function restores the county branding and
    PDF/UA-1 markers that apply_pikepdf_remediations would normally set.

    Returns a list of fix descriptions (passed to the remediation log).
    """
    fixes: list[str] = []
    try:
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            fixes += apply_xmp_and_docinfo(pdf, title)
            fixes += set_root_accessibility_flags(pdf)
            fixes += add_minimal_structure_tree(pdf)   # Tesseract wipes the tree — restore it
            fixes += set_tab_order(pdf)
            pdf.save(str(pdf_path))
    except Exception as exc:
        fixes.append(f"metadata re-apply after Tesseract failed: {exc}")
    return fixes


def fix_table_headers(pdf: pikepdf.Pdf) -> list[str]:
    """Promote first-row TD cells to TH and add /Scope /Column.

    Walks the entire structure tree looking for Table elements where the
    first TR row contains only TD (data) cells instead of TH (header) cells.
    Only acts on tables with 2+ rows — single-row tables are skipped because
    a table with nothing but header cells is semantically invalid.

    This directly removes the 'complex tables' score deduction for tables
    that are correctly structured except for the missing TH designation.

    Returns a list of fix descriptions, one per table fixed.
    """
    fixes: list[str] = []
    if "/StructTreeRoot" not in pdf.Root:
        return fixes

    tables_found: list[pikepdf.Dictionary] = []

    def collect_tables(elem: object, depth: int = 0) -> None:
        if depth > 30 or not isinstance(elem, pikepdf.Dictionary):
            return
        if str(elem.get("/S", "")) == "/Table":
            tables_found.append(elem)
            return  # don't recurse into nested tables — handle as separate units
        kids = elem.get("/K")
        if kids is None:
            return
        if isinstance(kids, pikepdf.Array):
            for kid in kids:
                collect_tables(kid, depth + 1)
        elif isinstance(kids, pikepdf.Dictionary):
            collect_tables(kids, depth + 1)

    try:
        collect_tables(pdf.Root["/StructTreeRoot"])
    except Exception:
        return fixes

    for t_idx, table in enumerate(tables_found, start=1):
        try:
            n_rows   = _count_trs_in_table(table, pdf)
            if n_rows < 2:
                continue  # single-row table — skip

            first_tr = _get_first_tr(table, pdf)
            if first_tr is None:
                continue

            tr_kids = first_tr.get("/K")
            if not isinstance(tr_kids, pikepdf.Array):
                continue

            # Collect TD cells; skip if any TH already present
            td_cells: list[pikepdf.Dictionary] = []
            already_has_th = False
            for kid in tr_kids:
                try:
                    cell = (kid if isinstance(kid, pikepdf.Dictionary)
                            else pdf.get_object(kid.objgen))
                    cs = str(cell.get("/S", ""))
                    if cs == "/TH":
                        already_has_th = True
                        break
                    if cs == "/TD":
                        td_cells.append(cell)
                except Exception:
                    pass

            if already_has_th or not td_cells:
                continue

            # Promote each TD → TH and add /Scope /Column
            promoted = 0
            for cell in td_cells:
                try:
                    cell["/S"]     = pikepdf.Name("/TH")
                    cell["/Scope"] = pikepdf.Name("/Column")
                    promoted += 1
                except Exception:
                    pass

            if promoted:
                fixes.append(
                    f"Table {t_idx}: {promoted} first-row TD cell(s) promoted to TH "
                    f"with /Scope /Column ({n_rows} rows total)"
                )
        except Exception:
            pass

    return fixes


def generate_bookmarks_from_structure(pdf: pikepdf.Pdf, src_path: Path) -> list[str]:
    """Generate PDF outline bookmarks from structure tree heading elements.

    Only runs when all three conditions are true:
      - The document has a StructTreeRoot with H/H1-H6 elements (added by Acrobat autotag)
      - The document is 10+ pages (short docs don't need navigation bookmarks)
      - The document has no existing outline entries

    Heading label text is taken from /ActualText on the struct element if present,
    otherwise falls back to a pypdf first-line extraction from the heading's page.

    Returns a list of fix descriptions.
    """
    fixes: list[str] = []

    # Precondition: struct tree exists
    if "/StructTreeRoot" not in pdf.Root:
        return fixes

    # Precondition: long enough to need bookmarks
    if len(pdf.pages) < 10:
        return fixes

    # Precondition: no existing outline
    if "/Outlines" in pdf.Root:
        try:
            if int(pdf.Root["/Outlines"].get("/Count", 0)) != 0:
                return fixes
        except Exception:
            pass

    # Build page objgen → 0-based index map
    page_index: dict = {}
    for i, page in enumerate(pdf.pages):
        try:
            page_index[page.objgen] = i
        except Exception:
            pass

    # Walk the struct tree and collect heading elements
    HEADING_LEVELS = {
        "/H": 1, "/H1": 1, "/H2": 2, "/H3": 3,
        "/H4": 4, "/H5": 5, "/H6": 6,
    }
    headings: list[tuple[int, int, str]] = []  # (level, page_idx, text)

    def collect(elem: object, depth: int = 0) -> None:
        if depth > 30 or not isinstance(elem, pikepdf.Dictionary):
            return
        s_type = str(elem.get("/S", ""))
        level  = HEADING_LEVELS.get(s_type, 0)
        if level > 0:
            pg = elem.get("/Pg")
            if pg is not None:
                try:
                    pg_idx = page_index.get(pg.objgen, -1)
                    if pg_idx >= 0:
                        text = ""
                        actual = elem.get("/ActualText")
                        if actual:
                            text = str(actual).strip()
                        if not text:
                            t_attr = elem.get("/T")
                            if t_attr:
                                t_str = str(t_attr).strip()
                                if not t_str.isdigit():   # skip numeric IDs like "0001"
                                    text = t_str
                        headings.append((level, pg_idx, text))
                except Exception:
                    pass
        kids = elem.get("/K")
        if kids is None:
            return
        if isinstance(kids, pikepdf.Array):
            for kid in kids:
                collect(kid, depth + 1)
        elif isinstance(kids, pikepdf.Dictionary):
            collect(kids, depth + 1)

    try:
        collect(pdf.Root["/StructTreeRoot"])
    except Exception:
        return fixes

    if not headings:
        return fixes

    # Fill in missing labels via pypdf first-line text extraction per page
    pages_needing_text = {pg_idx for _, pg_idx, text in headings if not text}
    page_first_lines: dict[int, str] = {}
    if pages_needing_text:
        try:
            reader = PdfReader(str(src_path))
            for pg_idx in pages_needing_text:
                if 0 <= pg_idx < len(reader.pages):
                    raw = reader.pages[pg_idx].extract_text() or ""
                    lines = [ln.strip() for ln in raw.split("\n")
                             if ln.strip() and len(ln.strip()) > 3]
                    if lines:
                        page_first_lines[pg_idx] = lines[0][:80]
        except Exception:
            pass

    # Resolve labels and deduplicate — one bookmark per page, highest heading wins
    best: dict[int, tuple[int, str]] = {}   # page_idx → (level, text)
    for level, pg_idx, text in headings:
        if not text:
            text = page_first_lines.get(pg_idx, f"Page {pg_idx + 1}")
        if pg_idx not in best or level < best[pg_idx][0]:
            best[pg_idx] = (level, text)

    ordered = sorted(best.items())   # sorted by page number
    if not ordered:
        return fixes

    # Write the PDF outline
    try:
        with pdf.open_outline() as outline:
            for pg_idx, (_, text) in ordered:
                outline.root.append(pikepdf.OutlineItem(text, pg_idx))
        fixes.append(
            f"Bookmarks: {len(ordered)} heading(s) added to PDF outline from structure tree"
        )
    except Exception as exc:
        fixes.append(f"Bookmarks: could not write outline ({exc})")

    return fixes


def _detect_headings_pdfplumber(src_path: Path) -> dict[int, list[dict]]:
    """Use pdfplumber font-size/weight heuristics to detect headings per page.

    Returns {page_index: [{"tag": "H1"|"H2"|"H3", "text": "..."}, ...]}.
    Empty dict means no headings detected (caller falls back to flat Sect).
    Returns empty dict on any error — never breaks the pipeline.
    """
    try:
        import pdfplumber
    except ImportError:
        return {}

    headings_by_page: dict[int, list[dict]] = {}
    try:
        with pdfplumber.open(str(src_path)) as plumb:
            # ── Sample up to 10 pages for body-size detection ────────────────
            sample_pages = plumb.pages[:10]
            size_counts: dict[float, int] = {}
            for pg in sample_pages:
                chars = pg.chars or []
                for ch in chars:
                    sz = round(float(ch.get("size", 0)), 1)
                    if sz > 0:
                        size_counts[sz] = size_counts.get(sz, 0) + 1
            if not size_counts:
                return {}  # scanned PDF or no text layer
            body_size = max(size_counts, key=size_counts.get)

            # ── Scan all pages for headings ──────────────────────────────────
            for page_idx, pg in enumerate(plumb.pages):
                chars = pg.chars or []
                if not chars:
                    continue

                # Group consecutive chars into runs by style
                runs: list[dict] = []
                cur_text = ""
                cur_size = 0.0
                cur_bold = False
                cur_top = 0.0
                for ch in chars:
                    sz = round(float(ch.get("size", 0)), 1)
                    if sz <= 0:
                        continue
                    fontname = str(ch.get("fontname", ""))
                    bold = ("Bold" in fontname or "bold" in fontname
                            or "Black" in fontname or "Heavy" in fontname)
                    top = float(ch.get("top", 0))
                    # New run if style changes or vertical jump (new line)
                    if (sz != cur_size or bold != cur_bold
                            or abs(top - cur_top) > sz * 1.5):
                        if cur_text.strip():
                            runs.append({"text": cur_text.strip(),
                                         "size": cur_size, "bold": cur_bold})
                        cur_text = ch.get("text", "")
                        cur_size = sz
                        cur_bold = bold
                        cur_top = top
                    else:
                        cur_text += ch.get("text", "")
                if cur_text.strip():
                    runs.append({"text": cur_text.strip(),
                                 "size": cur_size, "bold": cur_bold})

                # Classify runs into headings
                page_headings: list[dict] = []
                for run in runs:
                    text = run["text"]
                    sz = run["size"]
                    bold = run["bold"]

                    # ── Filter out noise: short data values, numbers, etc. ───
                    # Skip very short runs (<3 chars) unless clearly a title
                    if len(text) < 3:
                        continue
                    # Skip runs that are just numbers, dates, percentages
                    stripped = text.strip().rstrip(".,;:!?")
                    if re.match(r'^[\d\s\.\,\$\%\-\/\(\)]+$', stripped):
                        continue
                    # Skip single-word items under 4 chars (likely labels)
                    word_count = len(text.split())

                    tag = None
                    if sz >= body_size + 4:
                        tag = "H1"
                    elif sz >= body_size + 1.5:
                        tag = "H2"
                    elif bold and sz >= body_size - 0.5:
                        # Bold at/near body size → H3 only if it looks like
                        # a heading: at least 2 words, or first char uppercase
                        if word_count >= 2 and text[0].isupper():
                            tag = "H3"
                        elif word_count >= 4:
                            tag = "H3"

                    if tag:
                        # Truncate very long headings to 120 chars for the tree
                        display = text[:120] + ("…" if len(text) > 120 else "")
                        page_headings.append({"tag": tag, "text": display})

                if page_headings:
                    headings_by_page[page_idx] = page_headings

    except Exception:
        return {}  # any error → silent fallback to flat Sect

    return headings_by_page


def add_minimal_structure_tree(pdf: "pikepdf.Pdf",
                               src_path: Path | None = None) -> list[str]:
    """Inject a Tagged PDF structure tree if one is not already present.

    When *src_path* is provided, pdfplumber analyses the original PDF for
    typographic heading cues (font size, bold weight).  If headings are found
    the tree uses real semantic elements:

        StructTreeRoot → Document → per-page Sect → H1 / H2 / H3 / P

    If pdfplumber is unavailable, finds no text, or detects no headings, the
    function falls back to the original flat structure:

        StructTreeRoot → Document → one Sect per page

    MCID content mappings are not written (those require re-rendering the page
    content stream, which is beyond scope here). The structure is present and
    valid per ISO 32000; tools that require MCID linkage will flag it as
    "unverified tagging" rather than "no tagging" — a meaningful improvement.

    Returns a list of fix description strings (empty if tree was already present).
    """
    if "/StructTreeRoot" in pdf.Root:
        return []   # already tagged — leave it alone

    # ── Detect headings: Vision → pdfplumber → flat fallback ─────────────────
    # Priority 1: Claude Vision (most accurate — sees visual layout)
    # Priority 2: pdfplumber font heuristics (fast, free, no API call)
    # Priority 3: flat Sect/P structure (always works, no heading info)
    headings_by_page: dict[int, list[dict]] = {}
    detection_method = "flat"
    if src_path is not None:
        headings_by_page = _detect_headings_vision(src_path)
        if headings_by_page:
            detection_method = "vision"
        else:
            headings_by_page = _detect_headings_pdfplumber(src_path)
            if headings_by_page:
                detection_method = "pdfplumber"

    total_headings = sum(len(v) for v in headings_by_page.values())
    use_semantic = total_headings > 0

    fixes: list[str] = []
    try:
        # ── Parent number tree (required by PDF spec even when initially empty) ──
        parent_tree = pdf.make_indirect(pikepdf.Dictionary(
            Nums=pikepdf.Array()
        ))

        # ── Document element (logical root of the content hierarchy) ────────────
        doc_struct = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/StructElem"),
            S=pikepdf.Name("/Document"),
            K=pikepdf.Array(),
        ))

        # ── Build per-page structure ─────────────────────────────────────────────
        page_count = 0
        heading_count = 0
        for page_idx, page in enumerate(pdf.pages):
            try:
                sect = pdf.make_indirect(pikepdf.Dictionary(
                    Type=pikepdf.Name("/StructElem"),
                    S=pikepdf.Name("/Sect"),
                    P=doc_struct,
                    Pg=page.obj,
                    K=pikepdf.Array(),
                ))

                if use_semantic and page_idx in headings_by_page:
                    # ── Semantic children: H1/H2/H3 + trailing P ────────────
                    for h in headings_by_page[page_idx]:
                        tag_name = h["tag"]   # "H1", "H2", or "H3"
                        elem = pdf.make_indirect(pikepdf.Dictionary(
                            Type=pikepdf.Name("/StructElem"),
                            S=pikepdf.Name(f"/{tag_name}"),
                            P=sect,
                            Pg=page.obj,
                        ))
                        sect["/K"].append(elem)
                        heading_count += 1
                    # Add a P element for body text on this page
                    p_elem = pdf.make_indirect(pikepdf.Dictionary(
                        Type=pikepdf.Name("/StructElem"),
                        S=pikepdf.Name("/P"),
                        P=sect,
                        Pg=page.obj,
                    ))
                    sect["/K"].append(p_elem)
                else:
                    # ── Flat fallback: single P child ───────────────────────
                    p_elem = pdf.make_indirect(pikepdf.Dictionary(
                        Type=pikepdf.Name("/StructElem"),
                        S=pikepdf.Name("/P"),
                        P=sect,
                        Pg=page.obj,
                    ))
                    sect["/K"].append(p_elem)

                doc_struct["/K"].append(sect)
                page_count += 1
            except Exception:
                pass   # skip any page that can't be referenced

        # ── StructTreeRoot ───────────────────────────────────────────────────────
        struct_tree_root = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/StructTreeRoot"),
            K=pikepdf.Array([doc_struct]),
            ParentTree=parent_tree,
            ParentTreeNextKey=pikepdf.Integer(0),
        ))
        doc_struct["/P"] = struct_tree_root

        # ── Attach to document catalog ───────────────────────────────────────────
        pdf.Root["/StructTreeRoot"] = struct_tree_root

        # Ensure MarkInfo marks document as Tagged (set_root_accessibility_flags
        # may have already done this, but set it here too for clarity)
        if "/MarkInfo" not in pdf.Root:
            pdf.Root["/MarkInfo"] = pdf.make_indirect(pikepdf.Dictionary())
        pdf.Root["/MarkInfo"]["/Marked"] = pikepdf.Boolean(True)

        if use_semantic:
            method_label = (
                "Claude Vision" if detection_method == "vision"
                else "pdfplumber font heuristics"
            )
            fixes.append(
                f"Structure tree: StructTreeRoot created with Document → "
                f"{page_count} Sect(s), {heading_count} heading element(s) "
                f"(H1/H2/H3 via {method_label}); document marked as Tagged PDF"
            )
        else:
            fixes.append(
                f"Structure tree: StructTreeRoot created with Document → "
                f"{page_count} Sect/P element(s) (flat — no headings detected); "
                f"document marked as Tagged PDF"
            )
    except Exception as e:
        fixes.append(f"Structure tree: could not create ({e})")

    return fixes


def ensure_document_root(pdf: "pikepdf.Pdf") -> list[str]:
    """PDF/UA requires StructTreeRoot → /Document → everything else.

    Acrobat AutoTag sometimes produces StructTreeRoot → /Part (or other
    elements) directly, with no /Document wrapper.  Screen readers (NVDA,
    JAWS) flag this as inaccessible even when the tagging is otherwise
    correct.

    This function checks whether the immediate children of StructTreeRoot
    are already wrapped in a single /Document element.  If not, it creates
    one and re-parents all existing top-level children under it.

    Safe to call even on documents that already have a /Document root —
    it detects that and returns immediately.
    """
    if "/StructTreeRoot" not in pdf.Root:
        return []

    struct_root = pdf.Root["/StructTreeRoot"]

    # Collect current top-level kids
    raw_kids = struct_root.get("/K")
    if raw_kids is None:
        return []

    # Normalise to a list
    if isinstance(raw_kids, pikepdf.Array):
        top_kids = list(raw_kids)
    else:
        top_kids = [raw_kids]

    # Check whether the sole top-level child is already a /Document
    if len(top_kids) == 1:
        k = top_kids[0]
        if isinstance(k, pikepdf.Dictionary):
            if str(k.get("/S", "")) == "/Document":
                return []   # already correctly structured

    # Build a /Document wrapper and move all top-level kids into it
    doc_node = pikepdf.Dictionary(
        Type=pikepdf.Name("/StructElem"),
        S=pikepdf.Name("/Document"),
        P=struct_root.objgen if hasattr(struct_root, "objgen") else struct_root,
        K=pikepdf.Array(top_kids),
    )
    # Make it an indirect object so children can reference it as /P
    doc_indirect = pdf.make_indirect(doc_node)

    # Update each child's /P pointer to the new Document node
    for kid in top_kids:
        if isinstance(kid, pikepdf.Dictionary):
            try:
                kid["/P"] = doc_indirect
            except Exception:
                pass

    # Replace StructTreeRoot's kids with the single Document node
    struct_root["/K"] = doc_indirect

    return ["Structure tree: wrapped top-level elements under /Document node (PDF/UA compliance)"]


def apply_pikepdf_remediations(
    src_path: Path, dst_path: Path, title: str
) -> tuple[list[str], list[str]]:
    """Open the source PDF, apply all pikepdf fixes, save to dst_path.

    Processing order:
      0. Detect pre-AutoTagged files: if src already has a StructTreeRoot
         (from _autotag_acrobat_gui.py), skip GS entirely and pass
         src_path=None to add_minimal_structure_tree so it exits early.
         GS would destroy MCID content mappings that AutoTag built.
      1. Ghostscript font embedding (src → tmp) — only for un-tagged files.
         Safe because those files have no structure tree yet.
      2. pikepdf opens tmp (or src) and applies:
           - XMP metadata + DocInfo
           - /Lang, MarkInfo, ViewerPreferences
           - Structure tree (skipped when pre-tagged; Vision/pdfplumber otherwise)
           - Tab order
           - Form tooltips
           - Outline bookmarks from structure tree
           - Table header fixes
      3. Saves to dst_path.  tmp is deleted on completion.

    Reads from src_path (downloads/); never modifies it.
    Returns (fixes_applied: list[str], errors: list[str]).
    """
    fixes: list[str] = []
    errors: list[str] = []

    # ── Detect pre-AutoTagged files ───────────────────────────────────────────
    # If the source PDF already has a StructTreeRoot (e.g. from running
    # _autotag_acrobat_gui.py), we MUST skip Ghostscript.  GS rewrites the
    # PDF from scratch and would destroy every MCID content-mapping that
    # Acrobat's AutoTag carefully built.  The font-embedding step is also
    # unnecessary because _autotag_acrobat_gui.py runs GS before AutoTag.
    _src_already_tagged = False
    try:
        with pikepdf.open(str(src_path)) as _chk:
            _src_already_tagged = "/StructTreeRoot" in _chk.Root
    except Exception:
        pass

    # ── Step 1: Ghostscript font embedding (non-destructive, src → tmp) ──────
    gs_tmp: Path | None = None
    pikepdf_src = src_path   # default: open original if GS not available/failed
    if _src_already_tagged:
        # Source was pre-processed by _autotag_acrobat_gui.py — skip GS entirely
        fixes.append(
            "Font embedding: skipped — source has existing structure tree "
            "(AutoTag'd by _autotag_acrobat_gui.py); GS would destroy MCID mappings"
        )
    elif _GS_EXE:
        gs_tmp = dst_path.parent / (src_path.stem + "_gs_tmp.pdf")
        gs_ok, gs_msg = embed_fonts_gs_to_temp(src_path, gs_tmp)
        if gs_ok:
            fixes.append(f"Font embedding: {gs_msg}")
            pikepdf_src = gs_tmp
        else:
            if gs_msg:  # empty string = GS not found (silent)
                errors.append(f"Font embedding: {gs_msg} — proceeding without")
            if gs_tmp.exists():
                try: gs_tmp.unlink()
                except Exception: pass
            gs_tmp = None
    else:
        fixes.append("Font embedding: Ghostscript not found — skipped (fonts may already be embedded)")

    # ── Step 2: pikepdf fixes (structure tree, metadata, etc.) ───────────────
    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with pikepdf.open(str(pikepdf_src)) as pdf:
            fixes += apply_xmp_and_docinfo(pdf, title)
            fixes += set_root_accessibility_flags(pdf)
            # add_minimal_structure_tree skips automatically when StructTreeRoot
            # already exists — passing src_path=None when pre-tagged avoids an
            # unnecessary pdfplumber/Vision scan of a document we aren't tagging.
            fixes += add_minimal_structure_tree(
                pdf,
                src_path=None if _src_already_tagged else src_path
            )
            fixes += ensure_document_root(pdf)
            fixes += set_tab_order(pdf)
            tooltip_fixes = fix_form_tooltips(pdf)
            if tooltip_fixes:
                fixes += tooltip_fixes
                fixes.append(
                    f"Form tooltips: {len(tooltip_fixes)} field(s) given /TU tooltip text"
                )
            fixes += generate_bookmarks_from_structure(pdf, src_path)
            fixes += fix_table_headers(pdf)
            pdf.save(str(dst_path))
    except Exception as e:
        errors.append(f"pikepdf remediation: {e}")
    finally:
        # ── Step 3: clean up GS temp file ────────────────────────────────────
        if gs_tmp and gs_tmp.exists():
            try: gs_tmp.unlink()
            except Exception: pass

    return fixes, errors


# ── Alt text generation ───────────────────────────────────────────────────────

def get_page_image_map(pdf_path: Path) -> dict[int, list[tuple[str, int, int]]]:
    """Scan XObject resources and map page numbers to substantive images.

    Returns {page_num (0-indexed): [(xobj_name, width, height), ...]}.
    Only includes images at or above IMAGE_MIN_SIZE in both dimensions.
    """
    page_map: dict[int, list] = {}
    try:
        with pikepdf.open(str(pdf_path)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                resources = page.get("/Resources")
                if resources is None:
                    continue
                xobjects = resources.get("/XObject", {})
                for name in xobjects:
                    try:
                        xobj = xobjects[name]
                        if str(xobj.get("/Subtype", "")) != "/Image":
                            continue
                        w = int(xobj.get("/Width", 0))
                        h = int(xobj.get("/Height", 0))
                        if w >= IMAGE_MIN_SIZE and h >= IMAGE_MIN_SIZE:
                            page_map.setdefault(page_num, []).append((str(name), w, h))
                    except Exception:
                        pass
    except Exception:
        pass
    return page_map


_MAX_API_IMAGE_PX = 7900  # Anthropic API rejects images with any dimension > 8000px


def render_page_jpeg(pdf_path: Path, page_num: int) -> bytes | None:
    """Render a single PDF page to JPEG bytes at 150 DPI.

    Uses pdf2image (Poppler). Returns JPEG bytes, or None on failure.
    Page number is 0-indexed; pdf2image uses 1-indexed page numbers.

    The rendered image is capped at _MAX_API_IMAGE_PX on its longest side
    before encoding, so large-format PDFs (maps, posters, engineering drawings)
    don't trigger the Anthropic API's 8000-pixel dimension limit.
    """
    if not PDF2IMAGE_AVAILABLE:
        return None
    try:
        pages = convert_from_path(
            str(pdf_path),
            dpi=150,
            first_page=page_num + 1,
            last_page=page_num + 1,
            poppler_path=POPPLER_PATH,
            fmt="jpeg",
        )
        if not pages:
            return None
        img = pages[0]
        # Downscale if either dimension exceeds the API limit
        w, h = img.size
        if w > _MAX_API_IMAGE_PX or h > _MAX_API_IMAGE_PX:
            scale = _MAX_API_IMAGE_PX / max(w, h)
            img = img.resize(
                (int(w * scale), int(h * scale)),
                PILImage.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None


def _check_raise_billing_error(exc: Exception) -> None:
    """Inspect an exception from the Anthropic SDK.

    If it looks like a billing / credit-exhaustion error (HTTP 400/402 with
    a message about credits or billing) set the module-level
    _api_credits_exhausted flag and raise APICreditsExhaustedError so the
    main loop can stop cleanly rather than continuing without AI.

    Ordinary transient errors (timeouts, bad images, rate limits) are left
    for the caller to handle as before.
    """
    global _api_credits_exhausted
    msg = str(exc).lower()

    # Exclude known non-billing 400 errors so they don't halt the pipeline.
    # Anthropic returns HTTP 400 for both billing errors AND invalid-request
    # errors (e.g. image dimensions too large, token limit exceeded).
    non_billing_phrases = (
        "image dimensions",
        "max allowed size",
        "max_tokens",
        "token",
        "context_length",
        "invalid_request_error",
        "content_policy",
    )
    if any(phrase in msg for phrase in non_billing_phrases):
        return  # Not a billing error — let the caller handle it normally

    billing_keywords = ("credit", "billing", "balance", "payment", "quota",
                        "insufficient_quota", "credit_balance")
    is_billing = any(kw in msg for kw in billing_keywords)
    # HTTP 402 is always a billing error.
    # HTTP 400 is only treated as billing when billing keywords are present
    # (to avoid mis-classifying invalid-request errors as billing errors).
    status = getattr(exc, "status_code", None)
    if status == 402:
        is_billing = True
    if is_billing:
        _api_credits_exhausted = True
        raise APICreditsExhaustedError(
            f"Anthropic API credit/billing error — stopping pipeline so progress "
            f"is saved. Top up credits then re-run to continue. ({exc})"
        )


def call_claude_alt_text(jpeg_bytes: bytes) -> str:
    """Call Claude Haiku with a rendered page image and return alt text.

    The model is prompted to either:
      - Return 'DECORATIVE' for logos, seals, dividers, backgrounds
      - Return a 1-2 sentence screen-reader description

    Returns an empty string on API failure.
    """
    if _anthropic_client is None or _api_credits_exhausted:
        return ""
    try:
        img_b64 = base64.standard_b64encode(jpeg_bytes).decode()
        response = _anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            timeout=45.0,   # fail fast rather than hanging indefinitely
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "You are generating alt text for a government PDF document. "
                            "Classify this image as DECORATIVE (if it is a logo, seal, "
                            "divider, background, or purely aesthetic element) or provide "
                            "a 1-2 sentence screen-reader description focused on the "
                            "information conveyed. Begin your response with either "
                            "'DECORATIVE' or the description directly."
                        ),
                    },
                ],
            }],
        )
        return response.content[0].text.strip()
    except Exception as e:
        _check_raise_billing_error(e)   # re-raises APICreditsExhaustedError if billing
        return f"[API error: {e}]"


def _embed_alt_in_figures(
    pdf: pikepdf.Pdf, page_alt_map: dict[int, list[str]]
) -> int:
    """Walk StructTreeRoot and apply alt text to Figure elements.

    Matches Figure elements to pages via their /Pg reference. For each page
    with queued alt texts, pops one text per Figure element encountered.
    DECORATIVE text produces an empty /Alt string; all other text is embedded.

    Returns the number of Figure elements updated.
    """
    if "/StructTreeRoot" not in pdf.Root:
        return 0

    # Build a map from page object identity to page index
    rev_pages: dict[tuple, int] = {}
    for i, page in enumerate(pdf.pages):
        try:
            rev_pages[page.objgen] = i
        except Exception:
            pass

    count = [0]

    def walk(elem: object, current_page: int | None) -> None:
        """Recursively walk a structure element and apply alt text."""
        if not isinstance(elem, pikepdf.Dictionary):
            return

        # Update current page tracking from /Pg reference
        pg = elem.get("/Pg")
        if pg is not None:
            try:
                p_idx = rev_pages.get(pg.objgen)
                if p_idx is not None:
                    current_page = p_idx
            except Exception:
                pass

        s_type = str(elem.get("/S", ""))
        if s_type == "/Figure":
            if current_page is not None and current_page in page_alt_map:
                queue = page_alt_map[current_page]
                if queue:
                    alt = queue.pop(0)
                    elem["/Alt"] = pikepdf.String(
                        "" if alt.upper().startswith("DECORATIVE") else alt
                    )
                    count[0] += 1

        kids = elem.get("/K")
        if kids is None:
            return
        if isinstance(kids, pikepdf.Array):
            for kid in kids:
                walk(kid, current_page)
        elif isinstance(kids, pikepdf.Dictionary):
            walk(kids, current_page)

    walk(pdf.Root["/StructTreeRoot"], None)
    return count[0]


def process_alt_text(
    out_path: Path, logs_dir: Path, filename: str
) -> tuple[int, list[str]]:
    """Generate and embed alt text for all substantive images in out_path.

    Pipeline:
      1. Find pages with substantive XObject images
      2. Render each such page to JPEG at 150 DPI
      3. MD5-hash the JPEG — skip API call if hash is cached (repeated seals)
      4. Call Claude Haiku for a description or DECORATIVE classification
      5. Embed /Alt on Figure elements in the structure tree
      6. Write a sidecar audit file to logs_dir

    Returns (images_processed: int, log_entries: list[str]).
    """
    log_entries: list[str] = []

    if not PDF2IMAGE_AVAILABLE:
        log_entries.append("ALT TEXT: pdf2image unavailable — skipped (install pdf2image and Pillow)")
        return 0, log_entries

    if _anthropic_client is None:
        log_entries.append("ALT TEXT: Anthropic API not configured — skipped (set ANTHROPIC_API_KEY)")
        return 0, log_entries

    page_image_map = get_page_image_map(out_path)
    if not page_image_map:
        log_entries.append("ALT TEXT: No substantive images found — skipped")
        return 0, log_entries

    # Build per-page queues of alt text strings
    page_alt_queues: dict[int, list[str]] = {}
    sidecar_lines = [
        f"Alt Text Audit — {filename}",
        f"Generated : {TODAY}",
        f"County    : {COUNTY_NAME}",
        "=" * 60,
        "",
    ]

    images_processed = 0
    cache_hits = 0

    for page_num in sorted(page_image_map):
        imgs = page_image_map[page_num]

        # Render the page
        jpeg_bytes = render_page_jpeg(out_path, page_num)
        if jpeg_bytes is None:
            sidecar_lines.append(f"Page {page_num + 1}: render failed — skipped")
            continue

        # Check cache to avoid duplicate Claude calls for repeated imagery
        md5 = hashlib.md5(jpeg_bytes).hexdigest()
        if md5 in _alt_cache:
            alt_text = _alt_cache[md5]
            cache_hits += 1
        else:
            alt_text = call_claude_alt_text(jpeg_bytes)
            _alt_cache[md5] = alt_text

        for xobj_name, w, h in imgs:
            page_alt_queues.setdefault(page_num, []).append(alt_text)
            sidecar_lines.append(f"Page {page_num + 1}  |  Image: {xobj_name} ({w}×{h}px)")
            if alt_text.upper().startswith("DECORATIVE"):
                sidecar_lines.append("  Classification : DECORATIVE — empty /Alt, artifact")
            elif alt_text.startswith("[API error"):
                sidecar_lines.append(f"  Error          : {alt_text}")
            else:
                sidecar_lines.append(f"  Alt text       : {alt_text}")
            sidecar_lines.append("")
            images_processed += 1

    # Embed alt text into the structure tree and save
    figures_updated = 0
    if page_alt_queues:
        try:
            with pikepdf.open(str(out_path), allow_overwriting_input=True) as pdf:
                figures_updated = _embed_alt_in_figures(pdf, page_alt_queues)
                pdf.save(str(out_path))
        except Exception as e:
            log_entries.append(f"ALT TEXT: Failed to embed ({e})")

    log_entries.append(
        f"ALT TEXT: {images_processed} image(s) processed, "
        f"{figures_updated} Figure element(s) updated, "
        f"{cache_hits} cache hit(s)"
    )

    # Write sidecar audit file
    sidecar_path = logs_dir / f"{Path(filename).stem}_alt_text.txt"
    sidecar_path.write_text("\n".join(sidecar_lines), encoding="utf-8")
    log_entries.append(f"ALT TEXT: Audit file saved → {sidecar_path.name}")

    return images_processed, log_entries


# ── Color contrast verification ──────────────────────────────────────────────

# Per-session contrast cache: md5(jpeg_bytes) → "PASS" or "FAIL: reason"
_contrast_cache: dict[str, str] = {}

# Per-session heading cache: md5(jpeg_bytes) → [(level, text), ...]
_heading_cache: dict[str, list] = {}


def _pdf_str_to_python(s: object) -> str:
    """Decode a pikepdf.String to a Python str, handling UTF-16BE and Latin-1."""
    try:
        raw = bytes(s)  # type: ignore[arg-type]
        if raw[:2] == b"\xfe\xff":
            return raw[2:].decode("utf-16-be", errors="replace")
        return raw.decode("latin-1", errors="replace")
    except Exception:
        return str(s)


def _extract_page_mcid_texts(page: "pikepdf.Page") -> "dict[int, str]":
    """Parse a PDF page's content stream and return {mcid: text} for every
    marked-content block.  Used by tag_headings_from_claude() to match
    Claude-identified heading text back to struct-tree elements.
    """
    mcid_texts: dict[int, str] = {}
    current_mcid: "int | None" = None
    current_text: list[str] = []

    def _flush() -> None:
        nonlocal current_mcid, current_text
        if current_mcid is not None and current_text:
            mcid_texts[current_mcid] = " ".join(current_text).strip()
        current_mcid = None
        current_text = []

    try:
        for operands, operator in pikepdf.parse_content_stream(page):
            op = str(operator)
            if op == "BDC":
                _flush()
                for operand in operands:
                    if isinstance(operand, pikepdf.Dictionary):
                        mcid_val = operand.get("/MCID")
                        if mcid_val is not None:
                            try:
                                current_mcid = int(mcid_val)
                            except Exception:
                                pass
            elif op == "EMC":
                _flush()
            elif op in ("Tj", "'"):
                if current_mcid is not None and operands:
                    s = operands[0]
                    if isinstance(s, pikepdf.String):
                        decoded = _pdf_str_to_python(s).strip()
                        if decoded:
                            current_text.append(decoded)
            elif op == '"':
                if current_mcid is not None and len(operands) >= 3:
                    s = operands[2]
                    if isinstance(s, pikepdf.String):
                        decoded = _pdf_str_to_python(s).strip()
                        if decoded:
                            current_text.append(decoded)
            elif op == "TJ":
                if current_mcid is not None and operands:
                    arr = operands[0]
                    if isinstance(arr, pikepdf.Array):
                        for item in arr:
                            if isinstance(item, pikepdf.String):
                                decoded = _pdf_str_to_python(item).strip()
                                if decoded:
                                    current_text.append(decoded)
    except Exception:
        pass

    _flush()
    return mcid_texts


def _elem_mcids(elem: "pikepdf.Dictionary") -> "list[int]":
    """Return all MCID integers referenced by a struct element's /K children.

    Handles three /K child types:
    - Direct integer (rare but valid)
    - Marked content reference dictionary (/Type /MCR, /MCID n)
    - pikepdf.Integer objects
    """
    mcids: list[int] = []

    def _collect(k: object) -> None:
        if isinstance(k, int):
            mcids.append(k)
        elif isinstance(k, pikepdf.Array):
            for item in k:
                _collect(item)
        elif isinstance(k, pikepdf.Dictionary):
            mcid_val = k.get("/MCID")
            if mcid_val is not None:
                try:
                    mcids.append(int(mcid_val))
                except Exception:
                    pass
        else:
            # Catch pikepdf.Integer or other numeric-like objects
            try:
                mcids.append(int(k))  # type: ignore[arg-type]
            except Exception:
                pass

    kids = elem.get("/K")
    if kids is not None:
        _collect(kids)
    return mcids


def _call_claude_contrast(jpeg_bytes: bytes) -> str:
    """Ask Claude Haiku whether a single rendered page meets 4.5:1 contrast.

    Returns 'PASS' or 'FAIL: [brief reason]'.
    """
    if _anthropic_client is None or _api_credits_exhausted:
        return "FAIL: Claude unavailable"
    try:
        img_b64 = base64.standard_b64encode(jpeg_bytes).decode()
        response = _anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            timeout=30.0,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "You are a WCAG 2.1 color contrast auditor. "
                            "Examine all visible text on this PDF page. "
                            "Does ALL text meet the minimum 4.5:1 contrast ratio "
                            "against its background? "
                            "Reply with ONLY the word PASS if all text passes. "
                            "Reply with FAIL: followed by a brief (under 20 words) "
                            "description of the problem if any text fails."
                        ),
                    },
                ],
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        _check_raise_billing_error(exc)
        return f"FAIL: API error ({str(exc)[:50]})"


def check_color_contrast(pdf_path: Path, page_count: int) -> tuple[bool, str]:
    """Sample up to 3 pages and verify WCAG 4.5:1 contrast via Claude Vision.

    Pages sampled: first, middle, last (deduplicated for short documents).
    Uses the MD5 page-image cache so identical pages cost only one API call.

    Returns (all_passed: bool, detail_summary: str).
    Returns (False, "skipped ...") when Claude or pdf2image is unavailable.
    """
    if _anthropic_client is None or not PDF2IMAGE_AVAILABLE:
        return False, "skipped — Claude or pdf2image not available"
    if page_count <= 0:
        return False, "skipped — no pages"

    sample_indices = sorted(set([0, page_count // 2, page_count - 1]))

    all_pass = True
    details: list[str] = []

    for pg_idx in sample_indices:
        jpeg_bytes = render_page_jpeg(pdf_path, pg_idx)
        if jpeg_bytes is None:
            details.append(f"p{pg_idx + 1}:skip")
            continue

        md5 = hashlib.md5(jpeg_bytes).hexdigest()
        if md5 in _contrast_cache:
            result = _contrast_cache[md5]
        else:
            result = _call_claude_contrast(jpeg_bytes)
            _contrast_cache[md5] = result

        if result.upper().startswith("PASS"):
            details.append(f"p{pg_idx + 1}:PASS")
        else:
            all_pass = False
            details.append(f"p{pg_idx + 1}:{result[:40]}")

    return all_pass, "; ".join(details)


# ── Heading detection via Claude Vision ──────────────────────────────────────

def call_claude_identify_headings(jpeg_bytes: bytes) -> "list[tuple[str, str]]":
    """Ask Claude Haiku to identify visually prominent headings on a PDF page.

    Returns a list of (level, text) tuples where level is 'H1', 'H2', or 'H3'.
    Claude is asked to reply LEVEL|TEXT per heading, or NONE if no headings found.
    Returns an empty list on failure or when no headings are visible.
    """
    if _anthropic_client is None or _api_credits_exhausted:
        return []
    try:
        img_b64 = base64.standard_b64encode(jpeg_bytes).decode()
        response = _anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=400,
            timeout=30.0,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "You are analyzing a government PDF document page for heading structure. "
                            "Identify any text that visually appears to be a heading — based on "
                            "larger font size, bold weight, ALL-CAPS styling, or prominent placement. "
                            "For each heading found, reply with exactly one line per heading: "
                            "LEVEL|TEXT  where LEVEL is H1 (document title or major section), "
                            "H2 (subsection), or H3 (minor heading), "
                            "and TEXT is the exact heading text as it appears on the page. "
                            "Reply NONE if no headings are visible on this page. "
                            "Reply ONLY with heading lines or NONE — no explanation, no punctuation."
                        ),
                    },
                ],
            }],
        )
        result = response.content[0].text.strip()
        if not result or result.upper() == "NONE":
            return []
        headings: list[tuple[str, str]] = []
        for line in result.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            parts = line.split("|", 1)
            level = parts[0].strip().upper()
            text  = parts[1].strip()
            if level in ("H1", "H2", "H3") and text:
                headings.append((level, text))
        return headings
    except Exception as exc:
        _check_raise_billing_error(exc)
        return []


def _detect_headings_vision(src_path: Path) -> dict[int, list[dict]]:
    """Use Claude Haiku Vision to detect headings on every page of a PDF.

    Renders each page to JPEG via Poppler (pdf2image), then asks Claude to
    identify visually prominent headings (H1/H2/H3) based on font size, bold
    weight, ALL-CAPS styling, and layout prominence.

    This is called from add_minimal_structure_tree() to build heading elements
    directly into the structure tree — no MCID content-mapping required.

    Returns {page_index: [{"tag": "H1"|"H2"|"H3", "text": "..."}, ...]}.
    Empty dict when Claude or pdf2image are unavailable, or on any error —
    callers must always fall back gracefully.
    """
    if _anthropic_client is None or not PDF2IMAGE_AVAILABLE:
        return {}
    if _api_credits_exhausted:
        return {}
    if not src_path.exists():
        return {}

    headings_by_page: dict[int, list[dict]] = {}
    try:
        with pikepdf.open(str(src_path)) as _pdf_tmp:
            page_count = len(_pdf_tmp.pages)

        for pg_idx in range(page_count):
            jpeg_bytes = render_page_jpeg(src_path, pg_idx)
            if jpeg_bytes is None:
                continue

            # MD5 cache — avoids re-calling the API on repeated pipeline runs
            md5 = hashlib.md5(jpeg_bytes).hexdigest()
            if md5 in _heading_cache:
                raw = _heading_cache[md5]
            else:
                raw = call_claude_identify_headings(jpeg_bytes)
                _heading_cache[md5] = raw

            if raw:
                headings_by_page[pg_idx] = [
                    {"tag": level, "text": text} for level, text in raw
                ]

    except APICreditsExhaustedError:
        raise   # propagate so the main loop can stop cleanly
    except Exception:
        return {}   # any other error → silent fallback

    return headings_by_page


def tag_headings_from_claude(pdf_path: Path, page_count: int) -> "tuple[list[str], list[str]]":
    """Re-tag /P struct elements as H1/H2/H3 using Claude Vision heading detection.

    Only runs when:
    - Claude and pdf2image are available
    - The remediated PDF already exists at pdf_path
    - page_count > 0

    Process:
    1. Render the first 3 pages to JPEG (where most heading structure lives)
    2. Ask Claude Haiku to identify visually prominent headings with their levels
    3. Build a text→(level, page) lookup from Claude's response
    4. Parse each page's content stream for marked-content MCID blocks
    5. Walk the structure tree; for each /P element whose text matches a Claude
       heading, re-tag it as /H1, /H2, or /H3
    6. Save the modified PDF in-place (allow_overwriting_input=True)

    Returns (fixes: list[str], errors: list[str]).
    """
    fixes:  list[str] = []
    errors: list[str] = []

    if _anthropic_client is None or not PDF2IMAGE_AVAILABLE:
        return fixes, errors
    if page_count <= 0 or not pdf_path.exists():
        return fixes, errors

    # Step 1: render sampled pages and collect Claude heading lists
    sample_count = min(3, page_count)
    page_headings: dict[int, list[tuple[str, str]]] = {}

    for pg_idx in range(sample_count):
        jpeg_bytes = render_page_jpeg(pdf_path, pg_idx)
        if jpeg_bytes is None:
            continue
        md5 = hashlib.md5(jpeg_bytes).hexdigest()
        if md5 in _heading_cache:
            headings = _heading_cache[md5]
        else:
            headings = call_claude_identify_headings(jpeg_bytes)
            _heading_cache[md5] = headings
        if headings:
            page_headings[pg_idx] = headings

    if not page_headings:
        return fixes, errors  # Claude found no headings — nothing to do

    # Flatten into a normalized text → (level, page_idx) lookup
    heading_lookup: dict[str, tuple[str, int]] = {}
    for pg_idx, headings in page_headings.items():
        for level, text in headings:
            key = re.sub(r"\s+", " ", text.lower().strip())
            if key and key not in heading_lookup:
                heading_lookup[key] = (level, pg_idx)

    tagged_count = 0
    try:
        with pikepdf.open(str(pdf_path), allow_overwriting_input=True) as pdf:
            if "/StructTreeRoot" not in pdf.Root:
                return fixes, errors

            # Build page objgen → 0-based index map
            page_index: dict = {}
            for i, page in enumerate(pdf.pages):
                try:
                    page_index[page.objgen] = i
                except Exception:
                    pass

            # Parse content streams for the sampled pages only
            page_mcid_maps: dict[int, dict[int, str]] = {}
            for pg_idx in page_headings:
                if 0 <= pg_idx < len(pdf.pages):
                    try:
                        page_mcid_maps[pg_idx] = _extract_page_mcid_texts(
                            pdf.pages[pg_idx]
                        )
                    except Exception:
                        page_mcid_maps[pg_idx] = {}

            def walk_and_tag(elem: object, cur_page: "int | None", depth: int = 0) -> None:
                nonlocal tagged_count
                if depth > 30 or not isinstance(elem, pikepdf.Dictionary):
                    return

                # Track the current page via /Pg references
                pg_ref = elem.get("/Pg")
                if pg_ref is not None:
                    try:
                        cur_page = page_index.get(pg_ref.objgen, cur_page)
                    except Exception:
                        pass

                s_type = str(elem.get("/S", ""))
                if s_type == "/P" and cur_page in page_mcid_maps:
                    mcids    = _elem_mcids(elem)
                    mcid_map = page_mcid_maps[cur_page]
                    elem_text = " ".join(
                        mcid_map[m] for m in mcids if m in mcid_map
                    ).strip()
                    if elem_text:
                        norm_key = re.sub(r"\s+", " ", elem_text.lower().strip())
                        match = heading_lookup.get(norm_key)
                        # Fallback: try a 40-character prefix match for truncated text
                        if match is None:
                            for hkey, hval in heading_lookup.items():
                                short = min(40, len(hkey), len(norm_key))
                                if short > 5 and norm_key[:short] == hkey[:short]:
                                    match = hval
                                    break
                        if match:
                            level_str, _ = match
                            try:
                                elem["/S"] = pikepdf.Name(f"/{level_str}")
                                tagged_count += 1
                            except Exception:
                                pass

                kids = elem.get("/K")
                if kids is None:
                    return
                if isinstance(kids, pikepdf.Array):
                    for kid in kids:
                        walk_and_tag(kid, cur_page, depth + 1)
                elif isinstance(kids, pikepdf.Dictionary):
                    walk_and_tag(kids, cur_page, depth + 1)

            walk_and_tag(pdf.Root["/StructTreeRoot"], None)

            if tagged_count > 0:
                pdf.save(str(pdf_path))
                fixes.append(
                    f"Headings: {tagged_count} /P element(s) re-tagged as H1/H2/H3 "
                    f"via Claude Vision analysis of first {sample_count} page(s)"
                )
    except Exception as exc:
        errors.append(f"Heading tagging failed: {exc}")

    return fixes, errors


# ── Compliance scoring ────────────────────────────────────────────────────────

def score_document(
    issues: dict, page_count: int
) -> tuple[int, str, list[str]]:
    """Calculate a 0–100 compliance score and derive a letter grade.

    Starts at 100 and deducts points for each unresolved issue.
    Also generates specific, actionable manual review instructions.

    Returns (score: int, grade: str, manual_review_items: list[str]).
    """
    score = 100
    manual_items: list[str] = []

    # Critical deductions
    if not issues.get("has_structure_tree"):
        score -= DEDUCTIONS["no_structure_tree"]
        manual_items.append(
            "Structure tree missing: open in Acrobat Pro "
            "→ Tools → Accessibility → Autotag Document (for full MCID tagging)"
        )

    if not issues.get("has_text_layer"):
        score -= DEDUCTIONS["no_text_layer"]
        manual_items.append(
            "No text layer: open in Acrobat Pro "
            "→ Tools → Scan & OCR → Recognize Text → In This File"
        )

    # High deductions
    if issues.get("images_without_alt", 0) > 0 and issues.get("has_images"):
        score -= DEDUCTIONS["missing_alt_text"]
        n = issues["images_without_alt"]
        manual_items.append(
            f"{n} image(s) still missing alt text: open in Acrobat Pro "
            "→ Tags panel → right-click each Figure element → Properties → Alt Text"
        )

    if issues.get("form_fields_without_tooltips", 0) > 0:
        n_missing = issues["form_fields_without_tooltips"]
        score -= DEDUCTIONS["form_no_tooltips"]
        manual_items.append(
            f"{n_missing} form field(s) still missing tooltip text: open in Acrobat Pro "
            "→ Tools → Prepare Form → right-click each field → Properties → Tooltip "
            "(pipeline set /TU = field name as a baseline; replace with plain-English label)"
        )

    # Medium deductions
    if not issues.get("has_headings") and page_count > 10:
        score -= DEDUCTIONS["missing_headings"]
        manual_items.append(
            "No heading structure detected: open in Acrobat Pro "
            "→ Tags panel → re-tag content using H1/H2/H3 structure elements"
        )

    if not issues.get("has_bookmarks") and page_count > 9:
        score -= DEDUCTIONS["missing_bookmarks"]
        manual_items.append(
            f"No bookmarks on {page_count}-page document: open in Acrobat Pro "
            "→ Tools → Edit PDF → Bookmarks → New Bookmark from Structure"
        )

    unembedded = issues.get("unembedded_fonts", [])
    if unembedded:
        score -= DEDUCTIONS["unembedded_fonts"]
        font_list = ", ".join(unembedded[:3]) + ("…" if len(unembedded) > 3 else "")
        manual_items.append(
            f"Possibly unembedded fonts ({font_list}): open in Acrobat Pro "
            "→ Tools → Print Production → Preflight → Embed fonts profile"
        )

    if issues.get("vague_links"):
        score -= DEDUCTIONS["vague_hyperlinks"]
        manual_items.append(
            "Vague hyperlink text detected: review links with text like 'click here', "
            "'here', or 'read more' — replace with descriptive anchor text"
        )

    # Color contrast — deduct only when Claude Vision explicitly flags a failure.
    # Documents that pass get no deduction and no manual review item.
    if issues.get("contrast_check_passed"):
        pass  # Claude Vision confirmed passing — no action needed
    else:
        score -= DEDUCTIONS["color_contrast"]
        manual_items.append(
            "Color contrast issue detected: confirm 4.5:1 minimum "
            "ratio using Acrobat Accessibility Checker or the free Color Contrast Analyzer tool"
        )

    if issues.get("tables_without_headers", 0) > 0:
        n_tbls = issues["tables_without_headers"]
        score -= DEDUCTIONS["complex_tables"]
        manual_items.append(
            f"{n_tbls} table(s) still need header review: open in Acrobat Pro "
            "→ Tags panel → verify TH/TD structure and /Scope on header cells "
            "(pipeline promoted first-row TDs to TH as a baseline)"
        )

    score = max(0, score)

    # Determine grade from thresholds
    grade = "F"
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            grade = letter
            break

    return score, grade, manual_items


# ── Remediation log ───────────────────────────────────────────────────────────

def write_log(
    log_path: Path,
    filename: str,
    issues: dict,
    fixes: list[str],
    alt_log_entries: list[str],
    manual_items: list[str],
    score: int,
    grade: str,
) -> None:
    """Write a structured four-section remediation log for one document.

    Sections:
        HEADER           — filename, date, score, grade
        ISSUES FOUND     — every accessibility gap detected
        ISSUES FIXED     — every automated fix applied
        MANUAL REVIEW    — specific actionable instructions for human reviewer
    """
    page_count = issues.get("page_count", 0)

    lines = [
        "=" * 70,
        f"ADA REMEDIATION LOG  —  {COUNTY_NAME}",
        "=" * 70,
        f"File             : {filename}",
        f"Date             : {TODAY}",
        f"Pages            : {page_count}",
        f"Compliance Score : {score}/100",
        f"Compliance Grade : {grade}",
        "",

        "─" * 70,
        "ISSUES FOUND",
        "─" * 70,
    ]

    issue_count = 0

    def flag(label: str, severity: str, condition: bool) -> None:
        nonlocal issue_count
        if condition:
            lines.append(f"  [{severity:<8}] {label}")
            issue_count += 1

    flag("No structure tree — autotag did not complete",
         "CRITICAL", not issues.get("has_structure_tree"))
    flag("No text layer — document may still be a scanned image",
         "CRITICAL", not issues.get("has_text_layer"))

    if issues.get("images_without_alt", 0) > 0 and issues.get("has_images"):
        lines.append(f"  [HIGH    ] {issues['images_without_alt']} image(s) missing alt text")
        issue_count += 1

    if issues.get("form_fields_without_tooltips", 0) > 0:
        lines.append(
            f"  [HIGH    ] {issues['form_fields_without_tooltips']} form field(s) — "
            "tooltip set to field name; manual review recommended"
        )
        issue_count += 1

    flag("Missing heading structure",
         "MEDIUM", not issues.get("has_headings") and page_count > 2)
    flag(f"No bookmarks on {page_count}-page document",
         "MEDIUM", not issues.get("has_bookmarks") and page_count > 9)

    unembedded = issues.get("unembedded_fonts", [])
    if unembedded:
        lines.append(f"  [MEDIUM  ] Possibly unembedded fonts: {', '.join(unembedded[:5])}")
        issue_count += 1

    if issues.get("vague_links"):
        lines.append(f"  [MEDIUM  ] Vague hyperlink text: {issues['vague_links'][:3]}")
        issue_count += 1

    if issues.get("contrast_check_passed"):
        lines.append("  [PASS    ] Color contrast — AI-verified on sampled pages (4.5:1 met)")
    else:
        lines.append("  [LOW     ] Color contrast requires manual verification")
        issue_count += 1

    if issues.get("tables_without_headers", 0) > 0:
        lines.append(
            f"  [LOW     ] {issues['tables_without_headers']} table(s) — "
            "first row promoted to TH; manual scope verification recommended"
        )
        issue_count += 1
    elif issues.get("has_tables"):
        lines.append("  [PASS    ] Table(s) present — first-row TH headers confirmed")

    if issue_count == 0:
        lines.append("  No issues detected.")

    lines += [
        "",
        "─" * 70,
        "ISSUES FIXED (automated)",
        "─" * 70,
    ]

    all_fixes = fixes + alt_log_entries
    if all_fixes:
        for fix in all_fixes:
            lines.append(f"  ✓ {fix}")
    else:
        lines.append("  (no automated fixes applied)")

    lines += [
        "",
        "─" * 70,
        "MANUAL REVIEW NEEDED",
        "─" * 70,
    ]

    if manual_items:
        for i, item in enumerate(manual_items, start=1):
            # Wrap long lines at 65 chars for readability
            lines.append(f"  {i}. {item}")
    else:
        lines.append("  None — document passed all automated checks.")

    lines += ["", "=" * 70]

    log_path.write_text("\n".join(lines), encoding="utf-8")


# ── Main entry point ──────────────────────────────────────────────────────────

def main():
    """Run Pass 2: Python PDF remediation on all eligible documents."""

    print()
    print("═" * 62)
    print(f"  {COUNTY_NAME} — {COUNTY_DEPARTMENT}")
    print("  ADA Remediation Pipeline")
    print("═" * 62)
    print()

    # Warn if optional capabilities are missing
    if not PDF2IMAGE_AVAILABLE:
        print("  ⚠ pdf2image / Pillow not found — image alt text will be skipped")
    if _anthropic_client is None:
        if not ANTHROPIC_API_KEY:
            print("  ⚠ ANTHROPIC_API_KEY not set — image alt text will be skipped")
        else:
            print("  ⚠ Anthropic client failed to initialize — check your API key")
    if _GS_EXE:
        print(f"  ✓ Font embedding: Ghostscript found at {_GS_EXE}")
        print("    (GS runs before pikepdf so it cannot touch the structure tree)")
    else:
        print("  ⚠ Font embedding: Ghostscript not found — skipped")
        print("    (Most county docs have fonts pre-embedded; install GS to be thorough)")
    if PYTESSERACT_AVAILABLE and PDF2IMAGE_AVAILABLE:
        print("  ✓ Tesseract OCR fallback enabled — will recover docs with no text layer (+20 pts)")
    else:
        missing = []
        if not PYTESSERACT_AVAILABLE:
            missing.append("pytesseract")
        if not PDF2IMAGE_AVAILABLE:
            missing.append("pdf2image")
        print(f"  ⚠ Tesseract OCR fallback disabled — missing: {', '.join(missing)}")
    if _anthropic_client and PDF2IMAGE_AVAILABLE:
        print("  ✓ Color contrast check enabled (Claude Vision samples 3 pages per doc)")
        print("  ✓ Heading detection enabled (Claude Vision retags /P → H1/H2/H3 when missing)")
    else:
        print("  ⚠ Color contrast check unavailable — 5-point deduction will always apply")
        print("  ⚠ Heading detection unavailable — requires Claude API + pdf2image")
    print()

    # ── Directory setup ────────────────────────────────────────────
    for folder in (DOWNLOADS_DIR, REMEDIATED_DIR, LOGS_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    # ── Load status CSV ────────────────────────────────────────────
    print("Loading status CSV...")
    csv_mgr = CSVManager(STATUS_CSV)
    ready = csv_mgr.get_ready()
    total = len(csv_mgr.rows)
    n_ready = len(ready)

    print(f"  Total in CSV           : {total}")
    print(f"  Ready (pending)        : {n_ready}")
    print(f"  Already complete       : {total - n_ready}")

    if n_ready == 0:
        print()
        print("  Nothing to process.")
        print("  • Check remediation_status.csv for rows with pipeline_pass_status = pending.")
        print("  • Run _reset_pipeline.py to reset all rows to pending.")
        return

    n_passed = 0
    n_failed = 0

    print()

    for idx, row in enumerate(ready, start=1):
        fname    = row["filename"]
        title    = row.get("title") or filename_to_title(fname)
        src_path = DOWNLOADS_DIR / fname
        dst_path = REMEDIATED_DIR / fname
        log_path = LOGS_DIR / f"{Path(fname).stem}_remediation_log.txt"

        print(f"[{idx}/{n_ready}] {fname}")

        if not src_path.exists():
            print(f"  ✗ source file not found in downloads\n")
            csv_mgr.update_row(fname, {
                "pipeline_pass_status": "failed",
                "processed_date": TODAY,
            })
            n_failed += 1
            continue

        try:
            # ── Step 1: Assess source document ─────────────────────
            print("  → assessing...")
            issues = assess_document(src_path)
            pc = issues.get("page_count", 0)
            print(
                f"  → {pc}pp | "
                f"struct:{'✓' if issues['has_structure_tree'] else '✗'}  "
                f"text:{'✓' if issues['has_text_layer'] else '✗'}  "
                f"imgs:{'✓' if issues['has_images'] else '—'}  "
                f"bookmarks:{'✓' if issues['has_bookmarks'] else '—'}"
            )

            # ── Step 2: Apply pikepdf remediations ─────────────────
            print("  → applying pikepdf fixes...")
            fixes, errs = apply_pikepdf_remediations(src_path, dst_path, title)
            if errs:
                for e in errs:
                    print(f"  ⚠ {e}")
            else:
                print(f"  → {len(fixes)} fix(es) applied")

            # ── Step 2b: Tesseract OCR fallback ──────────────────────
            # Runs only when Acrobat OCR failed to produce a text layer.
            # Renders every page at 300 DPI and rebuilds the PDF with an
            # invisible Tesseract text overlay, recovering the -20 deduction.
            # Metadata/accessibility flags are re-applied immediately after.
            if (not issues.get("has_text_layer")
                    and PYTESSERACT_AVAILABLE and PDF2IMAGE_AVAILABLE
                    and dst_path.exists()):
                print(f"  → no text layer — running Tesseract OCR fallback ({pc} page(s))...")
                tess_ok, tess_pages, tess_msg = apply_tesseract_ocr(dst_path, pc)
                if tess_ok:
                    fixes.append(tess_msg)
                    print(f"  → Tesseract: ✓ {tess_pages}/{pc} page(s) OCR'd")
                    # Re-apply accessibility metadata lost when Tesseract rebuilt the PDF
                    meta_fixes = _reapply_metadata(dst_path, title)
                    fixes += meta_fixes
                elif tess_msg:
                    print(f"  ⚠ Tesseract: {tess_msg}")

            # ── Step 3: Alt text generation ────────────────────────
            alt_count = 0
            alt_log_entries: list[str] = []
            if issues.get("has_images") and dst_path.exists():
                print("  → generating image alt text...")
                alt_count, alt_log_entries = process_alt_text(dst_path, LOGS_DIR, fname)
                if alt_count > 0:
                    print(f"  → alt text: {alt_count} image(s) processed")
                else:
                    print("  → alt text: skipped or no images found")

            # ── Step 3a: Heading detection via Claude Vision ────────
            # Only runs when:
            #   • the source PDF had no headings, AND
            #   • add_minimal_structure_tree() did NOT already apply Vision
            #     heading detection (which would make this redundant).
            # Re-tags /P elements matching Claude-identified headings as H1/H2/H3.
            vision_already_applied = any(
                "Claude Vision" in f for f in fixes
            )
            if (not issues.get("has_headings")
                    and not vision_already_applied
                    and _anthropic_client and PDF2IMAGE_AVAILABLE
                    and dst_path.exists()):
                print("  → detecting headings via Claude Vision...")
                h_fixes, h_errs = tag_headings_from_claude(dst_path, pc)
                if h_fixes:
                    fixes += h_fixes
                    print(f"  → headings: {h_fixes[0]}")
                elif h_errs:
                    for e in h_errs:
                        print(f"  ⚠ heading: {e}")
                else:
                    print("  → headings: none identified on sampled pages")

            # ── Step 3b: Color contrast check (Claude Vision) ──────
            contrast_passed = False
            if _anthropic_client and PDF2IMAGE_AVAILABLE and dst_path.exists():
                print("  → checking color contrast...")
                contrast_passed, contrast_detail = check_color_contrast(dst_path, pc)
                marker = "✓" if contrast_passed else "—"
                print(f"  → contrast: {marker} ({contrast_detail[:60]})")

            # ── Step 4: Re-assess the remediated file ──────────────
            # Scoring on the post-remediation file captures what's still outstanding
            final_issues = assess_document(dst_path) if dst_path.exists() else issues
            final_issues["contrast_check_passed"] = contrast_passed

            # ── Step 5: Calculate compliance score ─────────────────
            score, grade, manual_items = score_document(final_issues, pc)
            print(f"  → compliance: {score}/100  grade: {grade}")

            # ── Step 6: Write per-document log ─────────────────────
            write_log(
                log_path, fname, final_issues, fixes,
                alt_log_entries, manual_items, score, grade,
            )

            # ── Step 7: Update CSV ─────────────────────────────────
            csv_mgr.update_row(fname, {
                "pipeline_pass_status": "complete",
                "alt_text_images":      str(alt_count),
                "compliance_score":     str(score),
                "compliance_grade":     grade,
                "manual_review_items":  " | ".join(manual_items),
                "log_path":             str(log_path),
                "processed_date":       TODAY,
            })

            status_marker = "✓" if not errs else "⚠"
            print(f"  {status_marker} complete\n")
            n_passed += 1

        except KeyboardInterrupt:
            # User pressed Ctrl+C — save progress and exit cleanly.
            # This document's row is NOT updated so it will retry on next run.
            print(f"\n  ⚠ Interrupted during {fname} — progress saved, re-run to continue.")
            break

        except APICreditsExhaustedError as credits_err:
            # Anthropic API billing/credit error — stop immediately.
            # Do NOT mark this document complete; leave it pending so it retries.
            print(f"\n  ✗ API CREDITS EXHAUSTED — stopping pipeline.")
            print(f"    {credits_err}")
            print(f"    Document '{fname}' left as pending — will retry on next run.")
            remaining = n_ready - (idx + 1)
            if remaining > 0:
                print(f"    {remaining} remaining document(s) also stay pending.")
            print(f"\n    → Top up your Anthropic API credits, then re-run RUN_PASS2_ONLY.bat.")
            break

        except Exception as doc_err:
            # Per-document exception — log it and continue to the next file
            msg = str(doc_err)
            print(f"  ✗ FAILED: {msg}\n")
            try:
                log_path.write_text(
                    f"PIPELINE ERROR\nFile:  {fname}\nDate:  {TODAY}\nError: {msg}\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
            csv_mgr.update_row(fname, {
                "pipeline_pass_status": "failed",
                "log_path": str(log_path),
                "processed_date": TODAY,
            })
            n_failed += 1

    # ── Summary ────────────────────────────────────────────────────
    print("═" * 62)
    print("  PASS 2 COMPLETE")
    print(f"  Processed : {n_passed + n_failed} of {n_ready}")
    print(f"  Passed    : {n_passed}")
    print(f"  Failed    : {n_failed}")
    print("═" * 62)
    print()
    print(f"  Remediated files : {REMEDIATED_DIR}/")
    print(f"  Logs             : {LOGS_DIR}/")
    print(f"  Status CSV       : {STATUS_CSV}")
    if n_failed > 0:
        print()
        print(f"  ⚠ {n_failed} document(s) failed — check {LOGS_DIR}/ for details.")
        print("    Failed rows have pipeline_pass_status = failed in the CSV.")


if __name__ == "__main__":
    main()
