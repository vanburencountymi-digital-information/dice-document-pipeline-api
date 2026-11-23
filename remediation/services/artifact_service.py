import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage

from common.db import retry_on_lost_connection
from common.error_logging.client import ErrorLoggingClient
from common.error_logging.constants import ErrorLoggingTag
from remediation.adapters.alt_text.claude_vision import ClaudeVisionClient
from remediation.adapters.alt_text.pike_pdf import (
    PikePdfAdapter as AltTextPikePdfAdapter,
)
from remediation.adapters.base import (
    AltTextAdapter,
    AltTextClient,
    FontRepairAdapter,
    LinkAdapter,
    MetadataAdapter,
    OCRAdapter,
    ScoringAdapter,
)
from remediation.adapters.font_repair.pike_pdf import (
    PikePdfAdapter as FontRepairPikePdfAdapter,
)
from remediation.adapters.link.pike_pdf import PikePdfAdapter as LinkPikePdfAdapter
from remediation.adapters.metadata.pike_pdf import PikePdfAdapter
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter
from remediation.adapters.scoring.pike_pdf import (
    PikePdfAdapter as ScoringPikePdfAdapter,
)
from remediation.models import (
    Remediation,
    RemediationArtifact,
    RemediationScore,
)

LOGGER = logging.getLogger(__name__)


class ArtifactService:
    """Mixin for pipeline step services that records their outcome as a `RemediationArtifact`.

    Subclasses set `step` to the `RemediationArtifact.Step` they represent. Every step is
    gated by a `RUN_<STEP>` setting named after the step itself (default off), so gating a
    new step never needs its own registration — just add the setting.
    """

    step: RemediationArtifact.Step

    def __init__(self, error_logging: ErrorLoggingClient | None = None) -> None:
        self.error_logging = error_logging or ErrorLoggingClient()

    @property
    def setting_name(self) -> str:
        return f"RUN_{self.step.name}"

    def is_disabled(self) -> bool:
        return not getattr(settings, self.setting_name)

    def record_skip(self, remediation: Remediation) -> RemediationArtifact:
        return self.mark_skipped(remediation, f"{self.setting_name} is disabled")

    def report_exception(self, exc: Exception, remediation: Remediation) -> None:
        self.error_logging.report_exception(
            exc,
            tags={
                ErrorLoggingTag.STEP: self.step.value,
                ErrorLoggingTag.REMEDIATION_ID: str(remediation.id),
            },
        )

    def construct_output_dir(self, remediation: Remediation) -> str:
        """This step's own storage-key prefix for one remediation attempt (ADR 0008):
        `remediations/<service_account_id>/<content_hash>/<remediation_id>/<step>/`.

        Pure string construction, not a real filesystem directory — `default_storage`
        isn't guaranteed to have one (`S3Storage.path()` raises `NotImplementedError`).
        Combine with a filename and pass to `persist_output` as the destination URI.
        """
        return (
            f"remediations/{remediation.service_account_id}/"
            f"{remediation.content_hash}/{remediation.id}/{self.step.value}/"
        )

    @contextmanager
    def local_input_copy(self, pdf_uri: str) -> Iterator[str]:
        """Copies `pdf_uri` out of `default_storage` into a real local file, since local
        tools (veraPDF, pikepdf, PyMuPDF, OpenDataLoader) need an actual filesystem path,
        not a storage-abstracted name. `.open()` is a standard `Storage` API method every
        backend implements, unlike `.path()`. Cleans up the temp file on exit either way.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, os.path.basename(pdf_uri))
            with default_storage.open(pdf_uri, "rb") as remote_file, open(local_path, "wb") as f:
                f.write(remote_file.read())
            yield local_path

    def persist_output(self, local_path: str, dest_uri: str) -> str:
        """Uploads a local tool's output file to `default_storage` at `dest_uri` — the
        write-side counterpart to `local_input_copy`. Returns the actual name saved to.

        Deletes any existing object at `dest_uri` first: ADR 0008's paths are deterministic
        on purpose (the same remediation + step always lands at the same key). Retries create
        new remediation rows, so this only happens when a redelivered task re-runs a step that
        didn't complete the first time (ADR 0019).
        """
        if default_storage.exists(dest_uri):
            default_storage.delete(dest_uri)
        with open(local_path, "rb") as fh:
            return default_storage.save(dest_uri, File(fh))

    def completed_output_uri(self, remediation: Remediation) -> str | None:
        """This step's `output_uri` if it already completed for `remediation`, else `None`
        (ADR 0019). Steps check this first so a redelivered task never re-runs finished work.
        Only `COMPLETED` counts — a `FAILED` or `SKIPPED` step is allowed to run again.
        """
        return (
            remediation.artifacts.filter(
                step=self.step, status=RemediationArtifact.StepStatus.COMPLETED
            )
            .values_list("output_uri", flat=True)
            .first()
        )

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        """Runs this step, unless it already completed for `remediation` (ADR 0019) — then
        `resume_completed` stands in for it, so a redelivered task never redoes finished work.
        Subclasses put their actual work in `_run`.
        """
        existing_output_uri = self.completed_output_uri(remediation)
        if existing_output_uri is not None:
            return self.resume_completed(remediation, existing_output_uri)
        return self._run(remediation, pdf_uri=pdf_uri)

    def resume_completed(self, remediation: Remediation, output_uri: str) -> str:
        """What `run` returns for an already-completed step. Default: the step's previous
        output. `VerificationService` overrides this to replay its stored verdict.
        """
        return output_uri

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        raise NotImplementedError

    @retry_on_lost_connection
    def _mark_status(
        self, remediation: Remediation, status: RemediationArtifact.StepStatus, **fields: str
    ) -> RemediationArtifact:
        # Upsert, not create (ADR 0019): a redelivered task re-running a previously failed
        # step overwrites its row instead of tripping `one_artifact_per_step`. Blank defaults
        # so a stale `error` doesn't survive a later success (and vice versa).
        artifact, _ = RemediationArtifact.objects.update_or_create(
            remediation=remediation,
            step=self.step,
            defaults={"status": status, "output_uri": "", "error": "", **fields},
        )
        return artifact

    def mark_completed(self, remediation: Remediation, output_uri: str) -> RemediationArtifact:
        return self._mark_status(
            remediation, RemediationArtifact.StepStatus.COMPLETED, output_uri=output_uri
        )

    def mark_skipped(self, remediation: Remediation, reason: str) -> RemediationArtifact:
        return self._mark_status(remediation, RemediationArtifact.StepStatus.SKIPPED, error=reason)

    def mark_failed(self, remediation: Remediation, error: str) -> RemediationArtifact:
        return self._mark_status(remediation, RemediationArtifact.StepStatus.FAILED, error=error)


class OCRService(ArtifactService):
    """OCR + tagging in one call, via a patched fork of `opendataloader-pdf` (ADR 0010) that
    fixes a confirmed upstream bug where hybrid mode's OCR-recovered text was silently
    dropped from `tagged-pdf`/`pdf` output. Produces the tagged PDF directly.
    """

    step = RemediationArtifact.Step.OCR

    def __init__(
        self,
        adapter: OCRAdapter | None = None,
        error_logging: ErrorLoggingClient | None = None,
    ) -> None:
        super().__init__(error_logging)
        self.adapter = adapter or OpenDataLoaderAdapter(
            hybrid_url=settings.OPENDATALOADER_HYBRID_URL
        )

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                output_path = self.adapter.extract(pdf_path, output_dir=local_output_dir)
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class FontRepairService(ArtifactService):
    """Recovers missing `/ToUnicode` mappings on embedded fonts whose character codes are
    shifted from true Unicode by a constant, per-font offset — see the font-repair plan for the
    full root-cause writeup. Doesn't touch tag structure or metadata; purely a font-dictionary
    repair, same footprint as `LinkService`.
    """

    step = RemediationArtifact.Step.FONT_REPAIR

    def __init__(
        self,
        adapter: FontRepairAdapter | None = None,
        error_logging: ErrorLoggingClient | None = None,
    ) -> None:
        super().__init__(error_logging)
        self.adapter = adapter or FontRepairPikePdfAdapter()

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                output_path = self.adapter.repair(pdf_path, output_dir=local_output_dir)
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


DEFAULT_TITLE = "Untitled document"


def _derive_title(remediation: Remediation) -> str:
    """Shared by `MetadataService` (dc:title) and `AltTextService` (Claude prompt context).

    original_filename is blank on old rows predating that field, and callers can still
    pass "" — fall back rather than deriving an empty title.
    """
    stem = os.path.splitext(remediation.original_filename)[0]
    return stem or DEFAULT_TITLE


class MetadataService(ArtifactService):
    """Normalizes accessibility metadata OpenDataLoader's tagging leaves incomplete —
    `MarkInfo`/`Lang`/title/tab-order (ADR 0003 stage 3), via pikepdf. Doesn't touch tag
    structure; see ADR 0010 for why this step is `finalize_metadata`, not `finalize_tags`.
    """

    step = RemediationArtifact.Step.FINALIZE_METADATA

    def __init__(
        self,
        adapter: MetadataAdapter | None = None,
        error_logging: ErrorLoggingClient | None = None,
    ) -> None:
        super().__init__(error_logging)
        self.adapter = adapter or PikePdfAdapter()

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                output_path = self.adapter.finalize(
                    pdf_path,
                    output_dir=local_output_dir,
                    title=_derive_title(remediation),
                    lang=settings.LANGUAGE_CODE,
                )
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class LinkService(ArtifactService):
    """Gives every untagged `/Link` annotation a struct-tree presence (ADR 0003 stage 5,
    "Repair links"), via pikepdf. No title/lang derivation — this step only touches
    annotation/struct-tree wiring, not document metadata.
    """

    step = RemediationArtifact.Step.LINK_TAG

    def __init__(
        self,
        adapter: LinkAdapter | None = None,
        error_logging: ErrorLoggingClient | None = None,
    ) -> None:
        super().__init__(error_logging)
        self.adapter = adapter or LinkPikePdfAdapter()

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                output_path = self.adapter.repair(pdf_path, output_dir=local_output_dir)
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class AltTextService(ArtifactService):
    """Generates WCAG-standard `/Alt` descriptions for untagged, non-decorative `<Figure>`
    elements via Claude Vision (ADR 0003 stage 6, "Enrich figures"; ADR 0005). Splits PDF-
    side work (`AltTextAdapter`) from the vision-API call (`AltTextClient`) rather than one
    class doing both — this is the only stage that needs both an outside-package and an
    outside-API integration.
    """

    step = RemediationArtifact.Step.ALT_TEXT

    def __init__(
        self,
        adapter: AltTextAdapter | None = None,
        client: AltTextClient | None = None,
        error_logging: ErrorLoggingClient | None = None,
    ) -> None:
        super().__init__(error_logging)
        self.adapter = adapter or AltTextPikePdfAdapter()
        self.client = client or ClaudeVisionClient(
            api_key=settings.ANTHROPIC_API_KEY, model=settings.CLAUDE_VISION_MODEL
        )

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        document_title = _derive_title(remediation)

        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                candidates = self.adapter.collect_figures(pdf_path)
                alt_by_ref: dict[tuple[int, int], str] = {}
                for candidate in candidates:
                    if candidate.decorative:
                        alt_by_ref[candidate.ref] = ""
                        continue
                    alt_by_ref[candidate.ref] = self.client.describe(
                        candidate.image_bytes,
                        media_type=candidate.media_type,
                        document_title=document_title,
                        page_number=candidate.page_number,
                    )
                output_path = self.adapter.write_alt_text(
                    pdf_path, output_dir=local_output_dir, alt_by_ref=alt_by_ref
                )
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class ScoringService(ArtifactService):
    """Heuristic scoring (add_confidence_scoring experiment) — non-blocking even before
    ADR 0013 made that the norm for every step, since a score is advisory, never a
    compliance determination.
    """

    step = RemediationArtifact.Step.SCORING

    def __init__(
        self,
        adapter: ScoringAdapter | None = None,
        error_logging: ErrorLoggingClient | None = None,
    ) -> None:
        super().__init__(error_logging)
        self.adapter = adapter or ScoringPikePdfAdapter()

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with self.local_input_copy(pdf_uri) as pdf_path:
                result = self.adapter.score(pdf_path)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        RemediationScore.objects.create(
            remediation=remediation,
            score=result.score,
            grade=result.grade,
            manual_review_items=result.manual_review_items,
        )
        self.mark_completed(remediation, output_uri=pdf_uri)
        return pdf_uri
