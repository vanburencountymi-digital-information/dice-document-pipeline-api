from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from remediation.serializers import RemediationSerializer, RemediationUploadSerializer
from remediation.tests.factories import PdfUploadFactory, RemediationFactory


def _upload_serializer(
    filename: str, content: bytes = b"%PDF-1.4", content_type: str = "application/pdf"
) -> RemediationUploadSerializer:
    upload = PdfUploadFactory(name=filename, content=content, content_type=content_type)
    return RemediationUploadSerializer(data={"file": upload})


class RemediationUploadSerializerTests(SimpleTestCase):
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
        remediation = RemediationFactory(content_hash="abc123")

        data = RemediationSerializer(remediation).data

        self.assertEqual(
            set(data.keys()),
            {"id", "document_id", "status", "error", "created_at", "started_at", "completed_at"},
        )
        self.assertEqual(data["id"], str(remediation.id))
        self.assertEqual(data["document_id"], "abc123")
        self.assertEqual(data["status"], remediation.status)
