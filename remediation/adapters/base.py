"""Base Adapters for processes; put framework-specific adapters in /adapters/ instead."""

from abc import ABC, abstractmethod
from typing import NoReturn


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
    """Base class that wraps adapters for the OCR/auto-tagging stage."""

    @abstractmethod
    def tag(self, pdf_path: str, *, output_dir: str) -> str:
        pass
