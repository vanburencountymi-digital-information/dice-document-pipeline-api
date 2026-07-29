from rest_framework import serializers

from remediation.models import Remediation


class RemediationSerializer(serializers.ModelSerializer):
    document_id = serializers.CharField(source="content_hash", read_only=True)

    class Meta:
        model = Remediation
        fields = [
            "id",
            "document_id",
            "status",
            "error",
            "created_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = ["id", "status", "error", "created_at", "started_at", "completed_at"]
