from typing import Any

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpRequest

from accounts.models import Organization, ServiceAccount
from accounts.services import ServiceAccountService


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(ServiceAccount)
class ServiceAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    actions = ["issue_token"]

    @staticmethod
    def _token_message(account_name: str, token: str) -> str:
        return f"{account_name}: {token} — copy this now, it can't be shown again."

    def get_form(
        self,
        request: HttpRequest,
        obj: ServiceAccount | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[ModelForm]:
        """On Add, only ask for what a person chooses; the login and webhook secret are generated."""
        if obj is None:
            kwargs["fields"] = ("organization", "name")
        return super().get_form(request, obj, change=change, **kwargs)

    def get_readonly_fields(
        self, request: HttpRequest, obj: ServiceAccount | None = None
    ) -> tuple[str, ...]:
        return ("user", "webhook_secret") if obj else ()

    def save_model(
        self, request: HttpRequest, obj: ServiceAccount, form: ModelForm, change: bool
    ) -> None:
        """Creating credentials is opt-in: only an Add (`change` is False) does it. Anything
        else is an ordinary save."""
        if not change:
            self._create_with_login(request, obj)
            return
        super().save_model(request, obj, form, change)

    def _create_with_login(self, request: HttpRequest, obj: ServiceAccount) -> None:
        """Go through the service so the login, webhook secret and first token are always
        created (a bare `obj.save()` would skip them)."""
        if obj.pk is not None:
            raise ValueError("Cannot create credentials for an account that already exists.")
        created, token = ServiceAccountService().create(obj.organization, obj.name)
        # Admin carries on with `obj` after this (logging, redirect), so make it the saved row
        obj.__dict__.update(created.__dict__)
        self.message_user(request, self._token_message(obj.name, token), level=messages.WARNING)

    @admin.action(description="Issue a new token")
    def issue_token(self, request: HttpRequest, queryset: QuerySet[ServiceAccount]) -> None:
        service = ServiceAccountService()
        for service_account in queryset:
            token = service.issue_token(service_account)
            self.message_user(
                request, self._token_message(service_account.name, token), level=messages.WARNING
            )
