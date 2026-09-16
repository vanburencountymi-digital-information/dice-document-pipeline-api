"""
Extracts veraPDF's PDF/UA-1 rule catalog from its CLI jar into a JSON snapshot
"""

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from django.core.management.base import BaseCommand, CommandError, CommandParser

# The profile document veraPDF's own CLI jar bundles for the ua1 flavour — see
# `VeraPDFAdapter`'s default `flavour="ua1"`. Hardcoded rather than discovered, since a
# different entry name would mean a genuinely different profile (e.g. ua2), not a version
# bump of this one.
PROFILE_ENTRY = "org/verapdf/pdfa/validation/PDFUA-1.xml"
NS = {"vp": "http://www.verapdf.org/ValidationProfile"}

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent.parent / "adapters/verification/pdfua1_catalog.json"
)


def _sort_key(clause: str) -> tuple:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"[.,\s]+", clause))


class Command(BaseCommand):
    """Extracts veraPDF's own PDF/UA-1 validation profile (the complete rule catalog it
    actually checks — clause, test number, description) from its CLI jar, and writes it to
    a checked-in JSON snapshot (simplify_vera_printouts). This is the authoritative source
    for building/auditing `severity.CLAUSE_SEVERITY`.
    """

    help = "Extracts veraPDF's PDF/UA-1 rule catalog from its CLI jar into a JSON snapshot."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--jar-path",
            required=True,
            help="Path to veraPDF's cli-*.jar (e.g. /opt/verapdf/bin/cli-1.30.2.jar). "
            "Required, not auto-discovered — this command must work the same regardless of "
            "any one Docker image's install layout.",
        )
        parser.add_argument(
            "--output",
            default=str(DEFAULT_OUTPUT),
            help=f"Where to write the JSON snapshot (default: {DEFAULT_OUTPUT}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        jar_path = Path(options["jar_path"])
        output_path = Path(options["output"])

        if not jar_path.exists():
            raise CommandError(f"No such file: {jar_path}")

        try:
            with zipfile.ZipFile(jar_path) as jar:
                profile_xml = jar.read(PROFILE_ENTRY)
        except KeyError:
            raise CommandError(
                f"{jar_path} has no {PROFILE_ENTRY} entry — is this really a veraPDF CLI jar?"
            ) from None
        except zipfile.BadZipFile as exc:
            raise CommandError(f"{jar_path} is not a valid jar/zip: {exc}") from exc

        root = ElementTree.fromstring(profile_xml)
        rules = []
        for rule in root.findall(".//vp:rules/vp:rule", NS):
            rule_id = rule.find("vp:id", NS)
            if rule_id is None:
                continue
            rules.append(
                {
                    "clause": rule_id.get("clause", ""),
                    "test_number": rule_id.get("testNumber", ""),
                    "specification": rule_id.get("specification", ""),
                    "object": rule.get("object", ""),
                    "tags": rule.get("tags", ""),
                    "description": (rule.findtext("vp:description", namespaces=NS) or "").strip(),
                }
            )
        rules.sort(key=lambda r: _sort_key(r["clause"]))

        version_match = re.search(r"cli-(.+)\.jar$", jar_path.name)
        snapshot = {
            "source_jar": jar_path.name,
            "verapdf_version": version_match.group(1) if version_match else None,
            "flavour": root.get("flavour"),
            "rule_count": len(rules),
            "rules": rules,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(snapshot, indent=2) + "\n")

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(rules)} rules ({snapshot['verapdf_version']}, "
                f"{snapshot['flavour']}) to {output_path}"
            )
        )
