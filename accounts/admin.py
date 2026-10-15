from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from knox.models import AuthToken

from accounts.models import (
    LIMIT_FIELDS,
    TOKEN_NAME_EXAMPLE,
    TOKEN_NAME_MAX_LENGTH,
    IncompleteLimitsError,
    Organization,
    ServiceAccount,
    ServiceAccountToken,
)
from accounts.services import (
    ServiceAccountNameTakenError,
    ServiceAccountService,
    TokenNameTakenError,
)

# Tokens are only created and revoked through ServiceAccountService, never knox's own admin.
admin.site.unregister(AuthToken)


class LimitDisplay(StrEnum):
    """What the account list shows in place of a number."""

    UNLIMITED = "unlimited"
    INCOMPLETE = "incomplete"


class IssueTokenForm(forms.Form):
    """Whether the name is already taken is decided by the service, not here, so it can't
    disagree with the database."""

    name = forms.CharField(
        max_length=TOKEN_NAME_MAX_LENGTH,
        help_text=f"What this token is for, e.g. {TOKEN_NAME_EXAMPLE}.",
    )


class ServiceAccountAddForm(forms.ModelForm):
    """What a person chooses when adding an account. The login and webhook secret are generated."""

    token_name = forms.CharField(
        max_length=TOKEN_NAME_MAX_LENGTH,
        help_text=f"Name for the account's first token, e.g. {TOKEN_NAME_EXAMPLE}.",
    )

    class Meta:
        model = ServiceAccount
        fields = ("organization", "name", *LIMIT_FIELDS, "token_name")


class TokenColumnsMixin:
    """The `token_prefix` and `created` columns, read from the token's knox record."""

    @admin.display(description="Starts with")
    def token_prefix(self, obj: ServiceAccountToken) -> str:
        return obj.auth_token.token_key

    @admin.display(description="Created")
    def created(self, obj: ServiceAccountToken) -> datetime:
        return obj.auth_token.created


class TokenInline(TokenColumnsMixin, admin.TabularInline):
    """Lists the account's tokens. Read-only: issue and revoke go through the service."""

    model = ServiceAccountToken
    fields = ("name", "token_prefix", "created")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: ServiceAccount | None = None) -> bool:
        return False


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(ServiceAccount)
class ServiceAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "in_flight_cap", "rate_limit")
    inlines = [TokenInline]

    @staticmethod
    def _limit_text(read: Callable[[], object]) -> str:
        """Shows a limit for the list. A row missing a value must not crash the whole page, so
        it reads as "incomplete" instead of raising.

        Takes: a function that returns the limit (None means unlimited).
        Returns: the text to show.
        """
        try:
            value = read()
        except IncompleteLimitsError:
            return LimitDisplay.INCOMPLETE
        return LimitDisplay.UNLIMITED if value is None else str(value)

    @admin.display(description="Max at once")
    def in_flight_cap(self, obj: ServiceAccount) -> str:
        """Takes: a ServiceAccount. Returns: its in-flight cap as list text."""
        return self._limit_text(lambda: obj.in_flight_cap)

    @admin.display(description="Submission limit")
    def rate_limit(self, obj: ServiceAccount) -> str:
        """Takes: a ServiceAccount. Returns: its submission limit (e.g. 100/day) as list text."""
        return self._limit_text(lambda: obj.rate_limit)

    @staticmethod
    def _token_message(account_name: str, token: str) -> str:
        # `user-select: all` makes one click select the whole token for copying
        return format_html(
            '{}: <code style="user-select: all">{}</code> — copy this now, it can\'t be shown again.',
            account_name,
            token,
        )

    def add_view(
        self, request: HttpRequest, form_url: str = "", extra_context: dict | None = None
    ) -> HttpResponse:
        """The Add form already refuses a name the organization uses. This catches the same name
        arriving twice at once (a double-click on Save), which the form can't see, and shows a
        message instead of a server error.

        Takes: the Add request, the form URL and any extra template context.
        Returns: the normal Add response, or, when the name was taken at the same moment, a
        redirect back to the Add page with an error message.
        """
        try:
            return super().add_view(request, form_url, extra_context)
        except ServiceAccountNameTakenError:
            self.message_user(
                request,
                "That organization already has a service account with that name.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(request.path)

    def get_form(
        self,
        request: HttpRequest,
        obj: ServiceAccount | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[ModelForm]:
        """On Add, only ask for what a person chooses; the login and webhook secret are generated."""
        if obj is None:
            kwargs["form"] = ServiceAccountAddForm
        return super().get_form(request, obj, change=change, **kwargs)

    def get_readonly_fields(
        self, request: HttpRequest, obj: ServiceAccount | None = None
    ) -> tuple[str, ...]:
        return ("user", "webhook_secret") if obj else ()

    def save_model(
        self, request: HttpRequest, obj: ServiceAccount, form: ModelForm, change: bool
    ) -> None:
        """Creating credentials is opt-in: only an Add (`change` is False) does it. Anything
        else is an ordinary save of the edited fields.

        Takes: the request, the ServiceAccount, its submitted form and whether this is an edit.
        Returns: nothing. Add also queues a message with the first token.
        """
        if not change:
            self._create_with_login(request, obj, form.cleaned_data["token_name"])
            return
        # Save only edited fields so a stale form can't overwrite a webhook secret regenerated
        # since it was opened. Limits are only edited here, so there's no service method for it.
        obj.save(update_fields=form.changed_data)

    def _create_with_login(
        self, request: HttpRequest, obj: ServiceAccount, token_name: str
    ) -> None:
        """Creates through ServiceAccountService so login, webhook secret and first token are always
        created (a bare `obj.save()` would skip them).

        Takes: the request, the unsaved ServiceAccount (its limits are passed on) and the
        first token's name.
        Returns: nothing. `obj` becomes the saved row and a message shows the token once.
        Raises: ValueError if `obj` is already saved.
        """
        if obj.pk is not None:
            raise ValueError("Cannot create credentials for an account that already exists.")
        limits = {field: getattr(obj, field) for field in LIMIT_FIELDS}
        created, token = ServiceAccountService().create(
            obj.organization, obj.name, token_name, **limits
        )
        # Admin carries on with `obj` after this (logging, redirect), so make it the saved row
        obj.__dict__.update(created.__dict__)
        self.message_user(request, self._token_message(obj.name, token), level=messages.WARNING)

    def get_actions(self, request: HttpRequest) -> dict[str, Any]:
        """No list actions: everything is done one account at a time, from its own page."""
        return {}

    def delete_model(self, request: HttpRequest, obj: ServiceAccount) -> None:
        """Through the service, so the login and its tokens go too and stop working."""
        ServiceAccountService().delete(obj)

    def get_urls(self) -> list[URLPattern]:
        custom = [
            path(
                "<path:object_id>/issue-token/",
                self.admin_site.admin_view(self.issue_token_view),
                name="accounts_serviceaccount_issue_token",
            ),
            path(
                "<path:object_id>/regenerate-webhook-secret/",
                self.admin_site.admin_view(self.regenerate_webhook_secret_view),
                name="accounts_serviceaccount_regenerate_webhook_secret",
            ),
        ]
        return custom + super().get_urls()

    def _account_page(
        self, request: HttpRequest, object_id: str, template: str, title: str, **context: Any
    ) -> TemplateResponse:
        return TemplateResponse(
            request,
            template,
            {
                **self.admin_site.each_context(request),
                "title": title,
                "opts": self.model._meta,
                "service_account": self.get_object(request, object_id),
                **context,
            },
        )

    def _account_or_404(self, request: HttpRequest, object_id: str) -> ServiceAccount:
        service_account = self.get_object(request, object_id)
        if service_account is None:
            raise Http404
        if not self.has_change_permission(request, service_account):
            raise PermissionDenied
        return service_account

    def _back_to(self, service_account: ServiceAccount) -> HttpResponseRedirect:
        return HttpResponseRedirect(
            reverse("admin:accounts_serviceaccount_change", args=[service_account.pk])
        )

    def issue_token_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        service_account = self._account_or_404(request, object_id)
        form = IssueTokenForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                token = ServiceAccountService().issue_token(
                    service_account, form.cleaned_data["name"]
                )
            except TokenNameTakenError:
                form.add_error("name", "This account already has a token with that name.")
            else:
                self.message_user(
                    request,
                    self._token_message(service_account.name, token),
                    level=messages.WARNING,
                )
                return self._back_to(service_account)
        return self._account_page(
            request, object_id, "admin/accounts/issue_token.html", "Issue a new token", form=form
        )

    def regenerate_webhook_secret_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        service_account = self._account_or_404(request, object_id)
        if request.method == "POST":
            ServiceAccountService().regenerate_webhook_secret(service_account)
            self.message_user(
                request,
                "New webhook secret generated (shown below). Update whatever checks our webhooks.",
                level=messages.WARNING,
            )
            return self._back_to(service_account)
        return self._account_page(
            request,
            object_id,
            "admin/accounts/regenerate_webhook_secret.html",
            "Regenerate webhook secret",
        )


@admin.register(ServiceAccountToken)
class ServiceAccountTokenAdmin(TokenColumnsMixin, admin.ModelAdmin):
    """Lists tokens. Deleting one revokes it, through ServiceAccountService."""

    list_display = ("name", "service_account", "token_prefix", "created")
    list_select_related = ("service_account__organization", "auth_token")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: ServiceAccountToken | None = None
    ) -> bool:
        return False

    def delete_model(self, request: HttpRequest, obj: ServiceAccountToken) -> None:
        ServiceAccountService().revoke_token(obj)

    def delete_queryset(
        self, request: HttpRequest, queryset: QuerySet[ServiceAccountToken]
    ) -> None:
        ServiceAccountService().revoke_tokens(queryset.select_related("auth_token"))
