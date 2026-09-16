from __future__ import annotations

import factory
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.tests.factories import ServiceAccountFactory
from remediation.adapters.verification.severity import Severity
from remediation.models import (
    PipelineConfig,
    Remediation,
    RemediationArtifact,
    RemediationCallback,
    RemediationScore,
    VerificationResult,
)


class RemediationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Remediation

    service_account = factory.SubFactory(ServiceAccountFactory)
    source_pdf_uri = factory.Sequence(lambda n: f"local:///tmp/document-{n}.pdf")
    content_hash = factory.Sequence(lambda n: f"hash-{n}")


class RemediationArtifactFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RemediationArtifact

    remediation = factory.SubFactory(RemediationFactory)
    step = factory.Iterator(RemediationArtifact.Step.values)
    status = factory.Iterator(RemediationArtifact.StepStatus.values)


class FailedRuleFactory(factory.DictFactory):
    """One `VerificationResult.failed_rules` entry, matching the dict shape
    `VerificationService.run` writes (simplify_vera_printouts). A plain `DictFactory` since
    `failed_rules` is JSON data, not a model — `VerificationResultFactory(failed_rules=[
    FailedRuleFactory(), FailedRuleFactory(severity=Severity.CRITICAL.value)])`.
    """

    clause = "7.21"
    test_number = "7"
    description = "font missing CIDSet entries"
    failed_checks = 1
    severity = Severity.MINOR.value


class VerificationResultFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VerificationResult

    remediation = factory.SubFactory(RemediationFactory)
    step = factory.Iterator([RemediationArtifact.Step.PRECHECK, RemediationArtifact.Step.POSTCHECK])
    is_compliant = factory.Faker("boolean")
    verapdf_version = "1.30.2"
    failed_rules = factory.LazyFunction(list)


class RemediationScoreFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RemediationScore

    remediation = factory.SubFactory(RemediationFactory)
    score = factory.Faker("random_int", min=0, max=100)
    grade = factory.Iterator(RemediationScore.Grade.values)
    manual_review_items = factory.LazyFunction(list)


class RemediationCallbackFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RemediationCallback

    remediation = factory.SubFactory(RemediationFactory)
    callback_url = factory.Sequence(lambda n: f"https://example.com/webhook/{n}")


class PipelineConfigFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PipelineConfig

    retry_floor_version = ""


class PdfUploadFactory(factory.Factory):
    class Meta:
        model = SimpleUploadedFile

    name = "test.pdf"
    content = b"%PDF-1.4 fake content"
    content_type = "application/pdf"
