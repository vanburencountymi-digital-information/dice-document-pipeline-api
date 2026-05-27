"""PDF accessibility assessment helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


IMAGE_MIN_SIZE = 100

VAGUE_LINK_RE = re.compile(
    r"^(click here|here|this link|read more|more|learn more|click|link)$",
    re.IGNORECASE,
)


def default_issues() -> dict:
    """Return the complete assessment shape with conservative defaults."""
    return {
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


def assess_document(pdf_path: Path) -> dict:
    """Inspect a PDF for accessibility issues the remediation pipeline detects."""
    issues = default_issues()

    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None

    if PdfReader is not None:
        try:
            reader = PdfReader(str(pdf_path))
            issues["page_count"] = len(reader.pages)

            sample_text = ""
            for page in reader.pages[:5]:
                sample_text += page.extract_text() or ""
            issues["has_text_layer"] = len(sample_text.strip()) > 50

            if reader.outline:
                issues["has_bookmarks"] = True

            if re.search(r"\n[A-Z][A-Z\s]{2,50}\n", sample_text):
                issues["has_headings"] = True
            if re.search(r"^\d+\.\s+[A-Z]", sample_text, re.MULTILINE):
                issues["has_headings"] = True
        except Exception:
            pass

    try:
        import pikepdf
    except ImportError:
        return issues

    try:
        with pikepdf.open(str(pdf_path)) as pdf:
            if "/StructTreeRoot" in pdf.Root:
                issues["has_structure_tree"] = True
                struct_root = pdf.Root["/StructTreeRoot"]

                def walk(elem: object, depth: int = 0) -> None:
                    if depth > 20 or not isinstance(elem, pikepdf.Dictionary):
                        return
                    s_type = str(elem.get("/S", ""))
                    if s_type in ("/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6"):
                        issues["has_headings"] = True
                    if s_type == "/Table":
                        issues["has_tables"] = True
                        try:
                            first_tr = _get_first_tr(elem, pdf, pikepdf)
                            n_rows = _count_trs_in_table(elem, pdf, pikepdf)
                            if first_tr is not None and n_rows >= 2:
                                tr_kids = first_tr.get("/K")
                                has_th = (
                                    isinstance(tr_kids, pikepdf.Array)
                                    and any(
                                        str(
                                            (
                                                c
                                                if isinstance(c, pikepdf.Dictionary)
                                                else pdf.get_object(c.objgen)
                                            ).get("/S", "")
                                        )
                                        == "/TH"
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
                            width = int(xobj.get("/Width", 0))
                            height = int(xobj.get("/Height", 0))
                            if width >= IMAGE_MIN_SIZE and height >= IMAGE_MIN_SIZE:
                                substantive_count += 1
                    except Exception:
                        pass
            issues["has_images"] = substantive_count > 0

            seen_bases: set[str] = set()
            base14_fonts = {
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
                        if font.get("/FontDescriptor") is None:
                            font_type = str(font.get("/Subtype", ""))
                            if font_type in ("/Type1", "/TrueType") and base not in base14_fonts:
                                issues["unembedded_fonts"].append(base)
                    except Exception:
                        pass

            if "/AcroForm" in pdf.Root:
                issues["has_forms"] = True
                acro = pdf.Root["/AcroForm"]
                total_fields = 0
                missing_tooltips = 0

                def count_fields(fields_arr: object) -> None:
                    nonlocal total_fields, missing_tooltips
                    if not isinstance(fields_arr, pikepdf.Array):
                        return
                    for item in fields_arr:
                        try:
                            field = (
                                item
                                if isinstance(item, pikepdf.Dictionary)
                                else pdf.get_object(item.objgen)
                            )
                            if field.get("/T") is not None:
                                total_fields += 1
                                tooltip = field.get("/TU")
                                if not tooltip or str(tooltip).strip() == "":
                                    missing_tooltips += 1
                            kids = field.get("/Kids")
                            if kids:
                                count_fields(kids)
                        except Exception:
                            pass

                count_fields(acro.get("/Fields", []))
                issues["form_fields_count"] = total_fields
                issues["form_fields_without_tooltips"] = missing_tooltips

            for page in pdf.pages:
                annots = page.get("/Annots", [])
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


def _get_first_tr(table_elem: Any, pdf: Any, pikepdf: Any) -> Any | None:
    """Return the first TR element in a Table, handling table section groups."""
    kids = table_elem.get("/K")
    if not isinstance(kids, pikepdf.Array):
        return None
    for kid in kids:
        try:
            child = kid if isinstance(kid, pikepdf.Dictionary) else pdf.get_object(kid.objgen)
            child_type = str(child.get("/S", ""))
            if child_type == "/TR":
                return child
            if child_type in ("/THead", "/TBody", "/TFoot"):
                inner = child.get("/K")
                if isinstance(inner, pikepdf.Array):
                    for inner_kid in inner:
                        try:
                            inner_child = (
                                inner_kid
                                if isinstance(inner_kid, pikepdf.Dictionary)
                                else pdf.get_object(inner_kid.objgen)
                            )
                            if str(inner_child.get("/S", "")) == "/TR":
                                return inner_child
                        except Exception:
                            pass
        except Exception:
            pass
    return None


def _count_trs_in_table(table_elem: Any, pdf: Any, pikepdf: Any) -> int:
    """Count TR children in a Table, including table section groups."""
    count = 0
    kids = table_elem.get("/K")
    if not isinstance(kids, pikepdf.Array):
        return 0
    for kid in kids:
        try:
            child = kid if isinstance(kid, pikepdf.Dictionary) else pdf.get_object(kid.objgen)
            child_type = str(child.get("/S", ""))
            if child_type == "/TR":
                count += 1
            elif child_type in ("/THead", "/TBody", "/TFoot"):
                inner = child.get("/K")
                if isinstance(inner, pikepdf.Array):
                    for inner_kid in inner:
                        try:
                            inner_child = (
                                inner_kid
                                if isinstance(inner_kid, pikepdf.Dictionary)
                                else pdf.get_object(inner_kid.objgen)
                            )
                            if str(inner_child.get("/S", "")) == "/TR":
                                count += 1
                        except Exception:
                            pass
        except Exception:
            pass
    return count
