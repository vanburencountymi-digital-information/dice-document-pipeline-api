from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.authtoken.models import Token

from accounts.models import Organization, ServiceAccount

User = get_user_model()


class ServiceAccountService:
    @transaction.atomic
    def create(self, organization: Organization, service_name: str) -> ServiceAccount:
        """Sets up a new ServiceAccount for the organization using the service_name provided."""
        user = User.objects.create_user(username=service_name, password=None)
        Token.objects.get_or_create(user=user)
        service_account = ServiceAccount.objects.create(
            organization=organization, user=user, name=service_name
        )
        return service_account
