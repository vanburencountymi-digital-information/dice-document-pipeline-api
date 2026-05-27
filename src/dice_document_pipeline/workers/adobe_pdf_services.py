"""Adobe PDF Services adapter boundary."""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol


@dataclass(frozen=True)
class AdobeAutotagResult:
    """Result of an Adobe PDF Services Auto-Tag/OCR pass."""

    output_pdf: Path
    api_calls: list[dict[str, Any]]


class AdobePdfServicesAdapter(Protocol):
    """Interface for Adobe PDF Services Auto-Tag/OCR."""

    def autotag_pdf(self, source_pdf: Path, destination_pdf: Path) -> AdobeAutotagResult:
        """Run Auto-Tag/OCR and write the tagged PDF to destination_pdf."""


class _AdobeSdkSymbols(NamedTuple):
    credentials_cls: Any
    pdf_services_cls: Any
    media_type_cls: Any
    job_cls: Any
    params_cls: Any
    result_cls: Any


class AdobePdfServicesSdkAdapter:
    """Adobe PDF Services SDK-backed Auto-Tag adapter.

    The SDK imports are lazy so local tests and non-Adobe worker paths can load
    the package without Adobe credentials or the optional SDK installed.
    """

    def __init__(
        self,
        *,
        pdf_services: Any | None = None,
        generate_report: bool = True,
        shift_headings: bool = True,
        estimated_transactions_per_page: int = 10,
    ):
        self._symbols = _load_adobe_sdk_symbols()
        self.pdf_services = pdf_services or self._build_pdf_services_from_env()
        self.generate_report = generate_report
        self.shift_headings = shift_headings
        self.estimated_transactions_per_page = estimated_transactions_per_page

    def autotag_pdf(self, source_pdf: Path, destination_pdf: Path) -> AdobeAutotagResult:
        start = time.time()
        destination_pdf.parent.mkdir(parents=True, exist_ok=True)

        input_asset = self.pdf_services.upload(
            input_stream=source_pdf.read_bytes(),
            mime_type=self._symbols.media_type_cls.PDF,
        )
        params = self._symbols.params_cls(
            generate_report=self.generate_report,
            shift_headings=self.shift_headings,
        )
        job = self._symbols.job_cls(
            input_asset=input_asset,
            autotag_pdf_params=params,
        )
        location = self.pdf_services.submit(job)
        result = self.pdf_services.get_job_result(location, self._symbols.result_cls)

        result_payload = result.get_result()
        tagged_asset = result_payload.get_tagged_pdf()
        tagged_stream = self.pdf_services.get_content(tagged_asset)
        destination_pdf.write_bytes(tagged_stream.get_input_stream())

        report_path = None
        report_asset = result_payload.get_report()
        if report_asset:
            report_path = destination_pdf.with_suffix(".report.xlsx")
            report_stream = self.pdf_services.get_content(report_asset)
            report_path.write_bytes(report_stream.get_input_stream())

        elapsed_seconds = round(time.time() - start, 3)
        api_call = {
            "provider": "adobe-pdf-services",
            "operation": "autotag_pdf",
            "mode": "sdk",
            "elapsed_seconds": elapsed_seconds,
            "generate_report": self.generate_report,
            "shift_headings": self.shift_headings,
            "estimated_transactions_per_page": self.estimated_transactions_per_page,
            "report_path": str(report_path) if report_path else None,
        }
        return AdobeAutotagResult(output_pdf=destination_pdf, api_calls=[api_call])

    def _build_pdf_services_from_env(self) -> Any:
        client_id = os.getenv("PDF_SERVICES_CLIENT_ID")
        client_secret = os.getenv("PDF_SERVICES_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise EnvironmentError(
                "Missing PDF_SERVICES_CLIENT_ID or PDF_SERVICES_CLIENT_SECRET."
            )
        credentials = self._symbols.credentials_cls(
            client_id=client_id,
            client_secret=client_secret,
        )
        return self._symbols.pdf_services_cls(credentials=credentials)


class LocalAdobePdfServicesStub:
    """Local no-network Adobe adapter used until real credentials are wired."""

    def autotag_pdf(self, source_pdf: Path, destination_pdf: Path) -> AdobeAutotagResult:
        destination_pdf.parent.mkdir(parents=True, exist_ok=True)
        if source_pdf.resolve() != destination_pdf.resolve():
            shutil.copy2(source_pdf, destination_pdf)
        return AdobeAutotagResult(
            output_pdf=destination_pdf,
            api_calls=[
                {
                    "provider": "adobe-pdf-services",
                    "operation": "autotag_pdf",
                    "mode": "local_stub",
                    "transaction_count": 0,
                }
            ],
        )


def _load_adobe_sdk_symbols() -> _AdobeSdkSymbols:
    try:
        from adobe.pdfservices.operation.auth.service_principal_credentials import (
            ServicePrincipalCredentials,
        )
        from adobe.pdfservices.operation.pdf_services import PDFServices
        from adobe.pdfservices.operation.pdf_services_media_type import (
            PDFServicesMediaType,
        )
        from adobe.pdfservices.operation.pdfjobs.jobs.autotag_pdf_job import (
            AutotagPDFJob,
        )
        from adobe.pdfservices.operation.pdfjobs.params.autotag_pdf.autotag_pdf_params import (
            AutotagPDFParams,
        )
        from adobe.pdfservices.operation.pdfjobs.result.autotag_pdf_result import (
            AutotagPDFResult,
        )
    except ImportError as exc:
        raise ImportError(
            "Adobe PDF Services SDK is not installed. Install the optional "
            "dependency with `pip install 'dice-document-pipeline[adobe]'`."
        ) from exc

    return _AdobeSdkSymbols(
        credentials_cls=ServicePrincipalCredentials,
        pdf_services_cls=PDFServices,
        media_type_cls=PDFServicesMediaType,
        job_cls=AutotagPDFJob,
        params_cls=AutotagPDFParams,
        result_cls=AutotagPDFResult,
    )
