import glob
import os
import subprocess

import opendataloader_pdf

from remediation.adapters.base import OCRAdapter


class OpenDataLoaderAdapter(OCRAdapter):
    """Wraps OpenDataLoader's Python API to OCR and extract a PDF's structure as JSON (ADR 0010).

    Requests `format="json"`, not `format="tagged-pdf"`: OpenDataLoader's hybrid mode
    silently drops AI-backend-recovered OCR text from `tagged-pdf`/`pdf` output — a
    confirmed bug in its bundled Java CLI, isolated independent of this adapter (see
    ADR 0010) — but the same run's `json` output is fully and correctly populated,
    including real recovered text and correct PDF/UA tag roles (`P`, `H1`, `Figure`).
    `TagBuilderService`/`TaggedPdfWriterAdapter` builds the actual tagged PDF from that
    JSON in the next pipeline step; this adapter only extracts.

    Always runs in `--hybrid docling-fast` mode per ADR 0004 — not configurable, since the
    ADR didn't decide to support a non-hybrid fallback. That backend lives at `hybrid_url`,
    a separate container in docker-compose (`Dockerfile.opendataloader-hybrid`) since it
    needs Docling/PyTorch, which don't ship musl/Alpine wheels like the rest of this app's
    image.
    """

    HYBRID_MODE = "docling-fast"

    def __init__(self, hybrid_url: str | None = None) -> None:
        self.hybrid_url = hybrid_url

    @property
    def name(self) -> str:
        return "OpenDataLoader Adapter"

    def extract(self, pdf_path: str, *, output_dir: str) -> str:
        """OCRs and extracts `pdf_path`'s structure, writing a JSON file into `output_dir`.

        Returns the JSON file's path. OpenDataLoader's Python API doesn't document its
        output filename convention or what it raises on failure (missing Java, a bad PDF,
        a hybrid server that's unreachable or still downloading its models, etc.), so this
        reads the result back off disk rather than trusting a return value, and wraps any
        underlying exception rather than naming specific ones.
        """
        os.makedirs(output_dir, exist_ok=True)
        kwargs = {"hybrid_url": self.hybrid_url} if self.hybrid_url else {}
        try:
            opendataloader_pdf.convert(
                input_path=[pdf_path],
                output_dir=output_dir,
                format="json",
                hybrid=self.HYBRID_MODE,
                **kwargs,
            )
        except subprocess.CalledProcessError as exc:
            # str(exc) alone is just "Command '[...]' returned non-zero exit status N" —
            # the JVM's actual error (bad input, unreachable hybrid server, etc.) is in
            # stdout/stderr, when the library call captured them at all.
            raw_detail = exc.stderr or exc.stdout
            if isinstance(raw_detail, bytes):
                raw_detail = raw_detail.decode(errors="replace")
            message = f"opendataloader-pdf failed: {exc}"
            if raw_detail and raw_detail.strip():
                message += f"\n{raw_detail.strip()}"
            self.raise_adapter_error(message)
        except Exception as exc:
            self.raise_adapter_error(f"opendataloader-pdf failed: {exc}")

        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        matches = glob.glob(os.path.join(output_dir, f"{stem}*.json"))
        if not matches:
            self.raise_adapter_error(f"opendataloader-pdf produced no JSON output in {output_dir}")
        if len(matches) > 1:
            self.raise_adapter_error(
                f"opendataloader-pdf produced multiple candidate outputs in {output_dir}: {matches}"
            )

        # Force the output's filename to match the input's stem exactly, regardless of
        # whatever OpenDataLoader itself named it (undocumented, and not guaranteed
        # to just be the input's basename) — every stage's artifact should be
        # findable under the same name as the original.
        output_path = matches[0]
        target_path = os.path.join(output_dir, f"{stem}.json")
        if output_path != target_path:
            os.replace(output_path, target_path)
            output_path = target_path
        return output_path
