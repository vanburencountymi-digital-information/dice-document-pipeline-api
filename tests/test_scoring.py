from dice_document_pipeline.remediation.scoring import score_document


def base_issues(**overrides):
    issues = {
        "has_structure_tree": True,
        "has_text_layer": True,
        "images_without_alt": 0,
        "has_images": False,
        "form_fields_without_tooltips": 0,
        "has_headings": True,
        "has_bookmarks": True,
        "unembedded_fonts": [],
        "vague_links": False,
        "contrast_check_passed": True,
        "tables_without_headers": 0,
    }
    issues.update(overrides)
    return issues


def test_perfect_document_scores_a():
    score, grade, manual_items = score_document(base_issues(), page_count=3)

    assert score == 100
    assert grade == "A"
    assert manual_items == []


def test_missing_structure_and_text_are_critical_deductions():
    score, grade, manual_items = score_document(
        base_issues(has_structure_tree=False, has_text_layer=False),
        page_count=3,
    )

    assert score == 62
    assert grade == "C"
    assert len(manual_items) == 2
    assert "Structure tree missing" in manual_items[0]
    assert "No text layer" in manual_items[1]


def test_long_document_without_headings_or_bookmarks_gets_deductions():
    score, grade, manual_items = score_document(
        base_issues(has_headings=False, has_bookmarks=False),
        page_count=12,
    )

    assert score == 84
    assert grade == "B"
    assert any("heading structure" in item for item in manual_items)
    assert any("No bookmarks" in item for item in manual_items)


def test_score_never_goes_below_zero():
    score, grade, _ = score_document(
        base_issues(
            has_structure_tree=False,
            has_text_layer=False,
            images_without_alt=10,
            has_images=True,
            form_fields_without_tooltips=5,
            has_headings=False,
            has_bookmarks=False,
            unembedded_fonts=["A", "B", "C", "D"],
            vague_links=True,
            contrast_check_passed=False,
            tables_without_headers=3,
        ),
        page_count=12,
    )

    assert score == 3
    assert grade == "F"
