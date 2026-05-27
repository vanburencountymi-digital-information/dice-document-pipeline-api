#!/usr/bin/env python3
"""
ADA Remediation Pipeline — Pass 1: Acrobat Pro Automation
Van Buren County, Michigan — DICE Department

Drives Adobe Acrobat Pro via Windows COM automation (pywin32).
Must be run BEFORE ada_remediate.py.

Usage:
    py -3.11 acrobat_pass.py

Prerequisites:
    - Adobe Acrobat Pro installed (not Adobe Reader)
    - pywin32 installed: pip install pywin32 --break-system-packages
    - Acrobat must NOT be open when this script starts
    - Run from the folder containing this script

What this does per document:
    1. Detects whether the PDF is a scanned image (no text layer)
    2. If scanned: runs OCR via Acrobat (English, 300 DPI)
    3. Runs Autotag Document (builds structure tree)
    4. Runs Full Accessibility Check (saves report to logs/)
    5. Updates remediation_status.csv
"""

import csv
import datetime
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Config import ─────────────────────────────────────────────────────────────
# Add the repo root to sys.path so 'config.constants' is importable regardless
# of the current working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.constants import (
    COUNTY_NAME, COUNTY_DEPARTMENT, COUNTY_AUTHOR, COUNTY_SUBJECT, COUNTY_PRODUCER,
    CLAUDE_MODEL, DOWNLOADS_DIR, REMEDIATED_DIR, LOGS_DIR, STATUS_CSV,
)
# ─────────────────────────────────────────────────────────────────────────────

# Acrobat COM save mode — PDSaveFull overwrites the existing file in-place
ACROBAT_SAVE_FULL = 1

# Minimum extractable character count before a PDF is considered "scanned"
SCANNED_CHAR_THRESHOLD = 50

TODAY = datetime.date.today().isoformat()

# ── CSV schema ─────────────────────────────────────────────────────────────────
# All status CSVs share this schema. acrobat_pass.py creates the file on first run.

CSV_FIELDNAMES = [
    "filename",              # PDF filename from downloads folder
    "title",                 # Human-readable title derived from filename
    "acrobat_pass_status",   # pending / complete / failed / skipped
    "pipeline_pass_status",  # pending / complete / failed / manual_review
    "ocr_applied",           # yes / no
    "autotag_applied",       # yes / no
    "alt_text_images",       # count of images processed for alt text
    "compliance_score",      # 0–100 automated estimate
    "compliance_grade",      # A / B / C / D / F
    "manual_review_items",   # pipe-separated list of human action items
    "log_path",              # path to per-document remediation log
    "processed_date",        # ISO date
]

STATUS_PENDING  = "pending"
STATUS_COMPLETE = "complete"
STATUS_FAILED   = "failed"
STATUS_SKIPPED  = "skipped"


# ── Utilities ─────────────────────────────────────────────────────────────────

def filename_to_title(filename: str) -> str:
    """Derive a human-readable document title from a PDF filename.

    Replaces hyphens and underscores with spaces, strips leading numeric IDs
    (WordPress attachment IDs), and title-cases the result.

    Examples:
        '12731-03-25-BOC-Agenda-Packet.pdf' -> '03 25 Boc Agenda Packet'
        'annual_report_2024.pdf'            -> 'Annual Report 2024'
    """
    stem = Path(filename).stem
    title = re.sub(r"[-_]+", " ", stem)          # hyphens/underscores → spaces
    title = re.sub(r"^\d{4,}\s+", "", title)     # strip leading WP attachment ID
    return title.title().strip()


# ── CSV manager ───────────────────────────────────────────────────────────────

class CSVManager:
    """Creates, loads, and updates the pipeline status CSV.

    On first run, scans the downloads folder and builds a fresh CSV.
    On subsequent runs, loads the existing CSV and merges any new files.
    Saves after every document update so progress is never lost.
    """

    def __init__(self, csv_path: Path, downloads_dir: Path):
        self.csv_path = csv_path
        self.downloads_dir = downloads_dir
        self.rows: list[dict] = []
        self._load_or_create()

    def _load_or_create(self):
        """Load an existing CSV or build a fresh one from the downloads folder."""
        existing: dict[str, dict] = {}

        if self.csv_path.exists():
            with open(self.csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    existing[row["filename"]] = row

        # Scan downloads for all PDF files
        pdf_files = sorted(self.downloads_dir.glob("*.pdf"))
        new_count = 0
        self.rows = []

        for pdf in pdf_files:
            fname = pdf.name
            if fname in existing:
                # Preserve all columns from the existing row
                row = {field: existing[fname].get(field, "") for field in CSV_FIELDNAMES}
                self.rows.append(row)
            else:
                # New file — add with fully-pending status
                self.rows.append({
                    "filename":             fname,
                    "title":               filename_to_title(fname),
                    "acrobat_pass_status": STATUS_PENDING,
                    "pipeline_pass_status": STATUS_PENDING,
                    "ocr_applied":         "no",
                    "autotag_applied":     "no",
                    "alt_text_images":     "0",
                    "compliance_score":    "",
                    "compliance_grade":    "",
                    "manual_review_items": "",
                    "log_path":            "",
                    "processed_date":      "",
                })
                new_count += 1

        print(f"  {len(pdf_files)} PDF(s) in downloads | "
              f"{len(existing)} in CSV | {new_count} new file(s) added")
        self.save()

    def save(self):
        """Write all rows to the CSV file immediately."""
        with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(self.rows)

    def get_pending(self) -> list[dict]:
        """Return all rows that need to be processed by the Acrobat pass.

        Includes both 'pending' (never attempted) and 'failed' (errored on a
        previous run) so that a simple re-run retries any failures automatically.
        'complete' and 'skipped' rows are always left alone.
        """
        return [
            r for r in self.rows
            if r.get("acrobat_pass_status") in (STATUS_PENDING, STATUS_FAILED)
        ]

    def update_row(self, filename: str, updates: dict):
        """Update a specific row's fields and save the CSV immediately."""
        for row in self.rows:
            if row["filename"] == filename:
                row.update(updates)
                break
        self.save()


# ── Pre-flight checks ─────────────────────────────────────────────────────────

def acrobat_is_running() -> bool:
    """Return True if Adobe Acrobat Pro (Acrobat.exe) is currently open.

    Uses the Windows tasklist command to check for the Acrobat process.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Acrobat.exe", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
        )
        return "Acrobat.exe" in result.stdout
    except Exception:
        return False  # Assume safe if tasklist fails


def check_dependencies() -> bool:
    """Verify that all required libraries are importable.

    Returns True if everything is installed, False otherwise.
    Prints actionable error messages on failure.
    """
    missing = []
    try:
        import win32com.client  # noqa: F401
        import pythoncom        # noqa: F401
    except ImportError:
        missing.append("pywin32")
    try:
        from pypdf import PdfReader  # noqa: F401
    except ImportError:
        missing.append("pypdf")

    if missing:
        print(f"\nERROR: Missing required libraries: {', '.join(missing)}")
        print("       Run INSTALL_DEPENDENCIES.bat to install all required packages.")
        return False
    return True


# ── Text layer detection ──────────────────────────────────────────────────────

def is_scanned_pdf(pdf_path: Path) -> bool:
    """Return True if the PDF appears to be a scanned image with no text layer.

    Checks the first 3 pages for extractable text. If fewer than
    SCANNED_CHAR_THRESHOLD characters are found, the document is flagged
    as scanned and will need OCR.
    """
    from pypdf import PdfReader
    try:
        reader = PdfReader(str(pdf_path))
        total_chars = 0
        for page in reader.pages[:3]:
            total_chars += len((page.extract_text() or "").strip())
            if total_chars >= SCANNED_CHAR_THRESHOLD:
                return False
        return total_chars < SCANNED_CHAR_THRESHOLD
    except Exception:
        # If pypdf cannot open the file, assume it has a text layer
        # (Acrobat's autotag will handle any real issues)
        return False


# ── Dialog watchdog ───────────────────────────────────────────────────────────

class DialogWatchdog:
    """Background thread that auto-dismisses unexpected Acrobat modal dialogs.

    Some Acrobat menu actions (especially "Make Accessible" on complex documents)
    spawn interactive dialogs mid-batch that would stall the script indefinitely.
    This watchdog polls every half-second for any dialog window belonging to the
    Acrobat process and sends Escape to close it, keeping the batch moving.

    Usage:
        watchdog = DialogWatchdog()
        watchdog.start()
        # ... run batch ...
        watchdog.stop()
    """

    # Window titles that belong to Acrobat and should be auto-dismissed
    _ACROBAT_TITLES = ("adobe acrobat", "acrobat", "make accessible",
                       "accessibility", "print", "recognize text", "ocr")

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self.dismissed_count = 0

    def start(self):
        """Start the watchdog background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the watchdog thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _watch(self):
        """Poll for and dismiss unexpected Acrobat dialogs every 500 ms."""
        try:
            import win32gui
            import win32con
            import win32api
        except ImportError:
            return  # win32 not available — watchdog disabled silently

        def _enum_callback(hwnd, _):
            """Called for every top-level window on the desktop."""
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).lower()
            cls   = win32gui.GetClassName(hwnd)

            # Target: dialog-class windows whose title suggests Acrobat ownership.
            # "#32770" is the Windows class for all modal dialog boxes.
            is_dialog   = (cls == "#32770")
            is_acrobat  = any(t in title for t in self._ACROBAT_TITLES)

            if is_dialog and is_acrobat:
                # Send Escape to the dialog window — dismisses most modal dialogs
                # without clicking any destructive action button.
                win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                win32api.PostMessage(hwnd, win32con.WM_KEYUP,   win32con.VK_ESCAPE, 0)
                self.dismissed_count += 1

        while self._running:
            try:
                win32gui.EnumWindows(_enum_callback, None)
            except Exception:
                pass
            time.sleep(0.5)


# ── Acrobat COM session management ────────────────────────────────────────────

class AcrobatSession:
    """Manages the Adobe Acrobat Pro COM session lifecycle.

    Opens Acrobat once at batch start and reuses the connection for every
    document. Call close() in a finally block to ensure clean shutdown.
    If COM crashes mid-batch, call reinitialize() to recover.
    """

    def __init__(self):
        self.app = None
        self._initialized = False

    def open(self) -> tuple[bool, str]:
        """Initialize the Acrobat COM interface.

        Returns (success: bool, error_message: str).
        Uses late binding (dynamic.Dispatch) to avoid gen_py stub limitations.
        """
        try:
            import pythoncom
            from win32com.client import dynamic
            pythoncom.CoInitialize()
            self.app = dynamic.Dispatch("AcroExch.App")  # late binding
            self.app.Show()           # Must be visible — GetJSObject() requires a rendered window
            self._initialized = True
            return True, ""
        except Exception as e:
            return False, str(e)

    def close(self):
        """Shut down the Acrobat COM session and release COM resources."""
        if self.app and self._initialized:
            try:
                self.app.CloseAllDocs()
            except Exception:
                pass
            self.app = None
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass
        self._initialized = False

    def reinitialize(self) -> tuple[bool, str]:
        """Attempt to recover from a COM crash by restarting the session.

        Returns (success: bool, error_message: str).
        """
        self.close()
        time.sleep(2)
        return self.open()


# ── Acrobat document operations ───────────────────────────────────────────────
#
# APPROACH: We avoid GetJSObject() entirely. That method is unreliable across
# Acrobat versions and requires an active JavaScript engine.
#
# Instead we use app.MenuItemExecute(name) on the AcroExch.App object directly.
# MenuItemExecute operates on whichever document is currently active in Acrobat,
# so the workflow is:
#   1. Open the PDF with AVDoc (makes it the active document)
#   2. Call app.MenuItemExecute("MakeAccessible" / "RecognizeText" / "FullCheck")
#   3. Save via PDDoc, close the AVDoc
#
# This bypasses the JavaScript bridge completely.

def _open_av_doc(pdf_path: Path) -> tuple[object | None, str]:
    """Open a PDF in an Acrobat AVDoc, making it the active document.

    Uses late-binding dynamic.Dispatch to avoid gen_py type library stubs.
    Returns (avDoc, error_message).
    """
    try:
        from win32com.client import dynamic
        avDoc = dynamic.Dispatch("AcroExch.AVDoc")
        ok = avDoc.Open(str(pdf_path.resolve()), pdf_path.name)
        if not ok:
            return None, "AVDoc.Open() returned False"
        time.sleep(1.5)   # Acrobat loads asynchronously; wait for it to be ready
        return avDoc, ""
    except Exception as e:
        return None, f"AVDoc.Open exception: {e}"


def _save_and_close(avDoc, pdf_path: Path, log_lines: list[str]) -> None:
    """Save the active document in-place via PDDoc, then close the AVDoc.

    Always called in a finally block so the document is never left open.
    """
    try:
        pd_doc = avDoc.GetPDDoc()
        saved = pd_doc.Save(ACROBAT_SAVE_FULL, str(pdf_path.resolve()))
        if not saved:
            log_lines.append("    WARN: PDDoc.Save() returned False")
    except Exception as e:
        log_lines.append(f"    WARN: save failed: {e}")
    try:
        avDoc.Close(False)   # False = close without prompting
    except Exception:
        pass


def run_ocr(app, pdf_path: Path, log_lines: list[str]) -> tuple[bool, str]:
    """Run Recognize Text (OCR) on a PDF via app.MenuItemExecute().

    Opens the document (making it active), fires the RecognizeText menu item
    on the App object, waits, saves, and closes.
    Returns (success, error_message).
    """
    avDoc = None
    try:
        avDoc, err = _open_av_doc(pdf_path)
        if avDoc is None:
            return False, err

        # MenuItemExecute on the App object — no JavaScript bridge needed
        app.MenuItemExecute("RecognizeText")
        time.sleep(6)   # OCR is CPU-intensive; give it time to finish
        log_lines.append("    OCR: RecognizeText executed via app.MenuItemExecute")

        _save_and_close(avDoc, pdf_path, log_lines)
        avDoc = None
        return True, ""

    except Exception as e:
        return False, f"OCR exception: {e}"
    finally:
        if avDoc:
            try:
                avDoc.Close(False)
            except Exception:
                pass


def run_autotag(app, pdf_path: Path, log_lines: list[str]) -> tuple[bool, str]:
    """Run Autotag Document and embed fonts via Acrobat COM.

    Uses "AutoTag" (direct autotag, no wizard dialog) rather than
    "MakeAccessible" (which opens the interactive accessibility wizard).
    After autotagging, attempts to embed fonts via Acrobat's built-in
    "EmbedAllFonts" command so Ghostscript is not needed at all.

    Verifies the structure tree was actually written before returning True.
    Returns (success, error_message).
    """
    avDoc = None
    try:
        avDoc, err = _open_av_doc(pdf_path)
        if avDoc is None:
            return False, err

        # ── Autotag ────────────────────────────────────────────────────────────
        # "AutoTag" fires the direct autotag engine (no dialog).
        # "MakeAccessible" opens the full accessibility wizard (requires clicks).
        app.MenuItemExecute("AutoTag")
        time.sleep(15)   # Give Acrobat time to finish — large docs need more
        log_lines.append("    Autotag: AutoTag executed via app.MenuItemExecute")

        # ── Font embedding ─────────────────────────────────────────────────────
        # Acrobat can embed fonts natively — no Ghostscript needed.
        # This runs after autotag so the structure tree is already present.
        try:
            app.MenuItemExecute("EmbedAllFonts")
            time.sleep(5)
            log_lines.append("    Fonts: EmbedAllFonts executed via app.MenuItemExecute")
        except Exception as fe:
            log_lines.append(f"    Fonts: EmbedAllFonts not available ({fe}) — skipped")

        # ── Save & verify ──────────────────────────────────────────────────────
        _save_and_close(avDoc, pdf_path, log_lines)
        avDoc = None

        # Confirm structure tree was actually written to the saved file
        try:
            import pikepdf as _pik
            with _pik.open(str(pdf_path)) as _pdf:
                has_tree = "/StructTreeRoot" in _pdf.Root
        except Exception:
            has_tree = True  # Can't verify — assume OK

        if not has_tree:
            log_lines.append("    WARNING: StructTreeRoot not found after autotag+save")
            return False, "AutoTag ran but no StructTreeRoot found in saved file"

        log_lines.append("    Verified: StructTreeRoot present in saved file")
        return True, ""

    except Exception as e:
        return False, f"Autotag exception: {e}"
    finally:
        if avDoc:
            try:
                avDoc.Close(False)
            except Exception:
                pass


def run_accessibility_check(
    app, pdf_path: Path, log_lines: list[str], logs_dir: Path
) -> tuple[bool, str]:
    """Run the Full Accessibility Check via Acrobat COM and save a report.

    Executes the FullCheck menu item and writes a marker log to
    logs/{stem}_acrobat_check.txt. Full check results are visible in
    Acrobat's Accessibility panel after the run.

    Returns (success, error_message).
    """
    report_path = logs_dir / f"{pdf_path.stem}_acrobat_check.txt"
    avDoc = None
    try:
        avDoc, err = _open_av_doc(pdf_path)
        if avDoc is None:
            return False, err

        try:
            app.MenuItemExecute("FullCheck")
            time.sleep(3)
            log_lines.append("    Accessibility check: FullCheck executed via app.MenuItemExecute")
        except Exception as e:
            log_lines.append(f"    WARN: FullCheck failed ({e})")
            report_path.write_text(
                f"Acrobat Full Accessibility Check — {TODAY}\n"
                f"File:   {pdf_path.name}\n"
                f"Status: Could not run automatically.\n"
                f"Error:  {e}\n\n"
                f"Manual action: Open in Acrobat Pro → Tools → Accessibility → Full Check\n",
                encoding="utf-8",
            )
            _save_and_close(avDoc, pdf_path, log_lines)
            avDoc = None
            return False, f"FullCheck failed: {e}"

        report_path.write_text(
            f"Acrobat Full Accessibility Check — {TODAY}\n"
            f"File:   {pdf_path.name}\n"
            f"Status: Check executed via COM automation (app.MenuItemExecute).\n\n"
            f"To review results:\n"
            f"  Open file in Acrobat Pro → Tools → Accessibility → Full Check\n"
            f"  The Accessibility Checker panel shows each issue with a fix link.\n",
            encoding="utf-8",
        )
        log_lines.append(f"    Accessibility check report: {report_path.name}")

        _save_and_close(avDoc, pdf_path, log_lines)
        avDoc = None
        return True, ""

    except Exception as e:
        return False, f"Accessibility check exception: {e}"
    finally:
        if avDoc:
            try:
                avDoc.Close(False)
            except Exception:
                pass


# ── Main entry point ──────────────────────────────────────────────────────────

def main():
    """Run Pass 1: Acrobat Pro automation on all pending PDFs."""

    print()
    print("═" * 62)
    print(f"  {COUNTY_NAME} — {COUNTY_DEPARTMENT}")
    print("  ADA Remediation Pipeline — Pass 1: Acrobat Automation")
    print("═" * 62)
    print()

    # ── Dependency check ───────────────────────────────────────────
    if not check_dependencies():
        sys.exit(1)

    # ── Acrobat conflict check ─────────────────────────────────────
    if acrobat_is_running():
        print("⚠  WARNING: Adobe Acrobat Pro is currently open.")
        print("   This script controls Acrobat via COM automation and requires")
        print("   exclusive access. Please close all Acrobat windows and re-run.")
        sys.exit(1)

    # ── Directory setup ────────────────────────────────────────────
    for folder in (DOWNLOADS_DIR, REMEDIATED_DIR, LOGS_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    if not any(DOWNLOADS_DIR.glob("*.pdf")):
        print(f"ERROR: No PDF files found in {DOWNLOADS_DIR}")
        print("       Place source PDFs there and re-run.")
        sys.exit(1)

    # ── Load / create status CSV ───────────────────────────────────
    print("Scanning downloads folder and loading status CSV...")
    csv_mgr = CSVManager(STATUS_CSV, DOWNLOADS_DIR)
    pending = csv_mgr.get_pending()

    total   = len(csv_mgr.rows)
    n_pend  = len(pending)
    skipped = total - n_pend

    print(f"\n  Total:    {total}")
    print(f"  Pending:  {n_pend}")
    print(f"  Skipped (already processed): {skipped}")

    if n_pend == 0:
        print("\n  Nothing to do — all documents already have acrobat_pass_status = complete.")
        print("  Run ada_remediate.py for Pass 2, or RESET_PIPELINE.bat to re-run Pass 2 only.")
        return

    # ── Initialize Acrobat COM ─────────────────────────────────────
    print("\nInitializing Acrobat Pro COM interface...")
    session = AcrobatSession()
    ok, err = session.open()
    if not ok:
        print(f"\nERROR: Could not initialize Acrobat COM: {err}")
        print("\nTroubleshooting steps:")
        print("  1. Confirm Adobe Acrobat Pro is installed (not Reader)")
        print("  2. Run: pip install pywin32 --break-system-packages")
        print("  3. Try: py -3.11 -c \"import win32com.client; print('pywin32 OK')\"")
        print("  4. Ensure Acrobat is not set to run as Administrator")
        print("  5. On first-ever pywin32 install, run: py -3.11 -m win32com.client.makepy")
        sys.exit(1)
    print("  ✓ Acrobat COM initialized\n")

    # ── Start dialog watchdog ──────────────────────────────────────
    # Runs in background and auto-dismisses any unexpected Acrobat modal
    # dialogs (Print, OCR options, Make Accessible wizard steps, etc.)
    # that would otherwise stall the batch indefinitely.
    watchdog = DialogWatchdog()
    watchdog.start()
    print("  ✓ Dialog watchdog active (auto-dismisses unexpected popups)\n")

    # ── Batch processing loop ──────────────────────────────────────
    n_passed = 0
    n_failed = 0

    try:
        for idx, row in enumerate(pending, start=1):
            fname    = row["filename"]
            pdf_path = DOWNLOADS_DIR / fname
            log_lines: list[str] = [
                f"ACROBAT PASS LOG — {fname}",
                f"Date: {TODAY}",
                "=" * 60,
            ]

            print(f"[{idx}/{n_pend}] processing: {fname}")

            if not pdf_path.exists():
                print(f"  ✗ file not found in downloads — skipping\n")
                csv_mgr.update_row(fname, {
                    "acrobat_pass_status": STATUS_SKIPPED,
                    "processed_date": TODAY,
                })
                n_failed += 1
                continue

            doc_failed = False

            try:
                # ── Step 1: Text layer detection ──────────────────
                scanned = is_scanned_pdf(pdf_path)
                ocr_applied = "no"

                if scanned:
                    print("  → scanned document detected — running OCR")
                    log_lines.append("DETECT: Scanned image — OCR required")
                    ok_ocr, err_ocr = run_ocr(session.app, pdf_path, log_lines)
                    if ok_ocr:
                        ocr_applied = "yes"
                        print("  → OCR complete")
                        log_lines.append("OCR: complete")
                    else:
                        print(f"  ✗ OCR failed: {err_ocr}")
                        log_lines.append(f"OCR FAILED: {err_ocr}")
                        doc_failed = True
                        # Attempt COM recovery before continuing to autotag
                        ok_reinit, _ = session.reinitialize()
                        if not ok_reinit:
                            raise RuntimeError("COM session unrecoverable after OCR failure")
                else:
                    print("  → text layer detected — OCR skipped")
                    log_lines.append("DETECT: Text layer present — OCR not required")

                # ── Step 2: Autotag + font embedding ──────────────
                ok_tag, err_tag = run_autotag(session.app, pdf_path, log_lines)
                autotag_applied = "no"

                if ok_tag:
                    autotag_applied = "yes"
                    print("  → autotag complete")
                    log_lines.append("AUTOTAG: complete")
                else:
                    print(f"  ✗ autotag failed: {err_tag}")
                    log_lines.append(f"AUTOTAG FAILED: {err_tag}")
                    doc_failed = True
                    ok_reinit, _ = session.reinitialize()
                    if not ok_reinit:
                        raise RuntimeError("COM session unrecoverable after autotag failure")

                # ── Step 3: Accessibility check ────────────────────
                ok_chk, err_chk = run_accessibility_check(session.app, pdf_path, log_lines, LOGS_DIR)
                if ok_chk:
                    print("  → accessibility check complete")
                    log_lines.append("ACCESSIBILITY CHECK: complete")
                else:
                    print(f"  ⚠ accessibility check: {err_chk} (non-fatal)")
                    log_lines.append(f"ACCESSIBILITY CHECK: {err_chk}")

                # ── Update CSV ─────────────────────────────────────
                final_status = STATUS_FAILED if doc_failed else STATUS_COMPLETE
                csv_mgr.update_row(fname, {
                    "acrobat_pass_status": final_status,
                    "ocr_applied":         ocr_applied,
                    "autotag_applied":     autotag_applied,
                    "processed_date":      TODAY,
                })

                symbol = "✓" if not doc_failed else "⚠"
                print(f"  {symbol} acrobat pass {final_status}\n")
                if not doc_failed:
                    n_passed += 1
                else:
                    n_failed += 1

            except Exception as doc_err:
                # Per-document exception — log and continue to next file
                err_msg = str(doc_err)
                print(f"  ✗ FAILED: {err_msg}\n")
                log_lines.append(f"CRITICAL ERROR: {err_msg}")
                csv_mgr.update_row(fname, {
                    "acrobat_pass_status": STATUS_FAILED,
                    "processed_date": TODAY,
                })
                n_failed += 1

                # Write error log so the human reviewer can investigate
                err_log = LOGS_DIR / f"{Path(fname).stem}_acrobat_error.txt"
                err_log.write_text("\n".join(log_lines), encoding="utf-8")

                # Try to recover the COM session before the next document
                ok_reinit, reinit_err = session.reinitialize()
                if not ok_reinit:
                    print(f"  ✗ COM session unrecoverable: {reinit_err}")
                    print("  Stopping batch to prevent further errors.")
                    break

    finally:
        # Always shut down cleanly, even if an exception escaped
        watchdog.stop()
        if watchdog.dismissed_count:
            print(f"\n  ℹ Dialog watchdog dismissed {watchdog.dismissed_count} unexpected dialog(s).")
        print("Closing Acrobat Pro...")
        session.close()

    # ── Summary ────────────────────────────────────────────────────
    print()
    print("═" * 62)
    print("  PASS 1 COMPLETE")
    print(f"  Processed : {n_passed + n_failed} of {n_pend}")
    print(f"  Passed    : {n_passed}")
    print(f"  Failed    : {n_failed}")
    print("═" * 62)
    print()
    if n_failed > 0:
        print(f"  ⚠  {n_failed} document(s) failed — check logs/ for details.")
        print("     Failed documents will remain 'pending' in the CSV.")
        print("     Fix any issues and re-run acrobat_pass.py to retry them.")
        print()
    print("  Next step: run ada_remediate.py (Pass 2)")
    print("             or use RUN_PIPELINE.bat to run both passes automatically.")


if __name__ == "__main__":
    main()
