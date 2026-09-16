from __future__ import annotations

from unittest.mock import create_autospec

from django.test import TestCase

from remediation.adapters.base import AdapterError
from remediation.models import Remediation, RemediationArtifact


def assert_step_continues_on_adapter_error(
    test_case: TestCase,
    *,
    service_cls: type,
    adapter_cls: type,
    mock_method_name: str,
    step: RemediationArtifact.Step,
    remediation: Remediation,
    pdf_uri: str,
) -> None:
    """Shared proof of ADR 0013's "a failed step doesn't abort the pipeline" contract, for
    any step whose `run()` is "call one adapter method, hand back `pdf_uri` unchanged on
    failure" (`OCRService`/`FontRepairService`/`MetadataService`/`LinkService`).
    `AltTextService` isn't a candidate here — it has two independent failure points
    (`collect_figures` vs. `describe`) and a second `client` collaborator, not just one
    adapter call, so it keeps its own dedicated tests.
    """
    adapter = create_autospec(adapter_cls, spec_set=True)
    getattr(adapter, mock_method_name).side_effect = AdapterError("boom")

    result = service_cls(adapter=adapter).run(remediation, pdf_uri=pdf_uri)

    test_case.assertEqual(result, pdf_uri)
    artifact = remediation.artifacts.get(step=step)
    test_case.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
    test_case.assertEqual(artifact.error, "boom")
