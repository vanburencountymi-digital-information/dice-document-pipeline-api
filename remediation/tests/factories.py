from __future__ import annotations

import factory

from accounts.tests.factories import ServiceAccountFactory
from remediation.models import Remediation


class RemediationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Remediation

    service_account = factory.SubFactory(ServiceAccountFactory)
    source_pdf_uri = factory.Sequence(lambda n: f"local:///tmp/document-{n}.pdf")
    content_hash = factory.Sequence(lambda n: f"hash-{n}")
