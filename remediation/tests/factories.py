from __future__ import annotations

import factory
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.tests.factories import ServiceAccountFactory
from remediation.models import Remediation, RemediationArtifact


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


class PdfUploadFactory(factory.Factory):
    class Meta:
        model = SimpleUploadedFile

    name = "test.pdf"
    content = b"%PDF-1.4 fake content"
    content_type = "application/pdf"
