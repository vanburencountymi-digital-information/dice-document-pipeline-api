import secrets
import uuid
from collections.abc import Iterable

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from knox.models import AuthToken

from accounts.models import Organization, ServiceAccount, ServiceAccountToken

User = get_user_model()


class TokenNameTakenError(Exception):
    """The account already has a token with that name."""


class ServiceAccountService:
    @transaction.atomic
    def create(
        self, organization: Organization, service_name: str, token_name: str
    ) -> tuple[ServiceAccount, str]:
        """Sets up a new ServiceAccount for the organization using the service_name provided,
        with a first token named token_name."""
        # Names are only unique per organization, but usernames are global.
        user = User.objects.create_user(username=f"service-{uuid.uuid4().hex}", password=None)
        service_account = ServiceAccount.objects.create(
            organization=organization,
            user=user,
            name=service_name,
            webhook_secret=secrets.token_hex(32),
        )
        token = self.issue_token(service_account, token_name)
        return service_account, token

    def issue_token(self, service_account: ServiceAccount, token_name: str) -> str:
        """Mints a new named knox token for the account.

        Takes: the ServiceAccount and a name no other token on it uses.
        Returns: the token string. It cannot be looked up again later.
        Raises: TokenNameTakenError if the name is used. Nothing is created then.
        """
        try:
            with transaction.atomic():
                auth_token, token = AuthToken.objects.create(user=service_account.user)
                ServiceAccountToken.objects.create(
                    service_account=service_account, name=token_name, auth_token=auth_token
                )
        except IntegrityError as error:
            raise TokenNameTakenError(token_name) from error
        return token

    @transaction.atomic
    def revoke_tokens(self, tokens: Iterable[ServiceAccountToken]) -> None:
        """Stops each token working, all or none. Deleting the knox token also removes the
        named row.

        Takes: the ServiceAccountTokens to revoke.
        """
        for token in tokens:
            token.auth_token.delete()

    def revoke_token(self, token: ServiceAccountToken) -> None:
        """Stops one token working. Takes: the ServiceAccountToken."""
        self.revoke_tokens([token])

    @transaction.atomic
    def delete(self, service_account: ServiceAccount) -> None:
        """Deletes the account, its login and every token. Deleting only the account would
        leave the login and its knox tokens working.

        Takes: the ServiceAccount to remove.
        """
        service_account.user.delete()

    def regenerate_webhook_secret(self, service_account: ServiceAccount) -> str:
        """Replaces the webhook secret. The receiver must be updated with the new one.

        Takes: the ServiceAccount.
        Returns: the new secret (str). The old one stops being used.
        """
        service_account.webhook_secret = secrets.token_hex(32)
        service_account.save(update_fields=["webhook_secret"])
        return service_account.webhook_secret
