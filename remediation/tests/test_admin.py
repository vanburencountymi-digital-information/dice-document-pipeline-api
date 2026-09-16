from __future__ import annotations

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase

from remediation.admin import PipelineConfigAdmin, PipelineConfigForm, RemediationAdmin
from remediation.models import PipelineConfig, Remediation
from remediation.tests.factories import PipelineConfigFactory, RemediationFactory


class PipelineConfigFormTests(TestCase):
    def test_choices_offer_only_the_none_option_when_no_remediations_exist(self) -> None:
        form = PipelineConfigForm()

        self.assertEqual(
            list(form.fields["retry_floor_version"].choices),
            [("", "(none — nothing auto-retries)")],
        )

    def test_choices_are_populated_from_distinct_pipeline_versions(self) -> None:
        RemediationFactory(pipeline_version="1.0.0")
        RemediationFactory(pipeline_version="1.1.0")
        RemediationFactory(pipeline_version="1.0.0")  # duplicate, shouldn't repeat

        form = PipelineConfigForm()

        self.assertEqual(
            list(form.fields["retry_floor_version"].choices),
            [
                ("", "(none — nothing auto-retries)"),
                ("1.0.0", "1.0.0"),
                ("1.1.0", "1.1.0"),
            ],
        )

    def test_choices_are_sorted_by_semver_not_string(self) -> None:
        RemediationFactory(pipeline_version="1.9.0")
        RemediationFactory(pipeline_version="1.10.0")
        RemediationFactory(pipeline_version="1.2.0")

        form = PipelineConfigForm()

        self.assertEqual(
            [value for value, _ in form.fields["retry_floor_version"].choices],
            ["", "1.2.0", "1.9.0", "1.10.0"],
        )

    def test_choices_exclude_blank_pipeline_versions(self) -> None:
        RemediationFactory(pipeline_version="")

        form = PipelineConfigForm()

        self.assertEqual(
            list(form.fields["retry_floor_version"].choices),
            [("", "(none — nothing auto-retries)")],
        )


class PipelineConfigAdminTests(TestCase):
    def setUp(self) -> None:
        self.admin = PipelineConfigAdmin(PipelineConfig, AdminSite())
        self.request = RequestFactory().get("/admin/")

    def test_add_permission_allowed_when_no_config_exists_yet(self) -> None:
        self.assertTrue(self.admin.has_add_permission(self.request))

    def test_add_permission_denied_once_a_config_row_exists(self) -> None:
        PipelineConfigFactory()

        self.assertFalse(self.admin.has_add_permission(self.request))


class RemediationAdminTests(TestCase):
    def test_add_permission_is_always_denied(self) -> None:
        admin = RemediationAdmin(Remediation, AdminSite())
        request = RequestFactory().get("/admin/")

        self.assertFalse(admin.has_add_permission(request))
