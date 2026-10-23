from typing import Any

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import IncompleteLimitsError, ServiceAccount
from api.renderers import PDFRenderer
from api.serializers import ForbiddenSerializer, IPThrottledSerializer, SubmissionLimitSerializer
from api.submission_limits import SubmissionLimitLike, limit_for
from remediation.models import Remediation
from remediation.serializers import RemediationSerializer, RemediationUploadSerializer
from remediation.services import RemediationService
from remediation.tasks import process_remediation, send_webhook_notification

CONTENT_HASH_PARAM = OpenApiParameter(
    "content_hash",
    OpenApiTypes.STR,
    OpenApiParameter.PATH,
    description="The `document_id` returned when the document was submitted.",
)


LIMITS_NOT_SET_MESSAGE = "This account's limits are not fully set. Ask the administrator."


class ServiceAccountRequiredMixin(APIView):
    """Token-authenticates a request (with the project's default login class, which locks out
    IP addresses that guess wrong tokens) and exposes the caller's `ServiceAccount`."""

    permission_classes = [IsAuthenticated]

    @property
    def service_account(self) -> ServiceAccount:
        """Returns: the logged-in caller's service account."""
        # IsAuthenticated already guarantees this; narrows the type for mypy.
        account = ServiceAccount.for_user(self.request.user)
        assert account is not None
        return account


class EnforceAccountLimitsMixin(ServiceAccountRequiredMixin):
    """Logged-in views that apply the caller's service-account limits (ADR 0030).

    Denies every request (403) if the account's limits are half-filled, which saving refuses
    but would otherwise mean "unlimited". Adds `X-RateLimit-Limit`, `-Remaining` and `-Reset`
    (seconds) to every response for an account that has a submission limit. A view that wants
    the submission limit enforced passes `self.submission_limit.check` to the service.
    """

    _submission_limit: SubmissionLimitLike | None = None

    @property
    def submission_limit(self) -> SubmissionLimitLike:
        """Returns: the account's submission limit (set up in `initial`)."""
        assert self._submission_limit is not None
        return self._submission_limit

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        """Runs after login and permission checks, before the view body.

        Takes: the request (and the URL arguments, unused). Raises: `PermissionDenied` if the
        account's limits are half-filled.
        """
        super().initial(request, *args, **kwargs)
        try:
            self._submission_limit = limit_for(self.service_account)
        except IncompleteLimitsError:
            raise PermissionDenied(LIMITS_NOT_SET_MESSAGE) from None

    def finalize_response(
        self, request: Request, response: Response, *args: Any, **kwargs: Any
    ) -> Response:
        """Adds the `X-RateLimit-*` headers (if the account has a limit) to any response.

        Takes: the request and the response so far. Returns: the response.
        """
        response = super().finalize_response(request, response, *args, **kwargs)
        # Failed logins and denied accounts never got a throttle, so there is nothing to report.
        if self._submission_limit is not None:
            for name, value in self._submission_limit.headers().items():
                response[name] = value
        return response


class CreateRemediationView(EnforceAccountLimitsMixin):
    """
    Handles incoming files for remediation jobs.

    If the file (by content hash) is new for the service account, enqueues a new
    remediation job.

    Otherwise finds the most recent existing job for that file — unless `force=true` is
    passed, or the existing job is NONCOMPLIANT/ERROR and old enough to auto-retry (ADR 0014), in
    which case a new attempt is enqueued instead.

    An optional `callback_url` registers a webhook subscription (ADR 0015) on whichever
    attempt the request resolved to, new or deduped — any number of callers can each
    register their own callback on the same attempt. If that attempt is already terminal
    (see `Remediation.FINISHED_STATUSES`) at submission time — i.e. subscribing after the fact — the
    notification is enqueued immediately rather than waiting for a pipeline run that isn't
    going to happen.

    Takes:
        a PDF document, and optionally `force` (bool, default false) and `callback_url`.

    Returns:
        Response with the serialized remediation job (id, document_id, original_filename,
        status, pipeline_version, error, created_at, started_at, completed_at,
        verification_results, download_url).
    """

    @extend_schema(
        summary="Submit a PDF for remediation",
        request={"multipart/form-data": RemediationUploadSerializer},
        responses={
            200: RemediationSerializer,
            201: RemediationSerializer,
            403: ForbiddenSerializer,
            429: SubmissionLimitSerializer,
        },
        description=(
            "201 means a new job was queued. 200 means this exact file was already submitted "
            "and the existing job is returned (unless `force` is true). Only new jobs count "
            "toward your submission limit. 429 means you are over it: wait `Retry-After` "
            "seconds and send again; nothing was created. A 429 with only a `detail` message "
            "instead means too many requests from your IP address, or too many wrong tokens."
        ),
    )
    def post(self, request: Request) -> Response:
        """Takes: the multipart upload. Returns: 201 with the new job, or 200 with the existing one.

        Raises: 429 if the account is over its submission limit (nothing is made then).
        """
        upload = RemediationUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)

        service = RemediationService()
        # The service runs the limit check only once it knows a new job is needed, and the
        # check locks the account's row, so two requests at the same moment can't both squeeze
        # under the limit. A rejected request creates nothing, and a deduped resubmit makes no
        # job, so it is never turned away or counted.
        remediation, created = service.get_or_create_from_upload(
            self.service_account,
            upload.validated_data["file"],
            force=upload.validated_data["force"],
            before_create=self.submission_limit.check,
        )

        callback_url = upload.validated_data["callback_url"]
        if callback_url:
            callback, callback_created = service.register_callback(remediation, callback_url)
            # If job is already completed, post to webhook now.
            if callback_created and remediation.status in Remediation.FINISHED_STATUSES:
                send_webhook_notification.enqueue(str(callback.id))

        if service.claim_enqueue(remediation, created):
            process_remediation.enqueue(str(remediation.id))
            remediation.refresh_from_db()
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(RemediationSerializer(remediation).data, status=response_status)


class DocumentStatusView(EnforceAccountLimitsMixin):
    """
    Looks up and returns the status of a document's most recent remediation attempt.

    Takes:
        content_hash: the SHA-256 hex digest identifying the document.

    Returns:
        Response with the serialized remediation job (id, document_id, original_filename,
        status, pipeline_version, error, created_at, started_at, completed_at,
        verification_results, download_url).
        Returns 404 if no remediation job for that file + that service account.
    """

    @extend_schema(
        summary="Get a document's status",
        parameters=[CONTENT_HASH_PARAM],
        responses={
            200: RemediationSerializer,
            403: ForbiddenSerializer,
            429: IPThrottledSerializer,
        },
    )
    def get(self, request: Request, content_hash: str) -> Response:
        remediation = RemediationService().latest_for_document(self.service_account, content_hash)
        if remediation is None:
            raise Http404
        return Response(RemediationSerializer(remediation).data)


class DocumentDownloadView(EnforceAccountLimitsMixin):
    """
    Serves a document's most recent remediation attempt's finished output file.

    Serves it regardless of `status` (ADR 0013/0015) — a NONCOMPLIANT or ERROR attempt can still have a
    partially-remediated file worth downloading — as long as one was actually produced.

    Takes:
        content_hash: the SHA-256 hex digest identifying the document.

    Returns:
        The PDF as an attachment. 404 if no remediation job for that file + that service
        account, or if nothing has been produced yet (e.g. still QUEUED/RUNNING).
    """

    renderer_classes = [PDFRenderer, JSONRenderer]

    def handle_exception(self, exc: Exception) -> Response:
        """Takes: the error raised while handling the request. Returns: the error response."""
        # Errors (e.g. 404) are JSON even when the caller asked for a PDF; the PDF renderer
        # can't render an error message.
        self.request.accepted_renderer = JSONRenderer()
        self.request.accepted_media_type = JSONRenderer.media_type
        return super().handle_exception(exc)

    @extend_schema(
        summary="Download the remediated PDF",
        parameters=[CONTENT_HASH_PARAM],
        responses={
            (200, "application/pdf"): OpenApiTypes.BINARY,
            404: inline_serializer("NotFound", {"detail": serializers.CharField()}),
            403: ForbiddenSerializer,
            429: IPThrottledSerializer,
        },
    )
    def get(self, request: Request, content_hash: str) -> FileResponse:
        remediation = RemediationService().latest_for_document(self.service_account, content_hash)
        if remediation is None or not remediation.final_output_uri:
            raise Http404
        return FileResponse(
            default_storage.open(remediation.final_output_uri),
            content_type="application/pdf",
            filename=remediation.original_filename or "document.pdf",
            as_attachment=True,
        )


@extend_schema(exclude=True)
class PingView(APIView):
    """Answers `pong!` to anyone, for Cloud Run's probes and scripts that wake the app up
    (ADR 0037). It checks no login and no other service. The default per-IP throttle still
    applies, since this is a public URL.
    """

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> HttpResponse:
        """Takes: the request. Returns: a plain-text `200 pong!`."""
        return HttpResponse("pong!", content_type="text/plain")
