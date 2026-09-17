import secrets

from django.contrib.auth import get_user_model
from django.db import transaction
from knox.models import AuthToken

from accounts.models import Organization, ServiceAccount

User = get_user_model()


class ServiceAccountService:
    @transaction.atomic
    def create(self, organization: Organization, service_name: str) -> tuple[ServiceAccount, str]:
        """Sets up a new ServiceAccount for the organization using the service_name provided."""
        user = User.objects.create_user(username=service_name, password=None)
        service_account = ServiceAccount.objects.create(
            organization=organization,
            user=user,
            name=service_name,
            webhook_secret=secrets.token_hex(32),
        )
        token = self.issue_token(service_account)
        return service_account, token

    def issue_token(self, service_account: ServiceAccount) -> str:
        """Mints a new knox token for the account.
        Cannot be looked up again later.
        """
        _, token = AuthToken.objects.create(user=service_account.user)
        return token
