import re

import pikepdf
import pymupdf as fitz

from remediation.adapters.base import ScoringAdapter, ScoringResult

# Images smaller than this in either dimension are treated as decorative.
IMAGE_MIN_SIZE = 100

# Compliance score deductions by issue key, ported from ada-remediation-pipeline.
DEDUCTIONS: dict[str, int] = {
    "no_structure_tree": 18,
    "no_text_layer": 20,
    "missing_alt_text": 12,
    "form_no_tooltips": 10,
    "missing_headings": 10,
    "missing_bookmarks": 6,
    "unembedded_fonts": 6,
    "vague_hyperlinks": 6,
    "color_contrast": 5,
    "complex_tables": 4,
}

GRADE_THRESHOLDS = [(90, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F")]

VAGUE_LINK_RE = re.compile(
    r"^(click here|here|this link|read more|more|learn more|click|link)$",
    re.IGNORECASE,
)

# PDF base-14 fonts — always available, exempt from the embedding check.
_BASE14_FONTS = {
    "/Courier",
    "/Courier-Bold",
    "/Courier-Oblique",
    "/Courier-BoldOblique",
    "/Helvetica",
    "/Helvetica-Bold",
    "/Helvetica-Oblique",
    "/Helvetica-BoldOblique",
    "/Times-Roman",
    "/Times-Bold",
    "/Times-Italic",
    "/Times-BoldItalic",
    "/Symbol",
    "/ZapfDingbats",
}


class PikePdfAdapter(ScoringAdapter):
    """Ports ada-remediation-pipeline's assess_document()/score_document() heuristic
    scorer, for comparison against veraPDF's verdict (add_confidence_scoring experiment).
    No AI/contrast check — `issues["contrast_check_passed"]` is always absent, so the
    color-contrast deduction always applies (matches the old pipeline's own no-AI fallback).
    """

    @property
    def name(self) -> str:
        return "pikepdf Adapter"

    def score(self, pdf_path: str) -> ScoringResult:
        try:
            issues = self._assess(pdf_path)
            score, grade, manual_review_items = self._score_document(issues, issues["page_count"])
        except Exception as exc:
            self.raise_adapter_error(f"scoring failed: {exc}")

        return ScoringResult(score=score, grade=grade, manual_review_items=manual_review_items)

    def _assess(self, pdf_path: str) -> dict:
        """Ported from assess_document(). Always returns every key."""
        issues: dict = {
            "has_structure_tree": False,
            "has_text_layer": False,
            "has_images": False,
            "images_without_alt": 0,
            "has_forms": False,
            "form_fields_count": 0,
            "form_fields_without_tooltips": 0,
            "has_bookmarks": False,
            "page_count": 0,
            "unembedded_fonts": [],
            "vague_links": [],
            "has_tables": False,
            "tables_without_headers": 0,
            "has_headings": False,
        }

        # Text layer / bookmarks / heading heuristic (PyMuPDF).
        try:
            with fitz.open(pdf_path) as doc:
                issues["page_count"] = doc.page_count
                sample_text = "".join(doc[i].get_text() for i in range(min(5, doc.page_count)))
                issues["has_text_layer"] = len(sample_text.strip()) > 50
                if doc.get_toc():
                    issues["has_bookmarks"] = True
                if re.search(r"\n[A-Z][A-Z\s]{2,50}\n", sample_text):
                    issues["has_headings"] = True
                if re.search(r"^\d+\.\s+[A-Z]", sample_text, re.MULTILINE):
                    issues["has_headings"] = True
        except Exception:
            pass

        # Structural checks (pikepdf).
        try:
            with pikepdf.open(pdf_path) as pdf:
                if "/StructTreeRoot" in pdf.Root:
                    issues["has_structure_tree"] = True
                    self._walk_struct_tree(pdf.Root["/StructTreeRoot"], pdf, issues)

                substantive_count = 0
                for page in pdf.pages:
                    resources = page.get("/Resources")
                    if resources is None:
                        continue
                    xobjects = resources.get("/XObject")
                    if xobjects is None:
                        continue
                    for name in xobjects:
                        try:
                            xobj = xobjects[str(name)]
                            if str(xobj.get("/Subtype", "")) == "/Image":
                                w = int(xobj.get("/Width", 0))
                                h = int(xobj.get("/Height", 0))
                                if w >= IMAGE_MIN_SIZE and h >= IMAGE_MIN_SIZE:
                                    substantive_count += 1
                        except Exception:
                            pass
                issues["has_images"] = substantive_count > 0

                seen_bases: set[str] = set()
                for page in pdf.pages:
                    resources = page.get("/Resources")
                    if resources is None:
                        continue
                    fonts = resources.get("/Font")
                    if fonts is None:
                        continue
                    for font_key in fonts:
                        try:
                            font = fonts[str(font_key)]
                            base = str(font.get("/BaseFont", font_key))
                            if base in seen_bases:
                                continue
                            seen_bases.add(base)
                            if font.get("/FontDescriptor") is None:
                                ftype = str(font.get("/Subtype", ""))
                                if ftype in ("/Type1", "/TrueType") and base not in _BASE14_FONTS:
                                    issues["unembedded_fonts"].append(base)
                        except Exception:
                            pass

                if "/AcroForm" in pdf.Root:
                    issues["has_forms"] = True
                    acro = pdf.Root["/AcroForm"]
                    total_fields = 0
                    missing_tooltips = 0

                    def _count_fields(fields_arr: pikepdf.Object) -> None:
                        nonlocal total_fields, missing_tooltips
                        if not isinstance(fields_arr, pikepdf.Array):
                            return
                        for item in fields_arr:
                            try:
                                fd = (
                                    item
                                    if isinstance(item, pikepdf.Dictionary)
                                    else pdf.get_object(item.objgen)
                                )
                                if fd.get("/T") is not None:
                                    total_fields += 1
                                    tu = fd.get("/TU")
                                    if not tu or str(tu).strip() == "":
                                        missing_tooltips += 1
                                kids = fd.get("/Kids")
                                if kids:
                                    _count_fields(kids)
                            except Exception:
                                pass

                    fields = acro.get("/Fields")
                    if fields is not None:
                        _count_fields(fields)
                    issues["form_fields_count"] = total_fields
                    issues["form_fields_without_tooltips"] = missing_tooltips

                for page in pdf.pages:
                    annots = page.get("/Annots")
                    if annots is None:
                        continue
                    for annot in annots:
                        try:
                            if (
                                isinstance(annot, pikepdf.Dictionary)
                                and str(annot.get("/Subtype", "")) == "/Link"
                            ):
                                contents = str(annot.get("/Contents", "")).strip()
                                if VAGUE_LINK_RE.match(contents):
                                    issues["vague_links"].append(contents)
                        except Exception:
                            pass
        except Exception:
            pass

        return issues

    def _walk_struct_tree(
        self, elem: object, pdf: pikepdf.Pdf, issues: dict, depth: int = 0
    ) -> None:
        """Recursively walks the struct tree for headings/tables/figures-without-alt."""
        if depth > 20 or not isinstance(elem, pikepdf.Dictionary):
            return
        s_type = str(elem.get("/S", ""))
        if s_type in ("/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6"):
            issues["has_headings"] = True
        if s_type == "/Table":
            issues["has_tables"] = True
            try:
                first_tr = self._get_first_tr(elem, pdf)
                n_rows = self._count_trs_in_table(elem, pdf)
                if first_tr is not None and n_rows >= 2:
                    tr_kids = first_tr.get("/K")
                    has_th = isinstance(tr_kids, pikepdf.Array) and any(
                        str(
                            (
                                c if isinstance(c, pikepdf.Dictionary) else pdf.get_object(c.objgen)
                            ).get("/S", "")
                        )
                        == "/TH"
                        for c in tr_kids
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
                self._walk_struct_tree(kid, pdf, issues, depth + 1)
        elif isinstance(kids, pikepdf.Dictionary):
            self._walk_struct_tree(kids, pdf, issues, depth + 1)

    def _get_first_tr(
        self, table_elem: pikepdf.Dictionary, pdf: pikepdf.Pdf
    ) -> "pikepdf.Object | None":
        """First TR in a Table, handling THead/TBody/TFoot groups."""
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
                                ik_d = (
                                    ik
                                    if isinstance(ik, pikepdf.Dictionary)
                                    else pdf.get_object(ik.objgen)
                                )
                                if str(ik_d.get("/S", "")) == "/TR":
                                    return ik_d
                            except Exception:
                                pass
            except Exception:
                pass
        return None

    def _count_trs_in_table(self, table_elem: pikepdf.Dictionary, pdf: pikepdf.Pdf) -> int:
        """Counts all TR children, including those under THead/TBody/TFoot."""
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
                                ik_d = (
                                    ik
                                    if isinstance(ik, pikepdf.Dictionary)
                                    else pdf.get_object(ik.objgen)
                                )
                                if str(ik_d.get("/S", "")) == "/TR":
                                    count += 1
                            except Exception:
                                pass
            except Exception:
                pass
        return count

    @staticmethod
    def _score_document(issues: dict, page_count: int) -> tuple[int, str, list[str]]:
        """Ported from score_document(). Starts at 100, deducts per unresolved issue."""
        score = 100
        manual_items: list[str] = []

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
                "→ Tools → Prepare Form → right-click each field → Properties → Tooltip"
            )

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

        # No AI contrast check in this port — always deducted, per class docstring.
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
                "→ Tags panel → verify TH/TD structure and /Scope on header cells"
            )

        score = max(0, score)

        grade = "F"
        for threshold, letter in GRADE_THRESHOLDS:
            if score >= threshold:
                grade = letter
                break

        return score, grade, manual_items
