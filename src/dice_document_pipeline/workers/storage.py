"""Storage adapters for server remediation workers."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse


class StorageAdapter(Protocol):
    """Interface for worker PDF/log artifact storage."""

    def download_pdf(self, source_uri: str, destination: Path) -> Path:
        """Download a source PDF to a local destination path."""

    def upload_pdf(self, source: Path, destination_name: str) -> str:
        """Upload a remediated PDF and return its storage URI."""

    def upload_log(self, source: Path, destination_name: str) -> str:
        """Upload a remediation log and return its storage URI."""


class LocalStorageAdapter:
    """Filesystem-backed storage adapter used for local tests and dry runs."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir.resolve()
        self.pdf_dir = self.output_dir / "pdfs"
        self.log_dir = self.output_dir / "logs"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def download_pdf(self, source_uri: str, destination: Path) -> Path:
        source = local_path_from_uri(source_uri)
        if not source.exists():
            raise FileNotFoundError(f"Source PDF does not exist: {source}")
        if not source.is_file():
            raise ValueError(f"Source PDF is not a file: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def upload_pdf(self, source: Path, destination_name: str) -> str:
        destination = self.pdf_dir / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        return destination.as_uri()

    def upload_log(self, source: Path, destination_name: str) -> str:
        destination = self.log_dir / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        return destination.as_uri()


def local_path_from_uri(uri: str) -> Path:
    """Resolve a local path or file URI to a filesystem path."""
    parsed = urlparse(uri)
    if _looks_like_windows_drive_path(uri) or parsed.scheme == "":
        return Path(uri).expanduser().resolve()
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported local storage URI scheme: {parsed.scheme!r}")
    if parsed.netloc and parsed.netloc not in ("localhost", "127.0.0.1"):
        raise ValueError(f"Unsupported file URI host: {parsed.netloc}")
    path = unquote(parsed.path)
    if _looks_like_windows_file_uri_path(path):
        path = path[1:]
    return Path(path).resolve()


def _looks_like_windows_drive_path(path: str) -> bool:
    return len(path) >= 3 and path[1] == ":" and path[2] in ("\\", "/")


def _looks_like_windows_file_uri_path(path: str) -> bool:
    return len(path) >= 4 and path[0] == "/" and path[2] == ":" and path[3] == "/"
