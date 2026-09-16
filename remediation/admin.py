from typing import Any

from django import forms
from django.contrib import admin
from django.http import HttpRequest
from packaging.version import InvalidVersion, Version

from remediation.models import PipelineConfig, Remediation


def _version_sort_key(value: str) -> Version:
    try:
        return Version(value)
    except InvalidVersion:
        return Version("0.0.0")


class PipelineConfigForm(forms.ModelForm):
    class Meta:
        model = PipelineConfig
        fields = ["retry_floor_version"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        versions = sorted(
            {v for v in Remediation.objects.values_list("pipeline_version", flat=True) if v},
            key=_version_sort_key,
        )
        self.fields["retry_floor_version"] = forms.ChoiceField(
            choices=[("", "(none — nothing auto-retries)"), *[(v, v) for v in versions]],
            required=False,
        )


@admin.register(PipelineConfig)
class PipelineConfigAdmin(admin.ModelAdmin):
    form = PipelineConfigForm

    def has_add_permission(self, request: HttpRequest) -> bool:
        return not PipelineConfig.objects.exists()


@admin.register(Remediation)
class RemediationAdmin(admin.ModelAdmin):
    list_display = ["id", "service_account", "status", "pipeline_version", "created_at"]
    list_filter = ["status"]
    readonly_fields = [
        "id",
        "service_account",
        "status",
        "source_pdf_uri",
        "content_hash",
        "original_filename",
        "error",
        "pipeline_version",
        "created_at",
        "started_at",
        "completed_at",
    ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
