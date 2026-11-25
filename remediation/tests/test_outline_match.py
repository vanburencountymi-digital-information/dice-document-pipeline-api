from __future__ import annotations

import os
import shutil
import tempfile

from django.test import SimpleTestCase
from parameterized import parameterized
from PIL import Image, ImageDraw

from remediation.adapters.font_repair import outline_match
from remediation.tests.helpers import build_shape_font

REFERENCE_CHARACTERS = {"triangle": ord("A"), "square": ord("B"), "bar": ord("C")}


def _blob(left: int, top: int, right: int, bottom: int) -> Image.Image:
    image = Image.new("1", (outline_match.GRID, outline_match.GRID), 0)
    ImageDraw.Draw(image).rectangle((left, top, right, bottom), fill=1)
    return image


class StyleOfTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("/ABCDEF+Calibri", "regular"),
            ("/ABCDEF+Calibri,Bold", "bold"),
            ("/ABCDEF+Calibri,Italic", "italic"),
            ("/ABCDEF+Calibri,BoldItalic", "bolditalic"),
            ("/ABCDEF+Calibri-Bold", "bold"),
            ("/ABCDEF+CambriaMath", None),
            ("/ABCDEF+Arial", None),
        ]
    )
    def test_style_of(self, base_font: str, expected: str | None) -> None:
        self.assertEqual(outline_match.style_of(base_font), expected)


class PickTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("clear_match", [(0.90, 0x41), (0.50, 0x42)], 0x41),
            (
                "plain_letter_beats_accent_on_near_tie",
                [(0.80, 0x13D), (0.76, 0x4C)],
                0x4C,
            ),
            ("real_accent_beats_plain_letter", [(0.95, 0xE9), (0.70, 0x65)], 0xE9),
            ("thin_mark_far_ahead_of_runner_up", [(0.40, 0x2D), (0.10, 0x29)], 0x2D),
            ("thin_mark_too_close_to_runner_up", [(0.40, 0x2D), (0.30, 0x29)], None),
            ("weak_match", [(0.30, 0x2D), (0.05, 0x29)], None),
            ("no_candidates", [], None),
        ]
    )
    def test_pick(self, _name: str, scored: list[tuple[float, int]], expected: int | None) -> None:
        picked = outline_match._pick(scored)

        self.assertEqual(None if picked is None else picked[0], expected)


class FillFromNeighboursTests(SimpleTestCase):
    def setUp(self) -> None:
        self.image = _blob(20, 20, 60, 60)
        self.reference = type(
            "Reference",
            (),
            {"glyphs": [(ord("3"), 600, self.image), (ord("%"), 600, self.image)]},
        )()

    @parameterized.expand(
        [
            ("both_neighbours_agree", {9: ord("2"), 11: ord("4")}, ord("3")),
            ("one_neighbour", {9: ord("2")}, ord("3")),
            ("neighbours_disagree", {9: ord("2"), 11: ord("9")}, None),
            ("no_neighbours", {}, None),
            ("not_a_letter_or_digit", {9: ord("$")}, None),  # next would be '%'
        ]
    )
    def test_fill(self, _name: str, mapping: dict[int, int], expected: int | None) -> None:
        mapping = dict(mapping)

        outline_match._fill_from_neighbours({10: (self.image, 600.0)}, mapping, self.reference)

        self.assertEqual(mapping.get(10), expected)

    def test_rejects_shape_that_does_not_fit(self) -> None:
        mapping = {9: ord("2")}

        outline_match._fill_from_neighbours(
            {10: (_blob(0, 0, 5, 5), 600.0)}, mapping, self.reference
        )

        self.assertNotIn(10, mapping)


class MatchGlyphsTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.directory = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.directory, ignore_errors=True)
        path = os.path.join(cls.directory, "reference.ttf")
        with open(path, "wb") as handle:
            handle.write(
                build_shape_font(["triangle", "square", "bar"], characters=REFERENCE_CHARACTERS)
            )
        cls.reference = outline_match.ReferenceFont(path)

    def test_maps_scrambled_glyph_ids_by_shape(self) -> None:
        # Glyph ids 1-5: blank, bar, triangle, square, and a shape that is nothing in the reference.
        program = build_shape_font([None, "bar", "triangle", "square", "sliver"])

        mapping = outline_match.match_glyphs(program, {1}, self.reference)

        self.assertEqual(mapping, {1: 0x20, 2: ord("C"), 3: ord("A"), 4: ord("B")})

    def test_blank_glyph_not_used_on_page_is_not_a_space(self) -> None:
        program = build_shape_font([None, "square"])

        mapping = outline_match.match_glyphs(program, set(), self.reference)

        self.assertEqual(mapping, {2: ord("B")})

    def test_two_glyphs_claiming_one_character_are_both_dropped(self) -> None:
        program = build_shape_font(["square", "square"])

        mapping = outline_match.match_glyphs(program, set(), self.reference)

        self.assertEqual(mapping, {})

    def test_glyph_id_past_the_font_is_ignored(self) -> None:
        program = build_shape_font(["square"])

        mapping = outline_match.match_glyphs(program, {99}, self.reference)

        self.assertEqual(mapping, {1: ord("B")})


class LoadReferenceFontsTests(SimpleTestCase):
    def test_missing_directory_gives_no_fonts(self) -> None:
        self.assertEqual(outline_match.load_reference_fonts("/nonexistent/fonts"), {})

    def test_loads_the_styles_that_exist(self) -> None:
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        with open(os.path.join(directory, "Carlito-Bold.ttf"), "wb") as handle:
            handle.write(build_shape_font(["square"], characters={"square": ord("B")}))

        loaded = outline_match.load_reference_fonts(directory)

        self.assertEqual(list(loaded), ["bold"])
