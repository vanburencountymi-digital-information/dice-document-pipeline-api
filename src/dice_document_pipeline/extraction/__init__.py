"""Shared document extraction utilities for the DICE KB pipeline.

This package contains the generalised methodology extracted from the MZM
zoning ordinance pipeline -- the parts that are document-type agnostic and
reusable across every county document type (minutes, resolutions, policies,
ordinances, etc.).

Modules
-------
client    -- Anthropic client factory with DICE-standard Windows fixes
grounding -- Source-grounding verifier: round-trips citations back to the PDF
models    -- Central LLM model name configuration

MZM migration note
------------------
mzm-extraction-pipeline still uses its own local copies of these modules
(scripts/anthropic_client.py, scripts/source_validation.py,
scripts/model_config.py). Once this package is stable and installed as a
dependency in MZM, those imports will be updated to point here. Do not remove
the MZM local copies until that migration is done.
"""

from .client import make_client
from .grounding import (
    GROUNDED,
    NORMALIZED_MATCH,
    MISLOCATED,
    UNGROUNDED,
    NO_QUOTE,
    NO_PAGE,
    PDF_UNAVAILABLE,
    VISION_GROUNDED,
    VISION_UNGROUNDED,
    GROUNDED_VERDICTS,
    UNGROUNDED_VERDICTS,
    GroundingResult,
    normalize_for_search,
    cp1252_safe,
    verify_quote,
    verify_citations,
    correct_pages,
)
from .models import DEFAULT_MODEL, VISION_MODEL, AUDIT_MODEL, EXPERIMENT_MODEL

__all__ = [
    # client
    "make_client",
    # grounding verdicts
    "GROUNDED", "NORMALIZED_MATCH", "MISLOCATED", "UNGROUNDED",
    "NO_QUOTE", "NO_PAGE", "PDF_UNAVAILABLE",
    "VISION_GROUNDED", "VISION_UNGROUNDED",
    "GROUNDED_VERDICTS", "UNGROUNDED_VERDICTS",
    # grounding types & functions
    "GroundingResult",
    "normalize_for_search", "cp1252_safe",
    "verify_quote", "verify_citations", "correct_pages",
    # model names
    "DEFAULT_MODEL", "VISION_MODEL", "AUDIT_MODEL", "EXPERIMENT_MODEL",
]
