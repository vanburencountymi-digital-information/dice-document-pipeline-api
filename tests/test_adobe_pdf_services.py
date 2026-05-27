from pathlib import Path
from unittest.mock import patch

from dice_document_pipeline.workers.adobe_pdf_services import (
    AdobePdfServicesSdkAdapter,
    _AdobeSdkSymbols,
)


class FakeMediaType:
    PDF = "application/pdf"


class FakeParams:
    def __init__(self, *, generate_report: bool, shift_headings: bool):
        self.generate_report = generate_report
        self.shift_headings = shift_headings


class FakeJob:
    def __init__(self, *, input_asset, autotag_pdf_params):
        self.input_asset = input_asset
        self.autotag_pdf_params = autotag_pdf_params


class FakeCredentials:
    def __init__(self, *, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret


class FakeStream:
    def __init__(self, payload: bytes):
        self.payload = payload

    def get_input_stream(self) -> bytes:
        return self.payload


class FakeResultPayload:
    def get_tagged_pdf(self):
        return "tagged-asset"

    def get_report(self):
        return "report-asset"


class FakeResult:
    def get_result(self):
        return FakeResultPayload()


class FakePdfServices:
    def __init__(self, credentials=None):
        self.credentials = credentials
        self.uploaded = None
        self.submitted_job = None

    def upload(self, *, input_stream: bytes, mime_type: str):
        self.uploaded = {"input_stream": input_stream, "mime_type": mime_type}
        return "input-asset"

    def submit(self, job):
        self.submitted_job = job
        return "job-location"

    def get_job_result(self, location, result_cls):
        assert location == "job-location"
        assert result_cls is FakeResult
        return FakeResult()

    def get_content(self, asset):
        if asset == "tagged-asset":
            return FakeStream(b"tagged pdf")
        if asset == "report-asset":
            return FakeStream(b"report bytes")
        raise AssertionError(f"Unexpected asset: {asset}")


def fake_symbols():
    return _AdobeSdkSymbols(
        credentials_cls=FakeCredentials,
        pdf_services_cls=FakePdfServices,
        media_type_cls=FakeMediaType,
        job_cls=FakeJob,
        params_cls=FakeParams,
        result_cls=FakeResult,
    )


def test_sdk_adapter_autotags_pdf_and_writes_report(tmp_path: Path):
    source = tmp_path / "source.pdf"
    destination = tmp_path / "tagged.pdf"
    source.write_bytes(b"source pdf")
    fake_service = FakePdfServices()

    with patch(
        "dice_document_pipeline.workers.adobe_pdf_services._load_adobe_sdk_symbols",
        return_value=fake_symbols(),
    ):
        adapter = AdobePdfServicesSdkAdapter(pdf_services=fake_service)
        result = adapter.autotag_pdf(source, destination)

    assert destination.read_bytes() == b"tagged pdf"
    assert destination.with_suffix(".report.xlsx").read_bytes() == b"report bytes"
    assert result.output_pdf == destination
    assert result.api_calls[0]["mode"] == "sdk"
    assert result.api_calls[0]["estimated_transactions_per_page"] == 10
    assert fake_service.uploaded == {
        "input_stream": b"source pdf",
        "mime_type": FakeMediaType.PDF,
    }
    assert fake_service.submitted_job.autotag_pdf_params.generate_report is True
    assert fake_service.submitted_job.autotag_pdf_params.shift_headings is True


def test_sdk_adapter_builds_pdf_services_from_environment():
    with patch(
        "dice_document_pipeline.workers.adobe_pdf_services._load_adobe_sdk_symbols",
        return_value=fake_symbols(),
    ), patch.dict(
        "os.environ",
        {
            "PDF_SERVICES_CLIENT_ID": "client-id",
            "PDF_SERVICES_CLIENT_SECRET": "client-secret",
        },
    ):
        adapter = AdobePdfServicesSdkAdapter()

    assert isinstance(adapter.pdf_services, FakePdfServices)
    assert adapter.pdf_services.credentials.client_id == "client-id"
    assert adapter.pdf_services.credentials.client_secret == "client-secret"


def test_sdk_adapter_requires_environment_credentials():
    with patch(
        "dice_document_pipeline.workers.adobe_pdf_services._load_adobe_sdk_symbols",
        return_value=fake_symbols(),
    ), patch.dict("os.environ", {}, clear=True):
        try:
            AdobePdfServicesSdkAdapter()
        except EnvironmentError:
            pass
        else:
            raise AssertionError("Expected missing Adobe credentials to fail")
