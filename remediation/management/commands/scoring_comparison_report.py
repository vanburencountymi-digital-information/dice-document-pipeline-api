from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from remediation.models import Remediation, RemediationArtifact


class Command(BaseCommand):
    """Read-only report for the add_confidence_scoring experiment: prints each
    Remediation's heuristic score/grade next to its postcheck (veraPDF) verdict.
    """

    help = "Prints each Remediation's heuristic score next to its postcheck verdict."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--service-account", default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        qs = Remediation.objects.select_related("score").prefetch_related("verification_results")
        if options["service_account"]:
            qs = qs.filter(service_account_id=options["service_account"])

        for remediation in qs.order_by("-created_at")[: options["limit"]]:
            verdict = self._postcheck_verdict(remediation)
            score = getattr(remediation, "score", None)
            name = remediation.original_filename or "(untitled)"
            score_str = f"{score.score:>3} ({score.grade})" if score else "  - (-)"
            items = len(score.manual_review_items) if score else "-"
            self.stdout.write(
                f"{remediation.id}  {name:40}  postcheck={verdict:20}  "
                f"score={score_str}  manual_items={items}"
            )

    def _postcheck_verdict(self, remediation: Remediation) -> str:
        """postcheck's actual verdict isn't a status field — read it off VerificationResult."""
        result = next(
            (
                r
                for r in remediation.verification_results.all()
                if r.step == RemediationArtifact.Step.POSTCHECK
            ),
            None,
        )
        if result is None:
            return "not_reached"
        return "compliant" if result.is_compliant else "not_compliant"
