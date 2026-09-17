from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from accounts.models import ServiceAccount
from accounts.services import ServiceAccountService


@admin.register(ServiceAccount)
class ServiceAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    actions = ["issue_token"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Turn off default Django `Add` in admin, which would skip tokens"""
        return False

    @admin.action(description="Issue a new token")
    def issue_token(self, request: HttpRequest, queryset: QuerySet[ServiceAccount]) -> None:
        service = ServiceAccountService()
        for service_account in queryset:
            token = service.issue_token(service_account)
            self.message_user(request, f"{service_account.name}: {token}")
