from django.conf import settings
from django.db import models


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
                fields=["organization", "name"], name="unique_service_account_name_per_organization"
            )
        ]

    def __str__(self) -> str:
        return f"{self.organization}: {self.name}"
