import io
import os
import re

import pikepdf
from fontTools.ttLib import TTFont

from remediation.adapters.base import FontRepairAdapter

# Small bundled wordlist for the offset-detection coherence check. No /usr/share/dict/words,
# wordfreq, or nltk are available in this environment — this is a deliberately cheap, scoped
# proxy (printable-ratio gate + common-word-hit rate), not the more ambitious LLM-based
# coherence checker floated elsewhere in implementation_plan.md's backlog for OCR-quality
# scoring. That's a separate, bigger idea and not a prerequisite here.
_COMMON_WORDS = frozenset(
    [
        "the",
        "of",
        "and",
        "to",
        "in",
        "a",
        "is",
        "that",
        "for",
        "on",
        "with",
        "as",
        "this",
        "by",
        "be",
        "or",
        "at",
        "from",
        "an",
        "are",
        "it",
        "was",
        "will",
        "shall",
        "not",
        "department",
        "board",
        "county",
        "employee",
        "employees",
        "plan",
        "date",
        "name",
        "title",
        "form",
        "please",
        "action",
        "if",
        "any",
        "all",
        "may",
        "must",
        "should",
        "request",
        "requested",
        "description",
        "background",
        "funding",
        "source",
        "amount",
        "purpose",
        "prepared",
        "subject",
        "specific",
        "meeting",
        "proposed",
        "budget",
        "number",
        "street",
        "city",
        "state",
        "zip",
        "phone",
        "email",
        "office",
        "administrator",
        "commissioner",
        "director",
        "manager",
        "staff",
        "public",
        "agenda",
        "item",
        "resolution",
        "motion",
        "approve",
        "approved",
        "denied",
        "vote",
        "yes",
        "no",
        "signature",
        "print",
        "effective",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "page",
        "attachment",
        "exhibit",
        "section",
        "article",
        "division",
        "fund",
        "account",
        "general",
        "information",
        "contact",
        "address",
        "telephone",
        "fax",
        "website",
        "michigan",
        "sheriff",
        "clerk",
        "treasurer",
        "prosecutor",
        "court",
        "dispatch",
        "department",
        "program",
        "services",
        "employer",
        "contribution",
        "benefit",
        "compensation",
        "adoption",
        "agreement",
        "committee",
        "resolution",
        "ordinance",
        "permit",
        "application",
    ]
)

MIN_PRINTABLE_RATIO = 0.95
MIN_WORD_HIT_RATIO = 0.2
MIN_USED_CODES = 8


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    printable = sum(1 for c in text if 0x20 <= ord(c) <= 0x7E or c in "\n\r\t")
    return printable / len(text)


def _word_hit_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z]{2,}", text)
    if not words:
        return 0.0
    hits = sum(1 for w in words if w.lower() in _COMMON_WORDS)
    return hits / len(words)


def _best_offset(code_sequence: list[int | None]) -> int | None:
    """Brute-forces a constant per-code Unicode offset and returns it only if a candidate
    clears both a printable-character gate and a common-word-hit confidence bar — never
    guesses. See `implementation_plan.md`/the font-repair plan for why this offset-shape defect
    is real and recoverable (confirmed by hand on two real fonts in a real document).

    `code_sequence` must be the codes *in the order they were shown* (not a deduplicated set) —
    scoring against a set collapses every run of text into one jumbled alphabet with no word
    structure left to match against, which silently defeats the whole coherence check. `None`
    entries mark a boundary between separate show operations and are treated as a space, so
    word-hit matching sees real word/line boundaries instead of one run-on string.
    """
    used_codes = [c for c in code_sequence if c is not None]
    if len(used_codes) < MIN_USED_CODES:
        return None

    best_offset = None
    best_score = 0.0
    for offset in range(-255, 256):
        if offset == 0:
            continue
        try:
            chars = [" " if c is None else chr(c + offset) for c in code_sequence]
        except ValueError:
            continue
        candidate = "".join(chars)
        if _printable_ratio(candidate) < MIN_PRINTABLE_RATIO:
            continue
        score = _word_hit_ratio(candidate)
        if score > best_score:
            best_score = score
            best_offset = offset

    if best_score < MIN_WORD_HIT_RATIO:
        return None
    return best_offset


def _build_tounicode_cmap(codes: list[int], offset: int) -> bytes:
    """Builds a standard `/ToUnicode` CMap stream mapping each 2-byte code actually used by a
    font to `code + offset`. Spec limits `beginbfchar`/`endbfchar` blocks to 100 entries, so
    this chunks large code sets across multiple blocks.
    """
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    sorted_codes = sorted(codes)
    for chunk_start in range(0, len(sorted_codes), 100):
        chunk = sorted_codes[chunk_start : chunk_start + 100]
        valid = [c for c in chunk if 0 <= c + offset <= 0xFFFF]
        if not valid:
            continue
        lines.append(f"{len(valid)} beginbfchar")
        for code in valid:
            lines.append(f"<{code:04X}> <{code + offset:04X}>")
        lines.append("endbfchar")
    lines += [
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
        "",
    ]
    return "\n".join(lines).encode("ascii", errors="replace")


class PikePdfAdapter(FontRepairAdapter):
    """Repairs two distinct, confirmed embedded-font defects found in a real submitted document
    (see the font-repair plan / `implementation_plan.md`), both traced to the same underlying
    font-subsetting tool producing structurally incomplete fonts — not hypothetical cases.

    1. **Missing `/ToUnicode`** on fonts whose character codes are shifted from true Unicode by
       a constant, per-font offset. Scoped narrowly: only `Type0`/`CIDFontType2` fonts with
       `Identity` encoding, no existing `/ToUnicode`, and a constant offset that clears both a
       printable-character gate and a common-word confidence bar (`_best_offset`). Every other
       shape of ToUnicode gap is left untouched rather than guessed at — an unproven repair
       would be worse than the current, honestly reported gap. Only scans each page's own
       top-level content stream and `/Resources/Font` — does not recurse into Form XObjects,
       since the two confirmed real cases are both page-level and recursing adds real
       resource-inheritance complexity (see the Java-side `ResourceHandler`/`ChunksWriter`
       investigation in the NPE-crash plan for how easily that goes wrong) with no concrete case
       driving it today.
    2. **Incomplete `/CIDSet`** on the FontDescriptor of an embedded CID font: PDF/UA requires a
       CIDSet, when present, to mark every CID actually present in the embedded font *program*
       ("regardless of whether a CID... is referenced or used by the PDF or not" — ISO
       14289-1:2014 7.21.4.2). Confirmed on the same two real fonts: the existing CIDSet only
       marked CIDs the document's own content stream happened to use (in one case the stream
       wasn't even long enough to represent the font's real glyph count at all), which is
       exactly what the rule says is insufficient. Scoped to `CIDFontType2`/`FontFile2`
       (TrueType) fonts — the only case confirmed so far; CFF (`FontFile3`) is out of scope
       until a real instance shows up, same "don't guess beyond what's validated" principle as
       the ToUnicode fix. Reads the font's true glyph count via `fontTools`, and only replaces
       the CIDSet if it doesn't already mark every glyph in that range — never touches an
       already-correct one.
    """

    @property
    def name(self) -> str:
        return "pikepdf Adapter"

    def repair(self, pdf_path: str, *, output_dir: str) -> str:
        """Applies the font repairs to `pdf_path` and writes the result into `output_dir` under
        the same filename, same convention as the other adapters. Returns the output path.
        """
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, os.path.basename(pdf_path))

        try:
            with pikepdf.open(pdf_path) as pdf:
                for page in pdf.pages:
                    self._repair_page_fonts(pdf, page)
                self._repair_cidsets(pdf)
                pdf.save(output_path)
        except Exception as exc:
            self.raise_adapter_error(f"pikepdf failed to repair fonts: {exc}")

        return output_path

    def _repair_page_fonts(self, pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
        resources = page.obj.get("/Resources")
        if resources is None:
            return
        fonts = resources.get("/Font")
        if fonts is None:
            return

        candidates = {str(name): font for name, font in fonts.items() if self._is_candidate(font)}
        if not candidates:
            return

        # Per font: codes in the order they were shown, with a `None` boundary marker after
        # each separate show operation — see `_best_offset`'s docstring for why order matters.
        code_sequences: dict[str, list[int | None]] = {name: [] for name in candidates}
        try:
            tokens = pikepdf.parse_content_stream(page)
        except Exception:
            return  # can't parse this page's content stream — leave fonts untouched

        current_font_name = None
        for token in tokens:
            # parse_content_stream() can also yield ContentStreamInlineImage for a BI..ID..EI
            # inline image — not an operator/operands pair, so skip anything that isn't a real
            # instruction rather than assuming every token unpacks the same way.
            if not isinstance(token, pikepdf.ContentStreamInstruction):
                continue
            operands, operator = token.operands, token.operator
            op = str(operator)
            if op == "Tf" and operands:
                current_font_name = str(operands[0])
            elif op in ("Tj", "'", '"') and current_font_name in candidates and operands:
                sequence = code_sequences[current_font_name]
                self._collect_codes(operands[-1], sequence)
                sequence.append(None)
            elif op == "TJ" and current_font_name in candidates and operands:
                sequence = code_sequences[current_font_name]
                for element in operands[0]:
                    if isinstance(element, pikepdf.String):
                        self._collect_codes(element, sequence)
                sequence.append(None)

        for name, sequence in code_sequences.items():
            used_codes = {c for c in sequence if c is not None}
            if not used_codes:
                continue
            offset = _best_offset(sequence)
            if offset is None:
                continue
            self._attach_tounicode(pdf, candidates[name], used_codes, offset)

    def _is_candidate(self, font: pikepdf.Object) -> bool:
        if str(font.get("/Subtype", "")) != "/Type0":
            return False
        if "/ToUnicode" in font:
            return False
        if str(font.get("/Encoding", "")) not in ("/Identity-H", "/Identity-V"):
            return False
        descendants = font.get("/DescendantFonts")
        if not descendants:
            return False
        # Both CID font subtypes (TrueType-based CIDFontType2 and CFF-based CIDFontType0) can
        # carry the same Identity-encoding defect — confirmed on real examples of each: page 9's
        # font is CIDFontType2, page 29's (CANKNL+HelveticaNeueLTStd-Lt, the exact font veraPDF
        # flagged in the full document) is CIDFontType0.
        return str(descendants[0].get("/Subtype", "")) in ("/CIDFontType0", "/CIDFontType2")

    def _collect_codes(self, string_obj: pikepdf.Object, out: list[int | None]) -> None:
        raw = bytes(string_obj)
        for i in range(0, len(raw) - 1, 2):
            out.append((raw[i] << 8) | raw[i + 1])

    def _attach_tounicode(
        self, pdf: pikepdf.Pdf, font: pikepdf.Object, codes: set[int], offset: int
    ) -> None:
        cmap_bytes = _build_tounicode_cmap(sorted(codes), offset)
        stream = pikepdf.Stream(pdf, cmap_bytes)
        font["/ToUnicode"] = pdf.make_indirect(stream)

    def _repair_cidsets(self, pdf: pikepdf.Pdf) -> None:
        """Walks every page's fonts once, deduplicating by the FontDescriptor's own indirect
        object id — the same embedded font commonly appears under several page resource
        entries (confirmed on the real document: the same Arial-BoldMT font is referenced from
        both page 9 and page 34), and there's no reason to re-parse the same embedded font
        program with `fontTools` more than once.
        """
        seen_descriptor_ids: set[int] = set()
        for page in pdf.pages:
            resources = page.obj.get("/Resources")
            if resources is None:
                continue
            fonts = resources.get("/Font")
            if fonts is None:
                continue
            for _name, font in fonts.items():
                if str(font.get("/Subtype", "")) != "/Type0":
                    continue
                descendants = font.get("/DescendantFonts")
                if not descendants:
                    continue
                font_descriptor = descendants[0].get("/FontDescriptor")
                if font_descriptor is None:
                    continue
                descriptor_id = font_descriptor.objgen[0]
                if descriptor_id in seen_descriptor_ids:
                    continue
                seen_descriptor_ids.add(descriptor_id)
                self._repair_cidset(pdf, font_descriptor)

    def _repair_cidset(self, pdf: pikepdf.Pdf, font_descriptor: pikepdf.Object) -> None:
        cidset = font_descriptor.get("/CIDSet")
        if cidset is None:
            return  # rule 7.21.4.2 only applies when a CIDSet exists at all
        font_file = font_descriptor.get("/FontFile2")
        if font_file is None:
            return  # scoped to TrueType-based CID fonts — the only confirmed real case

        num_glyphs = self._count_glyphs(bytes(font_file.read_bytes()))
        if num_glyphs is None:
            return  # unparseable font program — leave the existing CIDSet alone, don't guess

        current_bytes = bytes(cidset.read_bytes())
        if self._cidset_covers_all_glyphs(current_bytes, num_glyphs):
            return  # already correct — never touch a CIDSet that's already right

        new_bytes = self._build_full_cidset(num_glyphs)
        font_descriptor["/CIDSet"] = pdf.make_indirect(pikepdf.Stream(pdf, new_bytes))

    def _count_glyphs(self, font_data: bytes) -> int | None:
        try:
            font = TTFont(io.BytesIO(font_data), lazy=True)
            return int(font["maxp"].numGlyphs)
        except Exception:
            return None

    def _cidset_covers_all_glyphs(self, cidset_bytes: bytes, num_glyphs: int) -> bool:
        if len(cidset_bytes) * 8 < num_glyphs:
            return False
        for cid in range(num_glyphs):
            byte_index, bit_index = divmod(cid, 8)
            if not (cidset_bytes[byte_index] >> (7 - bit_index)) & 1:
                return False
        return True

    def _build_full_cidset(self, num_glyphs: int) -> bytes:
        """Builds a CIDSet with every CID in `[0, num_glyphs)` marked present — one bit per
        CID, packed high-order-bit-first per byte (ISO 32000-2, same bit order the existing,
        incomplete CIDSets in the real document already used). Treats every glyph slot the
        font program declares as "present," matching how a properly-subsetted font's CIDSet is
        normally built — these fonts are subsetted in name only (near-full glyph tables kept,
        per `fontTools`), so this is the correct set, not an approximation.
        """
        result = bytearray((num_glyphs + 7) // 8)
        for cid in range(num_glyphs):
            byte_index, bit_index = divmod(cid, 8)
            result[byte_index] |= 1 << (7 - bit_index)
        return bytes(result)
