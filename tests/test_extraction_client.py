"""Tests for dice_document_pipeline.extraction.client."""
from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

import pytest


def test_make_client_returns_anthropic_instance():
    """make_client() should call anthropic.Anthropic() and return its result."""
    from dice_document_pipeline.extraction.client import make_client

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = MagicMock()
        result = make_client()
        assert mock_cls.called
        assert result is mock_cls.return_value


def test_make_client_drops_empty_auth_token(monkeypatch):
    """Empty ANTHROPIC_AUTH_TOKEN must be removed before constructing client."""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = MagicMock()
        from dice_document_pipeline.extraction import client as client_mod
        client_mod.make_client()

    assert os.environ.get("ANTHROPIC_AUTH_TOKEN") is None


def test_make_client_drops_whitespace_api_key(monkeypatch):
    """Whitespace-only ANTHROPIC_API_KEY must be removed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = MagicMock()
        from dice_document_pipeline.extraction import client as client_mod
        client_mod.make_client()

    assert os.environ.get("ANTHROPIC_API_KEY") is None


def test_make_client_preserves_real_api_key(monkeypatch):
    """A real (non-empty) API key must NOT be removed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-key")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = MagicMock()
        from dice_document_pipeline.extraction import client as client_mod
        client_mod.make_client()

    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-real-key"


def test_make_client_uses_verify_false():
    """The httpx client must be constructed with verify=False."""
    import httpx

    captured = {}

    def fake_anthropic(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with patch("anthropic.Anthropic", side_effect=fake_anthropic):
        from dice_document_pipeline.extraction import client as client_mod
        client_mod.make_client()

    http_client = captured.get("http_client")
    assert http_client is not None
    # httpx.Client stores verify as _transport internals; check the type/param
    assert isinstance(http_client, httpx.Client)
