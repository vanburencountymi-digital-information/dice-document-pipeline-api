from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from parameterized import parameterized

from remediation.adapters.verification.severity import Severity
from remediation.models import RemediationArtifact
from remediation.serializers import RemediationSerializer, RemediationUploadSerializer
from remediation.tests.factories import (
    FailedRuleFactory,
    PdfUploadFactory,
    RemediationFactory,
    VerificationResultFactory,
)


def _upload_serializer(
    filename: str, content: bytes = b"%PDF-1.4", content_type: str = "application/pdf"
) -> RemediationUploadSerializer:
    upload = PdfUploadFactory(name=filename, content=content, content_type=content_type)
    return RemediationUploadSerializer(data={"file": upload})


class RemediationUploadSerializerTests(SimpleTestCase):
    @override_settings(WEBHOOK_ALLOW_PRIVATE_URLS=False)
    @patch("remediation.webhook_client.socket.getaddrinfo", autospec=True)
    def test_rejects_a_callback_url_pointing_at_an_internal_address(self, mock_lookup) -> None:
        mock_lookup.return_value = [(2, 1, 6, "", ("169.254.169.254", 443))]
        upload = PdfUploadFactory(name="test.pdf")

        serializer = RemediationUploadSerializer(
            data={"file": upload, "callback_url": "https://sneaky.example.com/hook"}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("callback_url", serializer.errors)

    def test_accepts_pdf_file(self) -> None:
        serializer = _upload_serializer("test.pdf")

        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["file"].name, "test.pdf")

    def test_accepts_uppercase_pdf_extension(self) -> None:
        serializer = _upload_serializer("TEST.PDF")

        self.assertTrue(serializer.is_valid())

    def test_rejects_non_pdf_extension(self) -> None:
        serializer = _upload_serializer("test.txt", content=b"not a pdf", content_type="text/plain")

        self.assertFalse(serializer.is_valid())
        self.assertIn("file", serializer.errors)

    def test_requires_file(self) -> None:
        serializer = RemediationUploadSerializer(data={})

        self.assertFalse(serializer.is_valid())
        self.assertIn("file", serializer.errors)


class RemediationSerializerTests(TestCase):
    def test_serializes_expected_fields(self) -> None:
        remediation = RemediationFactory(content_hash="abc123", original_filename="test.pdf")

        data = RemediationSerializer(remediation).data

        self.assertEqual(
            set(data.keys()),
            {
                "id",
                "document_id",
                "original_filename",
                "status",
                "pipeline_version",
                "error",
                "created_at",
                "started_at",
                "completed_at",
                "verification_results",
                "download_url",
            },
        )
        self.assertEqual(data["id"], str(remediation.id))
        self.assertEqual(data["document_id"], "abc123")
        self.assertEqual(data["original_filename"], "test.pdf")
        self.assertEqual(data["status"], remediation.status)

    @parameterized.expand(
        [
            ("released_version", "1.4.2"),
            # Rows created before ADR 0012 have no version recorded.
            ("pre_versioning_row", ""),
        ]
    )
    def test_serializes_pipeline_version_the_attempt_ran_on(self, _name, version) -> None:
        remediation = RemediationFactory(pipeline_version=version)

        data = RemediationSerializer(remediation).data

        self.assertEqual(data["pipeline_version"], version)

    def test_pipeline_version_is_read_only(self) -> None:
        remediation = RemediationFactory(pipeline_version="1.4.2")

        serializer = RemediationSerializer(
            remediation, data={"pipeline_version": "9.9.9"}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        remediation.refresh_from_db()
        self.assertEqual(remediation.pipeline_version, "1.4.2")

    def test_serializes_empty_verification_results_when_none_recorded(self) -> None:
        remediation = RemediationFactory()

        data = RemediationSerializer(remediation).data

        self.assertEqual(data["verification_results"], [])

    def test_serializes_verification_results_with_failed_rules(self) -> None:
        remediation = RemediationFactory()
        failed_rule = FailedRuleFactory(clause="7.4", severity=Severity.CRITICAL.value)
        VerificationResultFactory(
            remediation=remediation,
            step=RemediationArtifact.Step.POSTCHECK,
            is_compliant=False,
            failed_rules=[failed_rule],
        )

        data = RemediationSerializer(remediation).data

        self.assertEqual(
            data["verification_results"],
            [
                {
                    "step": RemediationArtifact.Step.POSTCHECK.value,
                    "is_compliant": False,
                    "verapdf_version": "1.30.2",
                    "failed_rules": [failed_rule],
                }
            ],
        )
