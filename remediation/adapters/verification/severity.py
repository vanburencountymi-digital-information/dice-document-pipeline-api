from django.db import models

# The veraPDF version this table's severity judgments were reviewed against — see
# `pdfua1_catalog.json` (produced by `manage.py extract_verapdf_profile`) for the actual
# rule catalog that informed them. If veraPDF is upgraded, re-run that command, diff the new
# snapshot against this one, and bump this constant once the table's been reviewed for the
# new version — a stale mismatch here is a signal the table hasn't been re-checked yet.
BUILT_AGAINST_VERAPDF_VERSION = "1.30.2"


class Severity(models.TextChoices):
    """How badly a failed PDF/UA-1 rule affects an assistive-technology user, not just
    whether it failed. Grounded in DOJ Title II's "good faith effort" framing (prioritize
    by real-world impact, not raw rule count) and WCAG 2.1 AA's own success criteria —
    see `docs/adrs/` for the pipeline's other design decisions; this classification isn't
    yet its own ADR.
    """

    CRITICAL = "critical", "Critical"
    MAJOR = "major", "Major"
    MINOR = "minor", "Minor"
    UNCLASSIFIED = "unclassified", "Unclassified"


# Worst-to-best — shared by `VerificationResult.worst_severity` and the postcheck failure
# summary (services.py) so "how bad is this overall" ranks the same way everywhere.
SEVERITY_RANK: tuple[Severity, ...] = (
    Severity.CRITICAL,
    Severity.MAJOR,
    Severity.MINOR,
    Severity.UNCLASSIFIED,
)


# Exact "{clause}-{testNumber}" overrides, for the two clauses (7.1, 7.2) whose rules span
# every severity tier under one clause number — confirmed against veraPDF 1.30.2's actual
# PDF/UA-1 profile (`remediation/management/commands/extract_verapdf_profile.py`, snapshot
# at `pdfua1_catalog.json`), not guessed. Every rule under 7.1/7.2 in that 1.30.2 snapshot is
# listed here deliberately — a testNumber this table doesn't cover (new in a future veraPDF
# version) falls through to `Severity.UNCLASSIFIED` rather than inheriting a neighbor's
# severity by accident.
RULE_SEVERITY: dict[str, Severity] = {
    # Clause 7.1 ("General") — not one topic: structure-tree/tagging fundamentals sit next
    # to purely declarative metadata technicalities under the same clause number.
    "7.1-1": Severity.CRITICAL,  # Artifact-marked content found inside tagged content
    "7.1-2": Severity.CRITICAL,  # Tagged content found inside Artifact-marked content
    "7.1-3": Severity.CRITICAL,  # Content neither marked Artifact nor tagged as real content
    "7.1-4": Severity.MINOR,  # Suspects flag (auto-tagging honesty marker, not perceptible)
    "7.1-5": Severity.MAJOR,  # Non-standard structure type not mapped to a standard one
    "7.1-6": Severity.MINOR,  # Circular role-map mapping (internal consistency)
    "7.1-7": Severity.MAJOR,  # Standard tag remapped (breaks AT's assumptions about it)
    "7.1-8": Severity.MINOR,  # Metadata stream/key missing (declarative)
    "7.1-9": Severity.CRITICAL,  # dc:title missing — WCAG 2.4.2 Page Titled
    "7.1-10": Severity.CRITICAL,  # DisplayDocTitle not true — WCAG 2.4.2 Page Titled
    "7.1-11": Severity.CRITICAL,  # StructTreeRoot missing entirely — the textbook "no structure"
    "7.1-12": Severity.MAJOR,  # Structure element missing its parent (P) entry
    # Clause 7.2 — despite the name, covers natural-language determination AND table/list/TOC
    # nesting rules; these are two unrelated concerns that happen to share a clause number.
    "7.2-2": Severity.CRITICAL,  # Outline entry language undetermined — WCAG 3.1.1/3.1.2
    "7.2-3": Severity.MAJOR,  # Table element child-type constraint
    "7.2-4": Severity.MAJOR,
    "7.2-5": Severity.MAJOR,
    "7.2-6": Severity.MAJOR,
    "7.2-7": Severity.MAJOR,
    "7.2-8": Severity.MAJOR,
    "7.2-9": Severity.MAJOR,
    "7.2-10": Severity.MAJOR,
    "7.2-11": Severity.MAJOR,
    "7.2-12": Severity.MAJOR,
    "7.2-13": Severity.MAJOR,
    "7.2-14": Severity.MAJOR,
    "7.2-15": Severity.MAJOR,  # Table cell intersection
    "7.2-16": Severity.MAJOR,
    "7.2-17": Severity.MAJOR,  # List nesting
    "7.2-18": Severity.MAJOR,
    "7.2-19": Severity.MAJOR,
    "7.2-20": Severity.MAJOR,
    "7.2-21": Severity.CRITICAL,  # ActualText language undetermined
    "7.2-22": Severity.CRITICAL,  # Alt attribute language undetermined
    "7.2-23": Severity.CRITICAL,  # E attribute language undetermined
    "7.2-24": Severity.CRITICAL,  # Annotation Contents language undetermined
    "7.2-25": Severity.CRITICAL,  # Form field TU language undetermined
    "7.2-26": Severity.MAJOR,  # TOC nesting
    "7.2-27": Severity.MAJOR,
    "7.2-28": Severity.MAJOR,
    "7.2-29": Severity.CRITICAL,  # Lang value isn't a valid language identifier
    "7.2-30": Severity.CRITICAL,  # ActualText (Span MC) language undetermined
    "7.2-31": Severity.CRITICAL,  # Alt (Span MC) language undetermined
    "7.2-32": Severity.CRITICAL,  # E (Span MC) language undetermined
    "7.2-33": Severity.CRITICAL,  # Document metadata language undetermined
    "7.2-34": Severity.CRITICAL,  # Page content language undetermined (confirmed real fixture)
    "7.2-36": Severity.MAJOR,  # THead child-type constraint
    "7.2-37": Severity.MAJOR,
    "7.2-38": Severity.MAJOR,
    "7.2-39": Severity.MAJOR,
    "7.2-40": Severity.MAJOR,  # List Caption position
    "7.2-41": Severity.MAJOR,  # Table row/column count consistency
    "7.2-42": Severity.MAJOR,
    "7.2-43": Severity.MAJOR,
}


# Clause-prefix defaults for every other clause in veraPDF 1.30.2's PDF/UA-1 profile — safe
# to bucket by clause here because (unlike 7.1/7.2) every rule sharing one of these clause
# numbers/prefixes really does share the same real-world impact. Confirmed complete against
# the same snapshot: every clause below is homogeneous in the actual 1.30.2 rule catalog.
CLAUSE_SEVERITY: dict[str, Severity] = {
    # Blocks AT access entirely — WCAG 1.1.1 / 1.3.1 / 2.4.4 / 4.1.2.
    "6.2": Severity.CRITICAL,  # MarkInfo/Marked=true — AT won't attempt the structure tree
    "7.3": Severity.CRITICAL,  # Figures (alt text) — WCAG 1.1.1
    "7.4": Severity.CRITICAL,  # Headings (hierarchy/consistency)
    "7.18.1": Severity.CRITICAL,  # Annotation nesting/hidden rules + form field TU/alt text
    "7.18.4": Severity.CRITICAL,  # Widget-in-Form-tag nesting/role
    "7.18.5": Severity.CRITICAL,  # Links tagged + alternate description — WCAG 2.4.4
    # Degrades comprehension/navigation but doesn't fully block access.
    "7.5": Severity.MAJOR,  # Table header associations
    "7.7": Severity.MAJOR,  # Formulas
    "7.9": Severity.MAJOR,  # Notes (unique IDs)
    "7.18.3": Severity.MAJOR,  # Page Tabs key follows structure order (keyboard/AT nav order)
    # Machine/archival technicalities with no direct WCAG mapping, generally imperceptible
    # to an AT user.
    "5": Severity.MINOR,  # PDF/UA conformance identifier (XMP) — declarative only
    "6.1": Severity.MINOR,  # File header format
    "7.10": Severity.MINOR,  # Optional content config
    "7.11": Severity.MINOR,  # Embedded file spec keys
    "7.15": Severity.MINOR,  # Forms (XFA prohibition)
    "7.16": Severity.MINOR,  # Encryption permission bits
    "7.18.2": Severity.MINOR,  # TrapNet annotations (print-production artifact)
    "7.18.6": Severity.MINOR,  # Media clip CT/Alt keys (rare multimedia feature)
    "7.18.8": Severity.MINOR,  # PrinterMark annotations (print-production artifact)
    "7.20": Severity.MINOR,  # Form XObjects (internal structural technicality)
    "7.21": Severity.MINOR,  # Fonts (embedding, CIDSet, ToUnicode/glyph mapping)
}


def classify(clause: str, test_number: str) -> Severity:
    """Maps one failed veraPDF rule to a severity tier.

    Checks the exact `"{clause}-{test_number}"` override first (needed for 7.1/7.2, whose
    individual rules span every tier), then falls back to a longest dot-bounded prefix match
    against `CLAUSE_SEVERITY` — `"7.21.7"` matches `"7.21"`, not `"7.2"`, and `"7.10"` never
    matches `"7.1"` (the dot boundary keeps clause 7.1 and 7.10 distinct). Falls back to
    `Severity.UNCLASSIFIED` for anything not yet reviewed, rather than silently guessing.
    """
    exact = RULE_SEVERITY.get(f"{clause}-{test_number}")
    if exact is not None:
        return exact

    best_match = ""
    for known_clause in CLAUSE_SEVERITY:
        matches = clause == known_clause or clause.startswith(f"{known_clause}.")
        if matches and len(known_clause) > len(best_match):
            best_match = known_clause

    if not best_match:
        return Severity.UNCLASSIFIED
    return CLAUSE_SEVERITY[best_match]
