from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec

import pikepdf
import pymupdf as fitz
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase

from remediation.adapters.base import AdapterError
from remediation.models import Remediation, RemediationArtifact


def write_fake_pdf(uri: str, content: bytes = b"%PDF-1.4 fake content") -> None:
    """Ensures real bytes exist in `default_storage` at `uri`. `ArtifactService.
    local_input_copy` performs a real storage read, so any test exercising a step's
    `run()` needs actual content behind the URI, not just a DB field set to an arbitrary
    string — a `default_storage.path()`-based approach never needed this, since it was
    pure string math with no I/O.

    Prefer `RemediationFactory(with_stored_file=True, ...)` over calling this directly;
    this is kept as a standalone function for cases
    decoupled from a specific factory call, e.g. `assert_step_continues_on_adapter_error`
    below, which takes a bare `pdf_uri` argument.
    """
    if not default_storage.exists(uri):
        default_storage.save(uri, ContentFile(content))


def fake_adapter_output(filename: str = "test.pdf", content: bytes = b"%PDF-1.4 repaired"):
    """Builds a `side_effect` for a mocked adapter method that "produces a file"
    (`repair`/`finalize`/`extract`/`write_alt_text`). `ArtifactService.persist_output` now
    reads the adapter's returned path back off local disk to upload it, so a mock's
    `return_value` can no longer be a made-up string — it has to be a real file. The real
    `output_dir` is only known once `run()` actually creates its `tempfile.TemporaryDirectory()`,
    so this writes into whatever `output_dir` the call is actually made with, not a
    pre-computed one.
    """

    def _side_effect(*_args: Any, output_dir: str, **_kwargs: Any) -> str:
        output_path = os.path.join(output_dir, filename)
        with open(output_path, "wb") as f:
            f.write(content)
        return output_path

    return _side_effect


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
    write_fake_pdf(pdf_uri)
    adapter = create_autospec(adapter_cls, spec_set=True)
    getattr(adapter, mock_method_name).side_effect = AdapterError("boom")

    result = service_cls(adapter=adapter).run(remediation, pdf_uri=pdf_uri)

    test_case.assertEqual(result, pdf_uri)
    artifact = remediation.artifacts.get(step=step)
    test_case.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
    test_case.assertEqual(artifact.error, "boom")


def make_tagged_pdf(path: Path, pages: list[dict[str, Any]]) -> Path:
    """Writes a small PDF whose pages have known text and a known structure tree, so the
    quality report can be checked against exact numbers.

    Each page is `{"text": str, "tags": [...]}`. A tag is `("H1",)` (or any heading or `"P"`),
    `("L", item_count)`, `("Table", header_cell_count)` or `("Figure", alt_text)`. Leave out
    "tags" for no structure on that page.
    """
    doc = fitz.open()
    for page in pages:
        doc.new_page().insert_text((72, 72), page.get("text", ""))
    doc.save(str(path))
    doc.close()

    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        document = pdf.make_indirect(
            pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.Document)
        )
        document.K = pikepdf.Array()

        def element(tag: str, page: pikepdf.Page, **extra: Any) -> pikepdf.Object:
            return pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.StructElem, S=pikepdf.Name("/" + tag), Pg=page.obj, **extra
                )
            )

        for page, spec in zip(pdf.pages, pages, strict=True):
            for tag, *args in spec.get("tags", []):
                if tag == "L":
                    node = element(
                        "L", page, K=pikepdf.Array([element("LI", page) for _ in range(args[0])])
                    )
                elif tag == "Table":
                    cells = [element("TH", page) for _ in range(args[0])] or [element("TD", page)]
                    node = element(
                        "Table",
                        page,
                        K=pikepdf.Array([element("TR", page, K=pikepdf.Array(cells))]),
                    )
                elif tag == "Figure":
                    node = element(
                        "Figure", page, **({"Alt": pikepdf.String(args[0])} if args[0] else {})
                    )
                else:
                    node = element(tag, page)
                document.K.append(node)

        pdf.Root.StructTreeRoot = pdf.make_indirect(
            pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot, K=document)
        )
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        pdf.save(path)
    return path
