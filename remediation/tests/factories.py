from __future__ import annotations

import factory
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.tests.factories import ServiceAccountFactory
from remediation.models import (
    Remediation,
    RemediationArtifact,
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


class VerificationResultFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VerificationResult

    remediation = factory.SubFactory(RemediationFactory)
    step = factory.Iterator([RemediationArtifact.Step.PRECHECK, RemediationArtifact.Step.POSTCHECK])
    is_compliant = factory.Faker("boolean")


class RemediationScoreFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RemediationScore

    remediation = factory.SubFactory(RemediationFactory)
    score = factory.Faker("random_int", min=0, max=100)
    grade = factory.Iterator(RemediationScore.Grade.values)
    manual_review_items = factory.LazyFunction(list)


class PdfUploadFactory(factory.Factory):
    class Meta:
        model = SimpleUploadedFile

    name = "test.pdf"
    content = b"%PDF-1.4 fake content"
    content_type = "application/pdf"
