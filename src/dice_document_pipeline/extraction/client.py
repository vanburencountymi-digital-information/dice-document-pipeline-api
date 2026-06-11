#!/usr/bin/env python3
"""Centralized Anthropic client factory for the DICE document pipeline.

Lifted from mzm-extraction-pipeline/scripts/anthropic_client.py, which proved
these three fixes are necessary on every Windows machine running county software.
MZM still uses its own local copy until it migrates to import from here.

Three problems this solves:

1. verify=False -- Avast Web Shield intercepts SSL with its own cert that
   Python's certifi does not trust, so certificate verification must be off.

2. Bounded timeout + retries -- without an explicit timeout the SDK applies its
   600s-per-request default, which a half-open/trickling socket (Avast) keeps
   resetting -> effectively infinite (observed: a 2h hang in the MZM challenger
   pass). httpx.Timeout(120, connect=15) + max_retries makes a stalled call fail
   fast and retry with backoff.

3. Empty auth env vars -- the harness/OS environment may inject
   ANTHROPIC_AUTH_TOKEN="" (and ANTHROPIC_API_KEY may be ""). The SDK prefers
   Bearer `auth_token` over the `x-api-key` whenever auth_token is present, and
   an empty value emits an illegal header b"Bearer " -> every request fails with
   a generic APIConnectionError. Dropping empty values lets the SDK fall back to
   the real x-api-key loaded from .env (load_dotenv override=True).
"""
from __future__ import annotations

import os

import anthropic
import httpx
from dotenv import load_dotenv

_DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=15.0)

# Auth env vars the SDK reads at construction time. An empty/whitespace value
# in any of these poisons auth, so we drop them and let the real key win
# from .env.
_AUTH_ENV_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")


def make_client(
    *,
    timeout: httpx.Timeout | None = None,
    max_retries: int = 4,
) -> anthropic.Anthropic:
    """Build an Anthropic client with DICE-standard Windows fixes applied.

    Safe to call from any project on this machine. See module docstring for
    why each fix exists.
    """
    load_dotenv(override=True)
    for var in _AUTH_ENV_VARS:
        val = os.environ.get(var)
        if val is not None and not val.strip():
            os.environ.pop(var, None)
    return anthropic.Anthropic(
        http_client=httpx.Client(verify=False, timeout=timeout or _DEFAULT_TIMEOUT),
        max_retries=max_retries,
    )


__all__ = ["make_client"]
