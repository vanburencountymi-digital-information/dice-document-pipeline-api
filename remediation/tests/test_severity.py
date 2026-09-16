from __future__ import annotations

import json
from pathlib import Path

from django.test import SimpleTestCase
from parameterized import parameterized

from remediation.adapters.verification.severity import (
    CLAUSE_SEVERITY,
    RULE_SEVERITY,
    Severity,
    classify,
)

CATALOG_PATH = Path(__file__).resolve().parent.parent / "adapters/verification/pdfua1_catalog.json"


class ClassifyTests(SimpleTestCase):
    @parameterized.expand([(key, severity) for key, severity in RULE_SEVERITY.items()])
    def test_classify_returns_expected_severity_for_exact_rule_override(
        self, clause_test_number, expected_severity
    ) -> None:
        clause, test_number = clause_test_number.rsplit("-", 1)
        self.assertEqual(classify(clause, test_number), expected_severity)

    @parameterized.expand([(clause, severity) for clause, severity in CLAUSE_SEVERITY.items()])
    def test_classify_returns_expected_severity_for_known_clause(
        self, clause, expected_severity
    ) -> None:
        self.assertEqual(classify(clause, "1"), expected_severity)

    @parameterized.expand(
        [
            ("dotted_suffix_matches_parent_clause", "7.21.7", "1", Severity.MINOR),
            ("multi_segment_suffix_matches_parent_clause", "7.21.4.2", "1", Severity.MINOR),
            ("clause_7_10_does_not_match_7_1s_prefix", "7.10", "1", Severity.MINOR),
        ]
    )
    def test_classify_uses_longest_dot_bounded_prefix_match(
        self, _name, clause, test_number, expected_severity
    ) -> None:
        self.assertEqual(classify(clause, test_number), expected_severity)

    def test_classify_checks_exact_rule_override_before_falling_back_to_clause_prefix(
        self,
    ) -> None:
        # 7.1 has no CLAUSE_SEVERITY prefix entry at all (every one of its rules is only
        # reachable via the exact RULE_SEVERITY override) — this proves that path is really
        # being taken, not silently falling through to UNCLASSIFIED.
        self.assertNotIn("7.1", CLAUSE_SEVERITY)
        self.assertEqual(classify("7.1", "11"), Severity.CRITICAL)
        self.assertEqual(classify("7.1", "4"), Severity.MINOR)

    @parameterized.expand(
        [
            ("completely_unknown_clause", "99.99", "1"),
            ("empty_clause", "", "1"),
            ("unmapped_test_number_under_a_mixed_clause", "7.1", "999"),
        ]
    )
    def test_classify_returns_unclassified_for_unmapped_rule(
        self, _name, clause, test_number
    ) -> None:
        self.assertEqual(classify(clause, test_number), Severity.UNCLASSIFIED)

    def test_classify_always_returns_a_severity_member(self) -> None:
        for clause in [*CLAUSE_SEVERITY, "7.21.7", "unmapped", ""]:
            with self.subTest(clause=clause):
                self.assertIsInstance(classify(clause, "1"), Severity)


class RealCatalogCoverageTests(SimpleTestCase):
    """Confirms `classify()` covers every rule veraPDF 1.30.2's actual PDF/UA-1 profile
    contains — not just the ones sampled from one failing document
    (simplify_vera_printouts). The snapshot is produced by
    `manage.py extract_verapdf_profile`; re-run it after any veraPDF upgrade and re-run
    these tests to see exactly what's now unclassified.
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with open(CATALOG_PATH) as f:
            cls.catalog = json.load(f)

    def test_catalog_snapshot_has_the_expected_rule_count(self) -> None:
        # A change here means veraPDF's profile changed — re-review classify() coverage
        # below before just bumping this number.
        self.assertEqual(self.catalog["rule_count"], 106)
        self.assertEqual(len(self.catalog["rules"]), 106)

    def test_classify_has_no_unclassified_rules_in_the_real_catalog(self) -> None:
        unclassified = [
            f"{rule['clause']}-{rule['test_number']}: {rule['description']}"
            for rule in self.catalog["rules"]
            if classify(rule["clause"], rule["test_number"]) is Severity.UNCLASSIFIED
        ]
        self.assertEqual(unclassified, [])
