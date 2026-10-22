from rest_framework import serializers

from common.throttle_scopes import LimitKind


class ForbiddenSerializer(serializers.Serializer):
    """Body of the 403 for an account whose limits are not fully set. An administrator has to
    fix the account."""

    detail = serializers.CharField()


class IPThrottledSerializer(serializers.Serializer):
    """Body of the 429 for too many requests from one IP address, or too many wrong tokens.
    `Retry-After` says how many seconds to wait."""

    detail = serializers.CharField()


class SubmissionLimitSerializer(serializers.Serializer):
    """Body of the 429 for a used-up submission limit."""

    detail = serializers.CharField()
    limit = serializers.ChoiceField(
        choices=[kind.value for kind in LimitKind],
        help_text="Which limit was hit.",
    )
    retry_after = serializers.IntegerField(help_text="Seconds to wait before sending again.")
    remaining = serializers.IntegerField()
    resets_at = serializers.DateTimeField()
