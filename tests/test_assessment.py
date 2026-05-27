from pathlib import Path

from dice_document_pipeline.remediation.assessment import assess_document, default_issues


def test_default_issues_contains_complete_assessment_shape():
    issues = default_issues()

    assert issues["has_structure_tree"] is False
    assert issues["has_text_layer"] is False
    assert issues["page_count"] == 0
    assert issues["unembedded_fonts"] == []
    assert issues["vague_links"] == []
    assert issues["tables_without_headers"] == 0


def test_assess_document_returns_defaults_when_pdf_dependencies_are_unavailable():
    issues = assess_document(Path("missing.pdf"))

    assert issues == default_issues()
