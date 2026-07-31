import glob
import os
import subprocess

import opendataloader_pdf

from remediation.adapters.base import OCRAdapter


class OpenDataLoaderAdapter(OCRAdapter):
    """Wraps OpenDataLoader's Python API to OCR and auto-tag a PDF in one pass (ADR 0004).

    Pinned as a real, named dependency rather than hidden behind a generic OCR/tagging
    interface — OpenDataLoader's tagging pass always runs its own internal extraction/OCR
    first, so this one call backs the whole `ocr` step; there's no separate OCR call to make.
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

    def tag(self, pdf_path: str, *, output_dir: str) -> str:
        """Auto-tags `pdf_path`, writing the tagged PDF into `output_dir`.

        Returns the tagged PDF's path. OpenDataLoader's Python API doesn't document its
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
                format="tagged-pdf",
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
        matches = glob.glob(os.path.join(output_dir, f"{stem}*.pdf"))
        if not matches:
            self.raise_adapter_error(f"opendataloader-pdf produced no tagged PDF in {output_dir}")
        if len(matches) > 1:
            self.raise_adapter_error(
                f"opendataloader-pdf produced multiple candidate outputs in {output_dir}: {matches}"
            )

        # Force the output's filename to match the input's exactly, regardless of
        # whatever OpenDataLoader itself named it (undocumented, and not guaranteed
        # to just be the input's basename) — every stage's artifact should be
        # findable under the same name as the original.
        output_path = matches[0]
        target_path = os.path.join(output_dir, os.path.basename(pdf_path))
        if output_path != target_path:
            os.replace(output_path, target_path)
            output_path = target_path
        return output_path
