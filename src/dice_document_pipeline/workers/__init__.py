"""Worker-facing interfaces for remediation jobs."""

from .docling_pdf import (
    DoclingAdapter,
    DoclingConversionResult,
    DoclingPdfAdapter,
    LocalDoclingStub,
)
from .remediation_job import RemediationJob, RemediationResult, process_remediation_job
from .server_worker import process_server_remediation_job
from .storage import LocalStorageAdapter, StorageAdapter

__all__ = [
    "DoclingAdapter",
    "DoclingConversionResult",
    "DoclingPdfAdapter",
    "LocalDoclingStub",
    "LocalStorageAdapter",
    "RemediationJob",
    "RemediationResult",
    "StorageAdapter",
    "process_remediation_job",
    "process_server_remediation_job",
]
