"""Recovers Unicode for subsetted Calibri glyphs by comparing their outlines to Carlito.

Why: Word-exported PDFs sometimes embed Calibri with no `/ToUnicode` and no `cmap`/`post`
table, and its glyph ids are not a fixed offset from Unicode (so `_best_offset` rightly gives
up). Carlito is an open font with the same letter widths as Calibri, so each glyph can be
matched to the character it draws. Only glyphs that look clearly like one character are
mapped; anything unsure is left alone.

Needs the Carlito files on disk (Alpine package `font-carlito`; Debian `fonts-crosextra-carlito`); without
them, `load_reference_fonts` returns nothing and the repair is skipped.
"""

import io
import os
from typing import Any

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw

REFERENCE_FILES = {
    "regular": "Carlito-Regular.ttf",
    "bold": "Carlito-Bold.ttf",
    "italic": "Carlito-Italic.ttf",
    "bolditalic": "Carlito-BoldItalic.ttf",
}

MIN_IOU = 0.65  # below this the glyph does not clearly look like any one character
ASCII_PREFERENCE = 0.1  # plain Latin wins over an accented look-alike within this score
MIN_IOU_THIN, MIN_GAP_THIN = (
    0.35,
    0.2,
)  # weaker score allowed if far ahead of the runner-up
TIE_MARGIN = 0.03  # two glyphs this close for one character: neither is trusted
MIN_IOU_NEIGHBOUR = 0.45  # shape check for a glyph placed by its neighbours
GRID = 80  # raster size in pixels; small is fine, we only need a rough shape match
# Only plain Latin text and common punctuation. Cyrillic/Greek letters look identical to Latin
# ones, so leaving them out avoids picking a look-alike.
_ALLOWED = [
    (0x20, 0x24F),
    (0x2013, 0x2014),
    (0x2018, 0x201D),
    (0x2022, 0x2022),
    (0x2026, 0x2026),
]


def _allowed(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _ALLOWED)


class _FlatPen(BasePen):
    """Collects each outline as a list of straight-line points."""

    def __init__(self, glyph_set: Any) -> None:
        super().__init__(glyph_set)
        self.contours: list[list[tuple[float, float]]] = []
        self._current: list[tuple[float, float]] | None = None

    def _moveTo(self, pt: tuple[float, float]) -> None:
        self._current = [pt]

    def _lineTo(self, pt: tuple[float, float]) -> None:
        assert self._current is not None
        self._current.append(pt)

    def _curveToOne(
        self, p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]
    ) -> None:
        assert self._current is not None
        p0 = self._current[-1]
        for i in range(1, 9):
            t = i / 8
            u = 1 - t
            self._current.append(
                (
                    u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
                )
            )

    def _closePath(self) -> None:
        if self._current and len(self._current) > 2:
            self.contours.append(self._current)
        self._current = None

    _endPath = _closePath


def _raster(font: TTFont, glyph_name: str) -> Image.Image:
    glyph_set = font.getGlyphSet()
    pen = _FlatPen(glyph_set)
    glyph_set[glyph_name].draw(pen)
    em = font["head"].unitsPerEm
    x0, x1, y0, y1 = -0.1 * em, 1.2 * em, -0.3 * em, 1.0 * em
    result = Image.new("1", (GRID, GRID), 0)
    for contour in pen.contours:
        layer = Image.new("1", (GRID, GRID), 0)
        points = [((x - x0) / (x1 - x0) * GRID, (y1 - y) / (y1 - y0) * GRID) for x, y in contour]
        ImageDraw.Draw(layer).polygon(points, fill=1)
        result = ImageChops.logical_xor(result, layer)  # XOR so holes (o, e, 8) stay empty
    return result


def _iou(a: Image.Image, b: Image.Image) -> float:
    a_bytes, b_bytes = a.tobytes(), b.tobytes()
    inter = sum((x & y).bit_count() for x, y in zip(a_bytes, b_bytes, strict=True))
    union = sum((x | y).bit_count() for x, y in zip(a_bytes, b_bytes, strict=True))
    return inter / union if union else 0.0


class ReferenceFont:
    def __init__(self, path: str):
        font = TTFont(path)
        self.units_per_em = font["head"].unitsPerEm
        hmtx = font["hmtx"]
        # (codepoint, advance width, raster), lowest codepoint first so plain Latin wins ties.
        self.glyphs = [
            (cp, hmtx[name][0], _raster(font, name))
            for cp, name in sorted(font.getBestCmap().items())
            if _allowed(cp)
        ]


def load_reference_fonts(directory: str) -> dict[str, ReferenceFont]:
    """Loads whichever Carlito files exist in `directory` (e.g. `/usr/share/fonts/carlito`),
    keyed by style. Returns an empty dict if the folder or files are missing.
    """
    loaded = {}
    for style, filename in REFERENCE_FILES.items():
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            loaded[style] = ReferenceFont(path)
    return loaded


def style_of(base_font: str) -> str | None:
    """Returns the Carlito style to compare against, or None if this is not a Calibri font."""
    name = base_font.split("+", 1)[-1].lstrip("/")
    if not name.startswith("Calibri"):
        return None
    bold, italic = "Bold" in name, "Italic" in name
    return (
        "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    )


def _candidates(
    image: Image.Image, advance: float, reference: ReferenceFont
) -> list[tuple[float, int]]:
    """Reference characters with a similar width, best match first, as `(score, codepoint)`."""
    scored = [
        (_iou(image, ref_image), cp)
        for cp, ref_advance, ref_image in reference.glyphs
        if abs(ref_advance - advance) <= max(12, 0.02 * advance)
    ]
    return sorted(scored, key=lambda item: (-item[0], item[1]))


def _pick(scored: list[tuple[float, int]]) -> tuple[int, float] | None:
    """Chooses the character for one glyph, or None if nothing clearly matches."""
    if not scored:
        return None
    best_score, best_cp = scored[0]
    # An accented letter can edge out the plain one by a hair (Ľ vs L). A real accented glyph
    # beats the plain letter by a lot, so only switch to the plain letter on a near tie.
    for score, cp in scored:
        if cp < 0x80 and score >= best_score - ASCII_PREFERENCE:
            best_score, best_cp = score, cp
            break
    second = next((score for score, cp in scored if cp != best_cp and score < best_score), 0.0)
    if best_score >= MIN_IOU:
        return best_cp, best_score
    # Thin marks (- / ,) never overlap well, but they are still far closer to one character
    # than to any other, so accept a weaker score with a wide gap to the runner-up.
    if best_score >= MIN_IOU_THIN and best_score - second >= MIN_GAP_THIN:
        return best_cp, best_score
    return None


def _fill_from_neighbours(
    unmatched: dict[int, tuple[Image.Image, float]],
    mapping: dict[int, int],
    reference: ReferenceFont,
) -> None:
    """Calibri lists digits (and some other runs) in order, so glyph g sits between g-1 and
    g+1. If a neighbour is matched to `cp`, glyph g is probably `cp+1` (or `cp-1`). Only
    accepted for digits and plain letters, and only if the shape is still a plausible match.
    """
    for gid, (image, advance) in unmatched.items():
        guesses = set()
        if gid - 1 in mapping:
            guesses.add(mapping[gid - 1] + 1)
        if gid + 1 in mapping:
            guesses.add(mapping[gid + 1] - 1)
        if len(guesses) != 1:
            continue  # no neighbour, or the two neighbours disagree
        (cp,) = guesses
        if not (0x30 <= cp <= 0x39 or 0x41 <= cp <= 0x5A or 0x61 <= cp <= 0x7A):
            continue
        for ref_cp, ref_advance, ref_image in reference.glyphs:
            if ref_cp == cp and abs(ref_advance - advance) <= max(12, 0.02 * advance):
                if _iou(image, ref_image) >= MIN_IOU_NEIGHBOUR:
                    mapping[gid] = cp
                break


def match_glyphs(font_program: bytes, codes: set[int], reference: ReferenceFont) -> dict[int, int]:
    """Returns `{glyph id: Unicode}` for each used glyph that clearly matches one character.
    Assumes glyph id == CID (`/CIDToGIDMap /Identity`). A blank glyph with a width is a space.
    If two glyphs claim the same character, only the better match is kept.
    """
    font = TTFont(io.BytesIO(font_program), lazy=True)
    names = font.getGlyphOrder()
    hmtx = font["hmtx"]
    scale = reference.units_per_em / font["head"].unitsPerEm
    # Every glyph that still has an outline was kept because some page uses it, so match them
    # all: another page sharing this font then needs no second pass. Blank glyphs only count
    # when this page uses them (an unused blank slot is just an empty subset hole).
    glyf = font["glyf"]
    drawn = {i for i, n in enumerate(names) if glyf[n].numberOfContours != 0}
    mapping: dict[int, int] = {}
    scores: dict[int, float] = {}
    unmatched: dict[int, tuple[Image.Image, float]] = {}
    images: dict[int, Image.Image] = {}
    advances: dict[int, float] = {}
    for code in sorted(codes | drawn):
        if code >= len(names):
            continue
        name = names[code]
        image = _raster(font, name)
        advance = hmtx[name][0] * scale
        if not any(image.tobytes()):
            if code in codes and advance > 0:
                mapping[code] = 0x20
            continue
        images[code], advances[code] = image, advance
        picked = _pick(_candidates(image, advance, reference))
        if picked is None:
            unmatched[code] = (image, advance)
        else:
            mapping[code], scores[code] = picked

    # Two glyphs claiming one character (e.g. digit variants): keep a clearly better match,
    # and send the other (or both, on a near tie) back to be placed from their neighbours.
    owner: dict[int, int] = {}
    for code, cp in sorted(mapping.items()):
        if cp == 0x20:
            continue
        rival = owner.get(cp)
        if rival is None:
            owner[cp] = code
            continue
        if abs(scores[code] - scores[rival]) < TIE_MARGIN:
            losers, winner = [code, rival], None
        elif scores[code] > scores[rival]:
            losers, winner = [rival], code
        else:
            losers, winner = [code], rival
        for loser in losers:
            del mapping[loser]
            unmatched[loser] = (images[loser], advances[loser])
        if winner is None:
            del owner[cp]
        else:
            owner[cp] = winner

    # A placed glyph can unlock its neighbour, so repeat until nothing new is placed.
    while True:
        before = len(mapping)
        _fill_from_neighbours(unmatched, mapping, reference)
        for code in [c for c in unmatched if c in mapping]:
            del unmatched[code]
        if len(mapping) == before:
            break
    return mapping
