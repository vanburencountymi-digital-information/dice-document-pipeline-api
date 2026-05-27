"""Adobe PDF Services adapter boundary."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class AdobeAutotagResult:
    """Result of an Adobe PDF Services Auto-Tag/OCR pass."""

    output_pdf: Path
    api_calls: list[dict[str, Any]]


class AdobePdfServicesAdapter(Protocol):
    """Interface for Adobe PDF Services Auto-Tag/OCR."""

    def autotag_pdf(self, source_pdf: Path, destination_pdf: Path) -> AdobeAutotagResult:
        """Run Auto-Tag/OCR and write the tagged PDF to destination_pdf."""


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
