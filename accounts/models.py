from django.conf import settings
from django.db import models


# Create your models here.
class Organization(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self) -> str:
        return self.name


class ServiceAccount(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    def __str__(self) -> str:
        return f"{self.organization}: {self.name}"

    @property
    def token(self) -> str:
        return self.user.auth_token.key
