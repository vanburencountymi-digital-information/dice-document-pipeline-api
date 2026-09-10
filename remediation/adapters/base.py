"""Base Adapters for processes; put framework-specific adapters in /adapters/ instead."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, NoReturn

# The only image formats Claude Vision (and the wider `anthropic` SDK) accepts.
ImageMediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]


class AdapterError(Exception):
    """Base error class - step is clear from the pipeline itself."""


class Adapter(ABC):
    """Base class for adapters (package) and clients (outside APIs)"""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def raise_adapter_error(self, message: str) -> NoReturn:
        raise AdapterError(message)


class VerificationAdapter(Adapter):
    """Base class that wraps adapters for precheck and postcheck stages"""

    @abstractmethod
    def validate(self, pdf_path: str) -> tuple[bool, str]:
        pass


class OCRAdapter(Adapter):
    """Base class that wraps adapters for the OCR/tagging stage."""

    @abstractmethod
    def extract(self, pdf_path: str, *, output_dir: str) -> str:
        pass


class MetadataAdapter(Adapter):
    """Base class that wraps adapters for the finalize_metadata stage."""

    @abstractmethod
    def finalize(self, pdf_path: str, *, output_dir: str, title: str, lang: str) -> str:
        pass


class LinkAdapter(Adapter):
    """Base class that wraps adapters for the link_tag stage."""

    @abstractmethod
    def repair(self, pdf_path: str, *, output_dir: str) -> str:
        pass


class FontRepairAdapter(Adapter):
    """Base class that wraps adapters for the font_repair stage."""

    @abstractmethod
    def repair(self, pdf_path: str, *, output_dir: str) -> str:
        pass


@dataclass(frozen=True)
class FigureCandidate:
    """One `<Figure>` element needing alt text, plus the image data to describe it with.

    `ref` is `(page_idx, fig_n)` rather than a live pikepdf object handle, since object
    handles don't survive across separate `pikepdf.open()` calls — `AltTextAdapter`
    implementations re-derive the same struct-tree walk to relocate an element by `ref`
    when writing results back.
    """

    ref: tuple[int, int]
    page_number: int
    image_bytes: bytes
    media_type: ImageMediaType
    decorative: bool


class AltTextAdapter(Adapter):
    """Base class that wraps adapters for the alt_text stage's PDF-side work (struct-tree
    reading and writing). Pairs with `AltTextClient` for the vision-API call itself —
    this stage needs both an outside-package integration and an outside-API integration,
    unlike every other stage, which only needs one.
    """

    @abstractmethod
    def collect_figures(self, pdf_path: str) -> list[FigureCandidate]:
        pass

    @abstractmethod
    def write_alt_text(
        self, pdf_path: str, *, output_dir: str, alt_by_ref: dict[tuple[int, int], str]
    ) -> str:
        pass


class AltTextClient(Adapter):
    """Base class that wraps clients for the alt_text stage's vision-API call."""

    @abstractmethod
    def describe(
        self,
        image_bytes: bytes,
        *,
        media_type: ImageMediaType,
        document_title: str,
        page_number: int,
    ) -> str:
        pass


@dataclass(frozen=True)
class ScoringResult:
    """Heuristic compliance score for one PDF (add_confidence_scoring experiment)."""

    score: int
    grade: str
    manual_review_items: list[str]


class ScoringAdapter(Adapter):
    """Base class for the optional, non-blocking scoring stage. Implementations must
    never let a failure propagate past `ScoringService.run` — see services.py.
    """

    @abstractmethod
    def score(self, pdf_path: str) -> ScoringResult:
        pass
