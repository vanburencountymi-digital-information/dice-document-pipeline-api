from __future__ import annotations

import hashlib
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.utils import timezone
from parameterized import parameterized

from accounts.tests.factories import ServiceAccountFactory
from remediation.models import (
    Remediation,
)
from remediation.services.remediation_service import RemediationService
from remediation.tests.factories import (
    PdfUploadFactory,
    PipelineConfigFactory,
    RemediationFactory,
)


class RemediationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()

    def test_get_returns_remediation_by_id(self) -> None:
        remediation = RemediationFactory(service_account=self.service_account)

        found = RemediationService().get(str(remediation.id))

        self.assertEqual(found, remediation)

    def test_create_links_remediation_to_service_account_and_queues_it(self) -> None:
        remediation = RemediationService().create(
            self.service_account,
            source_pdf_uri="local:///tmp/document.pdf",
            content_hash="abc123",
        )

        self.assertEqual(remediation.service_account, self.service_account)
        self.assertEqual(remediation.source_pdf_uri, "local:///tmp/document.pdf")
        self.assertEqual(remediation.status, Remediation.JobStatus.QUEUED)

    @override_settings(PIPELINE_VERSION="1.4.2")
    def test_create_stamps_pipeline_version_from_settings(self) -> None:
        remediation = RemediationService().create(
            self.service_account,
            source_pdf_uri="local:///tmp/document.pdf",
            content_hash="abc123",
        )

        self.assertEqual(remediation.pipeline_version, "1.4.2")

    def test_latest_for_document_returns_most_recent_attempt(self) -> None:
        RemediationFactory(
            service_account=self.service_account,
            content_hash="abc123",
            status=Remediation.JobStatus.COMPLIANT,
        )
        newest = RemediationFactory(service_account=self.service_account, content_hash="abc123")

        found = RemediationService().latest_for_document(self.service_account, "abc123")

        self.assertEqual(found, newest)

    def test_latest_for_document_includes_failed_remediation(self) -> None:
        remediation = RemediationFactory(
            service_account=self.service_account,
            content_hash="abc123",
            status=Remediation.JobStatus.ERROR,
        )

        found = RemediationService().latest_for_document(self.service_account, "abc123")

        self.assertEqual(found, remediation)

    def test_latest_for_document_returns_none_when_no_match(self) -> None:
        found = RemediationService().latest_for_document(self.service_account, "abc123")

        self.assertIsNone(found)

    def test_latest_for_document_ignores_other_service_accounts(self) -> None:
        RemediationFactory(content_hash="abc123")

        found = RemediationService().latest_for_document(self.service_account, "abc123")

        self.assertIsNone(found)

    def test_claim_for_running_sets_status_and_started_at(self) -> None:
        remediation = RemediationFactory()

        claimed = RemediationService().claim_for_running(remediation)

        self.assertTrue(claimed)
        self.assertEqual(remediation.status, Remediation.JobStatus.RUNNING)
        self.assertIsNotNone(remediation.started_at)
        remediation.refresh_from_db()
        self.assertEqual(remediation.status, Remediation.JobStatus.RUNNING)

    @parameterized.expand([(status.value, status) for status in Remediation.FINISHED_STATUSES])
    def test_claim_for_running_refuses_a_finished_attempt_even_if_the_object_is_stale(
        self, _name, finished_status
    ) -> None:
        remediation = RemediationFactory(status=Remediation.JobStatus.QUEUED)
        # Another worker finishes the row after this copy was loaded.
        Remediation.objects.filter(pk=remediation.pk).update(status=finished_status)

        claimed = RemediationService().claim_for_running(remediation)

        self.assertFalse(claimed)
        self.assertEqual(remediation.status, Remediation.JobStatus.QUEUED)
        remediation.refresh_from_db()
        self.assertEqual(remediation.status, finished_status)
        self.assertIsNone(remediation.started_at)

    def test_claim_for_running_reclaims_a_running_attempt_left_by_a_crashed_worker(self) -> None:
        remediation = RemediationFactory(status=Remediation.JobStatus.RUNNING)

        claimed = RemediationService().claim_for_running(remediation)

        self.assertTrue(claimed)

    @parameterized.expand(
        [
            ("compliant", "mark_compliant", Remediation.JobStatus.COMPLIANT),
            ("skipped", "mark_skipped", Remediation.JobStatus.SKIPPED),
        ]
    )
    def test_mark_verdictless_sets_status_completed_at_and_final_output_uri(
        self, _name, method_name, expected_status
    ) -> None:
        remediation = RemediationFactory()

        getattr(RemediationService(), method_name)(
            remediation, final_output_uri="remediations/final.pdf"
        )

        self.assertEqual(remediation.status, expected_status)
        self.assertIsNotNone(remediation.completed_at)
        self.assertEqual(remediation.final_output_uri, "remediations/final.pdf")
        self.assertEqual(remediation.error, "")

    @parameterized.expand(
        [
            ("noncompliant", "mark_noncompliant", Remediation.JobStatus.NONCOMPLIANT),
            ("error", "mark_error", Remediation.JobStatus.ERROR),
        ]
    )
    def test_mark_noncompliant_or_error_sets_status_error_completed_at_and_final_output_uri(
        self, _name, method_name, expected_status
    ) -> None:
        remediation = RemediationFactory()

        getattr(RemediationService(), method_name)(
            remediation, "Timeout error during OCR", final_output_uri="remediations/partial.pdf"
        )

        self.assertEqual(remediation.status, expected_status)
        self.assertEqual(remediation.error, "Timeout error during OCR")
        self.assertIsNotNone(remediation.completed_at)
        self.assertEqual(remediation.final_output_uri, "remediations/partial.pdf")

    def test_register_callback_creates_a_new_row(self) -> None:
        remediation = RemediationFactory()

        callback, created = RemediationService().register_callback(
            remediation, "https://example.com/webhook"
        )

        self.assertTrue(created)
        self.assertEqual(callback.remediation, remediation)
        self.assertEqual(callback.callback_url, "https://example.com/webhook")

    def test_register_callback_is_idempotent_for_the_same_url(self) -> None:
        remediation = RemediationFactory()
        service = RemediationService()
        first, _ = service.register_callback(remediation, "https://example.com/webhook")

        second, created = service.register_callback(remediation, "https://example.com/webhook")

        self.assertFalse(created)
        self.assertEqual(second, first)
        self.assertEqual(remediation.callbacks.count(), 1)

    def test_register_callback_allows_multiple_distinct_subscribers(self) -> None:
        remediation = RemediationFactory()
        service = RemediationService()

        service.register_callback(remediation, "https://example.com/webhook-a")
        service.register_callback(remediation, "https://example.com/webhook-b")

        self.assertEqual(remediation.callbacks.count(), 2)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RemediationServiceGetOrCreateFromUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()

    def test_creates_new_remediation_and_saves_file_for_new_content(self) -> None:
        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf")
        )

        self.assertTrue(created)
        self.assertEqual(remediation.service_account, self.service_account)
        self.assertEqual(remediation.status, Remediation.JobStatus.QUEUED)
        self.assertEqual(remediation.original_filename, "test.pdf")
        expected_uri = f"remediations/{self.service_account.id}/{remediation.content_hash}/test.pdf"
        self.assertEqual(remediation.source_pdf_uri, expected_uri)
        self.assertTrue(default_storage.exists(remediation.source_pdf_uri))

    def test_resubmitting_errored_remediation_reports_it_without_new_job_or_file(self) -> None:
        content = b"same bytes"
        failed, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )
        failed.status = Remediation.JobStatus.ERROR
        failed.save(update_fields=["status"])
        original_path = failed.source_pdf_uri

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )

        self.assertFalse(created)
        self.assertEqual(remediation, failed)
        self.assertEqual(remediation.status, Remediation.JobStatus.ERROR)
        self.assertEqual(Remediation.objects.count(), 1)
        self.assertEqual(remediation.source_pdf_uri, original_path)

    def test_before_create_runs_once_when_a_new_attempt_is_made(self) -> None:
        calls: list[int] = []

        RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(), before_create=lambda: calls.append(1)
        )

        self.assertEqual(calls, [1])

    def test_before_create_does_not_run_when_an_existing_attempt_is_reused(self) -> None:
        content = b"same bytes"
        RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(content=content)
        )
        calls: list[int] = []

        _, created = RemediationService().get_or_create_from_upload(
            self.service_account,
            PdfUploadFactory(content=content),
            before_create=lambda: calls.append(1),
        )

        self.assertFalse(created)
        self.assertEqual(calls, [])

    def test_an_attempt_made_while_before_create_waited_is_returned_not_duplicated(self) -> None:
        content = b"same bytes"
        content_hash = hashlib.sha256(content).hexdigest()

        def another_request_made_it_meanwhile() -> None:
            RemediationFactory(service_account=self.service_account, content_hash=content_hash)

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account,
            PdfUploadFactory(content=content),
            before_create=another_request_made_it_meanwhile,
        )

        self.assertFalse(created)
        self.assertEqual(remediation.content_hash, content_hash)
        self.assertEqual(Remediation.objects.count(), 1)

    def test_nothing_is_made_if_before_create_refuses(self) -> None:
        def refuse() -> None:
            raise PermissionError("over the limit")

        with self.assertRaises(PermissionError):
            RemediationService().get_or_create_from_upload(
                self.service_account, PdfUploadFactory(), before_create=refuse
            )

        self.assertFalse(Remediation.objects.exists())

    def test_force_on_an_unfinished_attempt_returns_it_instead_of_making_another(self) -> None:
        content = b"same bytes"
        running, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account,
            PdfUploadFactory(name="test.pdf", content=content),
            force=True,
        )

        self.assertFalse(created)
        self.assertEqual(remediation, running)
        self.assertEqual(Remediation.objects.count(), 1)

    def test_force_on_an_unfinished_attempt_never_runs_the_limit_check(self) -> None:
        content = b"same bytes"
        running, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )
        calls: list[int] = []

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account,
            PdfUploadFactory(name="test.pdf", content=content),
            force=True,
            before_create=lambda: calls.append(1),
        )

        self.assertEqual((remediation, created, calls), (running, False, []))

    def test_a_clash_with_an_attempt_made_at_the_same_moment_returns_that_attempt(self) -> None:
        content = b"same bytes"
        service = RemediationService()
        first, first_created = service.get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )

        # The race: our lookup finds nothing, then making the attempt clashes with `first`.
        with patch.object(
            RemediationService, "latest_for_document", autospec=True, side_effect=[None, first]
        ):
            second, second_created = service.get_or_create_from_upload(
                self.service_account, PdfUploadFactory(name="test.pdf", content=content)
            )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(second, first)
        self.assertEqual(Remediation.objects.count(), 1)

    def test_force_retries_a_complete_remediation(self) -> None:
        content = b"same bytes"
        existing, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )
        existing.status = Remediation.JobStatus.COMPLIANT
        existing.save(update_fields=["status"])

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account,
            PdfUploadFactory(name="test.pdf", content=content),
            force=True,
        )

        self.assertTrue(created)
        self.assertNotEqual(remediation, existing)
        self.assertEqual(remediation.content_hash, existing.content_hash)
        self.assertEqual(Remediation.objects.count(), 2)

    def test_force_retries_an_errored_remediation(self) -> None:
        content = b"same bytes"
        failed, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )
        failed.status = Remediation.JobStatus.ERROR
        failed.save(update_fields=["status"])

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account,
            PdfUploadFactory(name="test.pdf", content=content),
            force=True,
        )

        self.assertTrue(created)
        self.assertNotEqual(remediation, failed)

    @parameterized.expand(
        [
            ("stale_version_auto_retries", "0.9.0", "1.0.0", True),
            ("version_exactly_at_floor_does_not_retry", "1.0.0", "1.0.0", False),
            ("version_above_floor_does_not_retry", "1.1.0", "1.0.0", False),
            ("blank_floor_does_not_retry", "1.0.0", "", False),
        ]
    )
    def test_failed_remediation_retry_gated_by_floor_comparison(
        self, _name, existing_version, floor_version, expected_created
    ) -> None:
        self._assert_retry_gated_by_floor(
            Remediation.JobStatus.ERROR, existing_version, floor_version, expected_created
        )

    @parameterized.expand(
        [
            ("noncompliant_retries", Remediation.JobStatus.NONCOMPLIANT, True),
            ("error_retries", Remediation.JobStatus.ERROR, True),
            ("compliant_does_not_retry", Remediation.JobStatus.COMPLIANT, False),
            ("skipped_does_not_retry", Remediation.JobStatus.SKIPPED, False),
        ]
    )
    def test_retry_below_floor_depends_on_status(self, _name, status, expected_created) -> None:
        self._assert_retry_gated_by_floor(status, "0.9.0", "1.0.0", expected_created)

    def _assert_retry_gated_by_floor(
        self, status, existing_version, floor_version, expected_created
    ) -> None:
        content = b"same bytes"
        with override_settings(PIPELINE_VERSION=existing_version):
            failed, _ = RemediationService().get_or_create_from_upload(
                self.service_account, PdfUploadFactory(name="test.pdf", content=content)
            )
        failed.status = status
        failed.save(update_fields=["status"])
        PipelineConfigFactory(retry_floor_version=floor_version)

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )

        self.assertEqual(created, expected_created)
        if not expected_created:
            self.assertEqual(remediation, failed)

    def test_returns_existing_remediation_without_touching_storage_for_duplicate(self) -> None:
        content = b"same bytes"
        existing, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(content=content)
        )

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(content=content)
        )

        self.assertFalse(created)
        self.assertEqual(remediation, existing)
        self.assertEqual(Remediation.objects.count(), 1)

    def test_reuses_content_hash_across_differently_named_files(self) -> None:
        content = b"same bytes"
        existing, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="first.pdf", content=content)
        )

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="second.pdf", content=content)
        )

        self.assertFalse(created)
        self.assertEqual(remediation, existing)


class RemediationServiceRecentSubmissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()

    def _attempt(self, age: timedelta, account=None) -> Remediation:
        remediation = RemediationFactory(
            service_account=account or self.service_account,
            status=Remediation.JobStatus.COMPLIANT,
        )
        # auto_now_add can't be set on create
        Remediation.objects.filter(pk=remediation.pk).update(created_at=timezone.now() - age)
        return remediation

    def test_counts_only_this_accounts_attempts_inside_the_window(self) -> None:
        self._attempt(timedelta(hours=1))
        self._attempt(timedelta(hours=23))
        self._attempt(timedelta(hours=25))
        self._attempt(timedelta(hours=1), account=ServiceAccountFactory())

        usage = RemediationService().recent_submissions(self.service_account, 24 * 3600)

        self.assertEqual(usage.count, 2)

    def test_oldest_is_when_the_earliest_counted_attempt_was_made(self) -> None:
        self._attempt(timedelta(hours=1))
        oldest = self._attempt(timedelta(hours=5))
        oldest.refresh_from_db()

        usage = RemediationService().recent_submissions(self.service_account, 24 * 3600)

        self.assertEqual(usage.oldest, oldest.created_at)

    def test_no_attempts_gives_zero_and_no_oldest(self) -> None:
        usage = RemediationService().recent_submissions(self.service_account, 3600)

        self.assertEqual((usage.count, usage.oldest), (0, None))

    def test_lock_needs_a_transaction(self) -> None:
        # TestCase wraps each test in one, so locking works here.
        usage = RemediationService().recent_submissions(self.service_account, 3600, lock=True)

        self.assertEqual(usage.count, 0)


@override_settings(STUCK_QUEUED_MINUTES=60, STUCK_RUNNING_MINUTES=180)
class RemediationServiceClaimEnqueueTests(TestCase):
    def _attempt(
        self,
        status,
        created_ago: int,
        started_ago: int | None = None,
        enqueued_ago: int | None = None,
    ) -> Remediation:
        now = timezone.now()
        remediation = RemediationFactory(status=status)
        Remediation.objects.filter(pk=remediation.pk).update(
            created_at=now - timedelta(minutes=created_ago),
            started_at=now - timedelta(minutes=started_ago) if started_ago else None,
            last_enqueued_at=now - timedelta(minutes=enqueued_ago) if enqueued_ago else None,
        )
        remediation.refresh_from_db()
        return remediation

    @parameterized.expand(
        [
            ("queued_past_its_limit", Remediation.JobStatus.QUEUED, 90, None, None, True),
            ("queued_within_its_limit", Remediation.JobStatus.QUEUED, 30, None, None, False),
            ("running_past_its_limit", Remediation.JobStatus.RUNNING, 300, 240, None, True),
            ("running_within_its_limit", Remediation.JobStatus.RUNNING, 300, 120, None, False),
            (
                "running_is_judged_from_its_start",
                Remediation.JobStatus.RUNNING,
                500,
                5,
                None,
                False,
            ),
            (
                "running_without_a_start_uses_creation",
                Remediation.JobStatus.RUNNING,
                240,
                None,
                None,
                True,
            ),
            (
                "queued_again_recently_is_progress",
                Remediation.JobStatus.QUEUED,
                500,
                None,
                10,
                False,
            ),
            (
                "queued_again_long_ago_is_stuck_again",
                Remediation.JobStatus.QUEUED,
                500,
                None,
                90,
                True,
            ),
            ("compliant_is_never_stuck", Remediation.JobStatus.COMPLIANT, 500, None, None, False),
            ("error_is_never_stuck", Remediation.JobStatus.ERROR, 500, None, None, False),
        ]
    )
    def test_a_reused_attempt_is_claimed_only_if_it_is_stuck(
        self, _label, status, created_ago, started_ago, enqueued_ago, expected
    ) -> None:
        remediation = self._attempt(status, created_ago, started_ago, enqueued_ago)

        self.assertEqual(RemediationService().claim_enqueue(remediation, created=False), expected)

    def test_a_new_attempt_is_always_queued(self) -> None:
        self.assertTrue(RemediationService().claim_enqueue(RemediationFactory(), created=True))

    def test_a_stuck_attempt_is_marked_when_claimed(self) -> None:
        remediation = self._attempt(Remediation.JobStatus.QUEUED, created_ago=90)

        RemediationService().claim_enqueue(remediation, created=False)

        remediation.refresh_from_db()
        self.assertIsNotNone(remediation.last_enqueued_at)

    def test_it_is_not_claimed_again_until_another_full_wait_has_passed(self) -> None:
        remediation = self._attempt(Remediation.JobStatus.QUEUED, created_ago=90)
        service = RemediationService()

        first = service.claim_enqueue(remediation, created=False)
        second = service.claim_enqueue(Remediation.objects.get(pk=remediation.pk), created=False)

        self.assertEqual((first, second), (True, False))

    def test_only_one_of_two_resubmits_that_saw_it_stuck_claims_it(self) -> None:
        saw_it_first = self._attempt(Remediation.JobStatus.QUEUED, created_ago=90)
        saw_it_second = Remediation.objects.get(
            pk=saw_it_first.pk
        )  # both read before either claims
        service = RemediationService()

        results = [
            service.claim_enqueue(saw_it_first, created=False),
            service.claim_enqueue(saw_it_second, created=False),
        ]

        self.assertEqual(results, [True, False])

    def test_it_is_not_claimed_if_it_finished_in_the_meantime(self) -> None:
        remediation = self._attempt(Remediation.JobStatus.QUEUED, created_ago=90)
        Remediation.objects.filter(pk=remediation.pk).update(status=Remediation.JobStatus.COMPLIANT)

        self.assertFalse(RemediationService().claim_enqueue(remediation, created=False))


class RemediationStatusSetsTests(TestCase):
    def test_finished_and_unfinished_statuses_cover_every_status_exactly_once(self) -> None:
        finished = set(Remediation.FINISHED_STATUSES)
        unfinished = set(Remediation.UNFINISHED_STATUSES)

        self.assertFalse(finished & unfinished)
        self.assertEqual(finished | unfinished, set(Remediation.JobStatus))
