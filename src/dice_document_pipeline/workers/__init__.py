"""Worker-facing interfaces for remediation jobs."""

from .adobe_pdf_services import AdobeAutotagResult, AdobePdfServicesAdapter, LocalAdobePdfServicesStub
from .remediation_job import RemediationJob, RemediationResult, process_remediation_job
from .server_worker import process_server_remediation_job
from .storage import LocalStorageAdapter, StorageAdapter

__all__ = [
    "AdobeAutotagResult",
    "AdobePdfServicesAdapter",
    "LocalAdobePdfServicesStub",
    "LocalStorageAdapter",
    "RemediationJob",
    "RemediationResult",
    "StorageAdapter",
    "process_remediation_job",
    "process_server_remediation_job",
]
