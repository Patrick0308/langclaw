"""Tests for configurable agent name and config directory."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from langclaw.config import schema


def test_default_agent_name():
    """Agent name defaults to 'langclaw' when env var not set."""
    # Ensure env var is not set
    old_val = os.environ.pop("LANGCLAW__AGENT_NAME", None)
    try:
        importlib.reload(schema)
        config = schema.load_config()
        assert config.agent_name == "langclaw"
    finally:
        if old_val:
            os.environ["LANGCLAW__AGENT_NAME"] = old_val
        importlib.reload(schema)


def test_custom_agent_name(monkeypatch):
    """Agent name reads from LANGCLAW__AGENT_NAME env var."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "testbot")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "testbot"

    # Cleanup
    importlib.reload(schema)


def test_empty_agent_name_falls_back(monkeypatch):
    """Empty agent name falls back to default."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "langclaw"

    # Cleanup
    importlib.reload(schema)


def test_agent_name_with_whitespace(monkeypatch):
    """Agent name strips whitespace."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "  mybot  ")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "mybot"

    # Cleanup
    importlib.reload(schema)


def test_invalid_agent_name_raises(monkeypatch):
    """Invalid characters in agent name raise ValueError."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "my bot!")

    with pytest.raises(ValueError, match="must contain only alphanumeric"):
        importlib.reload(schema)

    # Cleanup: reset to valid state
    monkeypatch.delenv("LANGCLAW__AGENT_NAME")
    importlib.reload(schema)


def test_custom_config_dir(monkeypatch, tmp_path):
    """Config directory reads from LANGCLAW__CONFIG_DIR env var."""
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", str(tmp_path))
    importlib.reload(schema)

    config = schema.load_config()
    assert config.config_dir == tmp_path

    # Cleanup
    importlib.reload(schema)


def test_config_dir_with_tilde(monkeypatch, tmp_path):
    """Config directory expands tilde."""
    # This test assumes HOME is set
    home = Path.home()
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", "~/.test_langclaw")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.config_dir == home / ".test_langclaw"

    # Cleanup
    importlib.reload(schema)
