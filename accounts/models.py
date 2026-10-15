from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

TOKEN_NAME_MAX_LENGTH = 50
TOKEN_NAME_EXAMPLE = "wordpress-prod-2026"

UNLIMITED_LABEL = "Service Account has no limits"


# Functions, not values, so a migration records the function's name and changing the setting
# later doesn't make `makemigrations` report a change.
def default_max_in_flight() -> int:
    """Returns: the in-flight cap a new service account starts with (DEFAULT_MAX_IN_FLIGHT)."""
    return settings.DEFAULT_MAX_IN_FLIGHT


def default_rate_limit_count() -> int:
    """Returns: the submissions per period a new service account starts with."""
    return settings.DEFAULT_RATE_LIMIT_COUNT


class RateLimitPeriod(models.TextChoices):
    # Values are the words DRF's throttle rates use, e.g. "100/day". DRF doesn't export them,
    # so test_models checks each one against its parse_rate.
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"


DEFAULT_RATE_LIMIT_PERIOD = RateLimitPeriod.DAY
LIMIT_FIELDS = ("unlimited", "max_in_flight", "rate_limit_count", "rate_limit_period")


class IncompleteLimitsError(Exception):
    """An account is missing a limit value."""


# Create your models here.
class Organization(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self) -> str:
        return self.name


class ServiceAccount(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    # HMAC key for signing outgoing webhook payloads (ADR 0015) — generated once in
    # ServiceAccountService.create()
    webhook_secret = models.CharField(max_length=64, blank=True, default="")
    # The three limit values may be blank only when this is ticked; read them through
    # `in_flight_cap` and `rate_limit`, never directly.
    unlimited = models.BooleanField(
        UNLIMITED_LABEL,
        default=False,
        help_text="Check this box to remove all limits from the account, such as during a large backfill.",
    )
    max_in_flight = models.PositiveIntegerField(
        "Maximum submissions allowed at once, queued and working",
        null=True,
        blank=True,
        default=default_max_in_flight,
        validators=[MinValueValidator(1)],
        help_text="Most documents this account can have waiting or being worked on at once.",
    )
    rate_limit_count = models.PositiveIntegerField(
        "Submission limit per time period",
        null=True,
        blank=True,
        default=default_rate_limit_count,
        validators=[MinValueValidator(1)],
        help_text="Most submissions allowed in each time period.",
    )
    # NULL (not "") is the "not set" state the limits_set_unless_unlimited constraint checks
    rate_limit_period = models.CharField(  # noqa: DJ001
        "Time period",
        max_length=10,
        choices=RateLimitPeriod,
        null=True,
        blank=True,
        default=DEFAULT_RATE_LIMIT_PERIOD,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_service_account_name_per_organization",
            ),
            models.CheckConstraint(
                condition=models.Q(unlimited=True)
                | models.Q(
                    max_in_flight__isnull=False,
                    rate_limit_count__isnull=False,
                    rate_limit_period__isnull=False,
                ),
                name="limits_set_unless_unlimited",
                violation_error_message=f"Fill in all the limits, or tick '{UNLIMITED_LABEL}'.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.organization}: {self.name}"

    @property
    def in_flight_cap(self) -> int | None:
        """The most documents this account may have waiting or being worked on.

        Returns: None only if the account is unlimited.
        Raises: IncompleteLimitsError if the value is missing without `unlimited`. The
        database and admin refuse that state; if it exists anyway, callers must deny.
        """
        if self.unlimited:
            return None
        if self.max_in_flight is None:
            raise IncompleteLimitsError(self.pk)
        return self.max_in_flight

    @property
    def rate_limit(self) -> str | None:
        """The submission limit as DRF writes it, e.g. "100/day".

        Returns: None only if the account is unlimited.
        Raises: IncompleteLimitsError if the count or period is missing without `unlimited`.
        The database and admin refuse that state; if it exists anyway, callers must deny.
        """
        if self.unlimited:
            return None
        if self.rate_limit_count is None or self.rate_limit_period is None:
            raise IncompleteLimitsError(self.pk)
        return f"{self.rate_limit_count}/{self.rate_limit_period}"


class ServiceAccountToken(models.Model):
    """The name for a knox token. Created and revoked only through ServiceAccountService."""

    service_account = models.ForeignKey(
        "accounts.ServiceAccount", on_delete=models.CASCADE, related_name="tokens"
    )
    name = models.CharField(max_length=TOKEN_NAME_MAX_LENGTH)
    auth_token = models.OneToOneField(settings.KNOX_TOKEN_MODEL, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["service_account", "name"],
                name="unique_token_name_per_service_account",
            )
        ]

    def __str__(self) -> str:
        return f"{self.service_account}: {self.name}"
