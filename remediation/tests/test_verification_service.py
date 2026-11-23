from __future__ import annotations

import tempfile
from unittest.mock import create_autospec

from django.test import TestCase, override_settings
from parameterized import parameterized

from remediation.adapters.base import (
    AdapterError,
    VerificationOutcome,
)
from remediation.adapters.verification.severity import Severity
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.models import (
    RemediationArtifact,
    VerificationResult,
)
from remediation.services.verification_service import (
    AlreadyCompliant,
    NotCompliant,
    PostCheckService,
    PostCheckUnavailable,
    PrecheckService,
)
from remediation.tests.factories import (
    FailedRuleFactory,
    RemediationArtifactFactory,
    RemediationFactory,
    VerificationResultFactory,
)
from remediation.verification_types import FailedRule


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class VerificationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.remediation = RemediationFactory(
            source_pdf_uri="remediations/test.pdf", with_stored_file=True
        )

    def setUp(self) -> None:
        self.adapter = create_autospec(VeraPDFAdapter, spec_set=True)

    @parameterized.expand(
        [
            (
                "precheck_compliant_raises_already_compliant",
                PrecheckService,
                True,
                AlreadyCompliant,
            ),
            ("precheck_noncompliant_continues", PrecheckService, False, None),
            ("postcheck_compliant_continues", PostCheckService, True, None),
            ("postcheck_noncompliant_raises_not_compliant", PostCheckService, False, NotCompliant),
        ]
    )
    def test_run_signals_based_on_compliance(
        self, _name, service_cls, is_compliant, expected_exception
    ) -> None:
        self.adapter.validate.return_value = VerificationOutcome(
            is_compliant=is_compliant, failed_rules=[], verapdf_version="1.30.2"
        )
        service = service_cls(adapter=self.adapter)

        if expected_exception is not None:
            with self.assertRaises(expected_exception):
                service.run(self.remediation, pdf_uri="remediations/test.pdf")
        else:
            result = service.run(self.remediation, pdf_uri="remediations/test.pdf")
            self.assertEqual(result, "remediations/test.pdf")

        artifact = self.remediation.artifacts.get(step=service.step)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        self.assertEqual(artifact.output_uri, "remediations/test.pdf")

        result_row = VerificationResult.objects.get(remediation=self.remediation, step=service.step)
        self.assertEqual(result_row.is_compliant, is_compliant)
        self.assertEqual(result_row.failed_rules, [])
        self.assertIsNone(result_row.worst_severity)
        self.assertEqual(result_row.verapdf_version, "1.30.2")

    @parameterized.expand(
        [
            ("precheck_compliant", PrecheckService, True, AlreadyCompliant),
            ("precheck_noncompliant", PrecheckService, False, None),
            ("postcheck_compliant", PostCheckService, True, None),
            ("postcheck_noncompliant", PostCheckService, False, NotCompliant),
        ]
    )
    def test_run_replays_stored_verdict_without_calling_adapter(
        self, _name, service_cls, is_compliant, expected_exception
    ) -> None:
        """ADR 0019 — on redelivery the stored verdict must still raise the same signal,
        or the pipeline would carry on past a decision the first run already made.
        """
        remediation = RemediationFactory()
        RemediationArtifactFactory(
            remediation=remediation,
            step=service_cls.step,
            status=RemediationArtifact.StepStatus.COMPLETED,
            output_uri="remediations/test.pdf",
        )
        VerificationResultFactory(
            remediation=remediation,
            step=service_cls.step,
            is_compliant=is_compliant,
            failed_rules=[] if is_compliant else [FailedRuleFactory()],
        )
        service = service_cls(adapter=self.adapter)

        if expected_exception is not None:
            with self.assertRaises(expected_exception):
                service.run(remediation, pdf_uri="remediations/test.pdf")
        else:
            result = service.run(remediation, pdf_uri="remediations/test.pdf")
            self.assertEqual(result, "remediations/test.pdf")

        self.adapter.validate.assert_not_called()
        self.assertEqual(
            VerificationResult.objects.filter(
                remediation=remediation, step=service_cls.step
            ).count(),
            1,
        )

    @parameterized.expand(
        [
            ("precheck", PrecheckService),
            ("postcheck", PostCheckService),
        ]
    )
    def test_run_raises_when_completed_step_has_no_stored_verdict(self, _name, service_cls) -> None:
        """ADR 0019 — a COMPLETED verification step without its `VerificationResult`
        shouldn't be possible (the result is saved first). If it happens anyway, fail loudly
        rather than silently skipping the verdict or quietly re-running the check.
        """
        remediation = RemediationFactory()
        RemediationArtifactFactory(
            remediation=remediation,
            step=service_cls.step,
            status=RemediationArtifact.StepStatus.COMPLETED,
            output_uri="remediations/test.pdf",
        )
        service = service_cls(adapter=self.adapter)

        with self.assertRaises(VerificationResult.DoesNotExist):
            service.run(remediation, pdf_uri="remediations/test.pdf")

        self.adapter.validate.assert_not_called()

    @parameterized.expand(
        [
            ("adapter_error", AdapterError("boom")),
            ("unexpected_error", RuntimeError("boom")),
        ]
    )
    def test_precheck_records_failed_artifact_and_continues_on_adapter_error(
        self, _name, side_effect
    ) -> None:
        self.adapter.validate.side_effect = side_effect
        service = PrecheckService(adapter=self.adapter)

        result = service.run(self.remediation, pdf_uri="remediations/test.pdf")

        self.assertEqual(result, "remediations/test.pdf")
        artifact = self.remediation.artifacts.get(step=service.step)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "boom")
        self.assertFalse(
            VerificationResult.objects.filter(
                remediation=self.remediation, step=service.step
            ).exists()
        )

    @parameterized.expand(
        [
            ("adapter_error", AdapterError("boom")),
            ("unexpected_error", RuntimeError("boom")),
        ]
    )
    def test_postcheck_records_failed_artifact_and_raises_unavailable_on_adapter_error(
        self, _name, side_effect
    ) -> None:
        self.adapter.validate.side_effect = side_effect
        service = PostCheckService(adapter=self.adapter)

        with self.assertRaises(PostCheckUnavailable) as ctx:
            service.run(self.remediation, pdf_uri="remediations/test.pdf")

        self.assertEqual(str(ctx.exception), "postcheck could not run: boom")
        artifact = self.remediation.artifacts.get(step=service.step)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "boom")

    def test_run_stores_failed_rules_with_severity_on_verification_result(self) -> None:
        failed_rules = [
            FailedRule(
                clause="7.4",
                test_number="2",
                description="heading levels skip",
                failed_checks=3,
                severity=Severity.CRITICAL,
            ),
            FailedRule(
                clause="7.21",
                test_number="7",
                description="font missing CIDSet entries",
                failed_checks=44,
                severity=Severity.MINOR,
            ),
        ]
        self.adapter.validate.return_value = VerificationOutcome(
            is_compliant=False, failed_rules=failed_rules, verapdf_version="1.30.2"
        )
        service = PostCheckService(adapter=self.adapter)

        with self.assertRaises(NotCompliant):
            service.run(self.remediation, pdf_uri="remediations/test.pdf")

        result_row = VerificationResult.objects.get(
            remediation=self.remediation, step=RemediationArtifact.Step.POSTCHECK
        )
        self.assertEqual(result_row.verapdf_version, "1.30.2")
        self.assertEqual(
            result_row.failed_rules,
            [
                {
                    "clause": "7.4",
                    "test_number": "2",
                    "description": "heading levels skip",
                    "failed_checks": 3,
                    "severity": "critical",
                },
                {
                    "clause": "7.21",
                    "test_number": "7",
                    "description": "font missing CIDSet entries",
                    "failed_checks": 44,
                    "severity": "minor",
                },
            ],
        )
        self.assertEqual(result_row.worst_severity, Severity.CRITICAL)

    def test_not_compliant_message_is_short_human_readable_summary_not_raw_xml(self) -> None:
        # MINOR listed first here, deliberately out of severity order, so the assertion
        # below actually exercises the worst-first sort rather than passing by coincidence.
        failed_rules = [
            FailedRule(
                clause="7.21",
                test_number="7",
                description="font missing CIDSet entries",
                failed_checks=44,
                severity=Severity.MINOR,
            ),
            FailedRule(
                clause="7.4",
                test_number="2",
                description="heading levels skip",
                failed_checks=3,
                severity=Severity.CRITICAL,
            ),
        ]
        self.adapter.validate.return_value = VerificationOutcome(
            is_compliant=False, failed_rules=failed_rules, verapdf_version="1.30.2"
        )
        service = PostCheckService(adapter=self.adapter)

        with self.assertRaises(NotCompliant) as ctx:
            service.run(self.remediation, pdf_uri="remediations/test.pdf")

        message = str(ctx.exception)
        self.assertIn("2 rules failed, 47 checks", message)
        self.assertIn("CRITICAL", message)
        self.assertIn("7.4", message)
        self.assertIn("MINOR", message)
        self.assertIn("7.21", message)
        # Worst severity listed first, regardless of the adapter's own ordering.
        self.assertLess(message.index("CRITICAL"), message.index("MINOR"))
        self.assertNotIn("<", message)
