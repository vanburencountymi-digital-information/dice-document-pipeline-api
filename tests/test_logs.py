from pathlib import Path

from dice_document_pipeline.remediation.logs import build_remediation_log, write_log


def test_build_remediation_log_includes_core_sections():
    body = build_remediation_log(
        filename="sample.pdf",
        issues={
            "page_count": 12,
            "has_structure_tree": False,
            "has_text_layer": True,
            "has_headings": False,
            "has_bookmarks": False,
            "contrast_check_passed": True,
        },
        fixes=["Metadata: title set"],
        alt_log_entries=["ALT TEXT: 1 image processed"],
        manual_items=["Review heading structure"],
        score=84,
        grade="B",
        county_name="Van Buren County",
        log_date="2026-05-27",
    )

    assert "ADA REMEDIATION LOG" in body
    assert "sample.pdf" in body
    assert "ISSUES FOUND" in body
    assert "ISSUES FIXED" in body
    assert "MANUAL REVIEW NEEDED" in body
    assert "No structure tree" in body
    assert "Metadata: title set" in body
    assert "Review heading structure" in body


def test_write_log_writes_utf8_file(tmp_path: Path):
    log_path = tmp_path / "sample_log.txt"

    write_log(
        log_path,
        filename="sample.pdf",
        issues={"page_count": 1, "contrast_check_passed": True},
        fixes=[],
        alt_log_entries=[],
        manual_items=[],
        score=100,
        grade="A",
        county_name="Van Buren County",
        log_date="2026-05-27",
    )

    assert log_path.read_text(encoding="utf-8").startswith("=" * 70)
