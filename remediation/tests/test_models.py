from __future__ import annotations

from django.test import TestCase

from remediation.models import PipelineConfig
from remediation.tests.factories import PipelineConfigFactory, RemediationFactory


class RemediationTests(TestCase):
    def test_pipeline_version_defaults_to_blank(self) -> None:
        remediation = RemediationFactory()

        self.assertEqual(remediation.pipeline_version, "")


class PipelineConfigTests(TestCase):
    def test_save_always_uses_pk_1(self) -> None:
        config = PipelineConfig(retry_floor_version="1.0.0")

        config.save()

        self.assertEqual(config.pk, 1)

    def test_saving_a_second_instance_overwrites_the_singleton_row(self) -> None:
        PipelineConfigFactory(retry_floor_version="1.0.0")

        PipelineConfig(retry_floor_version="2.0.0").save()

        self.assertEqual(PipelineConfig.objects.count(), 1)
        self.assertEqual(PipelineConfig.objects.get().retry_floor_version, "2.0.0")
