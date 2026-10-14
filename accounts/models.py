from django.conf import settings
from django.db import models

TOKEN_NAME_MAX_LENGTH = 50
TOKEN_NAME_EXAMPLE = "wordpress-prod-2026"


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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_service_account_name_per_organization",
            )
        ]

    def __str__(self) -> str:
        return f"{self.organization}: {self.name}"


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
