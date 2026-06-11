#!/usr/bin/env python3
"""Central model name configuration for DICE pipeline LLM calls.

Lifted and generalized from mzm-extraction-pipeline/scripts/model_config.py.
MZM still uses its own local copy (with MZM_MODEL_* env vars) until it migrates
to import from here.

Override any model via environment variable -- useful for testing with a cheaper
model or pinning to a specific version without a code change.

    export DICE_MODEL_DEFAULT=claude-haiku-4-5-20251001
"""
from __future__ import annotations

import os

# Primary model for extraction, grading, and general document tasks.
DEFAULT_MODEL: str = os.getenv("DICE_MODEL_DEFAULT", "claude-sonnet-4-6")

# Vision tasks (page rasterization analysis, alt text, heading detection).
VISION_MODEL: str = os.getenv("DICE_MODEL_VISION", DEFAULT_MODEL)

# Audit / adversarial verification passes.
AUDIT_MODEL: str = os.getenv("DICE_MODEL_AUDIT", DEFAULT_MODEL)

# Experimental / in-development model slot -- override without touching prod.
EXPERIMENT_MODEL: str = os.getenv("DICE_MODEL_EXPERIMENT", DEFAULT_MODEL)

__all__ = ["DEFAULT_MODEL", "VISION_MODEL", "AUDIT_MODEL", "EXPERIMENT_MODEL"]
