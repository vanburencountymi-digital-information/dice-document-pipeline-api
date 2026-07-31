import glob
import os
import subprocess

from remediation.adapters.base import TagBuilderAdapter

# Bundled into the app image alongside veraPDF (ADR 0010) — not built yet (Task #2/#3):
# a custom Apache PDFBox-based tool, not a third-party CLI, so it has no upstream install
# path to document here the way VeraPDFAdapter/OpenDataLoaderAdapter's tools do.
JAR_PATH = "/opt/tagged-pdf-writer/tagged-pdf-writer.jar"


class TaggedPdfWriterAdapter(TagBuilderAdapter):
    """Wraps a custom Apache PDFBox tool that builds a real tagged PDF from OpenDataLoader's
    JSON output (ADR 0010) — the step OpenDataLoader's own `tagged-pdf` format was supposed
    to produce, before hybrid-recovered OCR text was found silently missing from it.

    Not a third-party CLI like `VeraPDFAdapter`/`OpenDataLoaderAdapter` wrap: this tool is
    built and maintained in this repo (see `Dockerfile`'s build stage for `JAR_PATH`), since
    no existing library safely covers "construct a `StructTreeRoot` from structured JSON"
    (pikepdf has no high-level struct-tree API; fpdf2's is internal/unstable; iText7 would
    work but is AGPL-or-commercial, and this repo has no `LICENSE` file to check that
    against — PDFBox's Apache 2.0 license has no such question).
    """

    @property
    def name(self) -> str:
        return "Tagged PDF Writer Adapter"

    def build(self, structure_path: str, *, output_dir: str) -> str:
        """Builds a tagged PDF from `structure_path` (OpenDataLoader JSON), into `output_dir`.

        Returns the tagged PDF's path. Mirrors `OpenDataLoaderAdapter.extract()`'s
        stem-matching/rename convention, so every stage's artifact is findable under the
        same name as the original regardless of what the underlying tool names its output.
        """
        os.makedirs(output_dir, exist_ok=True)
        try:
            result = subprocess.run(
                ["java", "-jar", JAR_PATH, structure_path, output_dir],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.raise_adapter_error(
                "tagged-pdf-writer.jar not found — it's only built into the Docker image"
            )
        if result.returncode != 0:
            self.raise_adapter_error(
                f"tagged-pdf-writer failed (exit {result.returncode}): {result.stderr.strip()}"
            )

        stem = os.path.splitext(os.path.basename(structure_path))[0]
        matches = glob.glob(os.path.join(output_dir, f"{stem}*.pdf"))
        if not matches:
            self.raise_adapter_error(f"tagged-pdf-writer produced no PDF in {output_dir}")
        if len(matches) > 1:
            self.raise_adapter_error(
                f"tagged-pdf-writer produced multiple candidate outputs in {output_dir}: {matches}"
            )

        output_path = matches[0]
        target_path = os.path.join(output_dir, f"{stem}.pdf")
        if output_path != target_path:
            os.replace(output_path, target_path)
            output_path = target_path
        return output_path
