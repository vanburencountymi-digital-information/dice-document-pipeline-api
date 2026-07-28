from __future__ import annotations

import factory
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from accounts.models import Organization, ServiceAccount

User = get_user_model()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        # Service accounts authenticate via token, not credentials.
        self.set_unusable_password()
        if create:
            self.save()


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Organization {n}")


class ServiceAccountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ServiceAccount

    organization = factory.SubFactory(OrganizationFactory)
    user = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"service-account-{n}")

    @factory.post_generation
    def token(self, create, extracted, **kwargs):
        # Real service accounts always have a token (ServiceAccountService.create()).
        if create:
            Token.objects.get_or_create(user=self.user)
