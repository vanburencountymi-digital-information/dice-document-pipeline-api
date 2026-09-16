from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from remediation.models import Remediation, VerificationResult
from remediation.services import build_download_url


class RemediationUploadSerializer(serializers.Serializer):
    """Checks for pdf file and if should force re-running job"""

    file = serializers.FileField()
    force = serializers.BooleanField(required=False, default=False)

    def validate_file(self, value: UploadedFile) -> UploadedFile:
        if not value.name or not value.name.lower().endswith(".pdf"):
            raise serializers.ValidationError("file must be a PDF.")
        return value


class VerificationResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationResult
        fields = ["step", "is_compliant", "verapdf_version", "failed_rules"]
        read_only_fields = fields


class RemediationSerializer(serializers.ModelSerializer):
    document_id = serializers.CharField(source="content_hash", read_only=True)
    verification_results = VerificationResultSerializer(many=True, read_only=True)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Remediation
        fields = [
            "id",
            "document_id",
            "original_filename",
            "status",
            "error",
            "created_at",
            "started_at",
            "completed_at",
            "verification_results",
            "download_url",
        ]
        read_only_fields = [
            "id",
            "original_filename",
            "status",
            "error",
            "created_at",
            "started_at",
            "completed_at",
            "verification_results",
            "download_url",
        ]

    def get_download_url(self, obj: Remediation) -> str | None:
        # Present whenever final_output_uri is set, not gated on status == COMPLETE —
        # a FAILED attempt can still have a partially-remediated file worth downloading
        # (ADR 0013/0015).
        return build_download_url(obj) if obj.final_output_uri else None
