from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from langclaw.config.schema import LangclawConfig


def test_agent_name_from_config_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test agent_name loads from config.json."""
    # Setup temporary config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"

    # Write config with agent_name
    config_data = {"agent_name": "testbot"}
    config_file.write_text(json.dumps(config_data, indent=2))

    # Mock the config path
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Load config
    cfg = LangclawConfig()

    # Verify agent_name loaded from file
    assert cfg.agent_name == "testbot"


def test_agent_name_defaults_to_langclaw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test agent_name defaults to 'langclaw' when not in config.json."""
    # Setup temporary config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"

    # Write config without agent_name
    config_data = {"log_level": "INFO"}
    config_file.write_text(json.dumps(config_data, indent=2))

    # Mock the config path
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Load config
    cfg = LangclawConfig()

    # Verify default value
    assert cfg.agent_name == "langclaw"


def test_agent_name_env_var_overrides_config_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test LANGCLAW__AGENT_NAME env var takes priority over config.json."""
    # Setup temporary config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"

    # Write config with agent_name
    config_data = {"agent_name": "filebot"}
    config_file.write_text(json.dumps(config_data, indent=2))

    # Mock the config path
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Set environment variable
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "envbot")

    # Load config
    cfg = LangclawConfig()

    # Verify env var takes priority
    assert cfg.agent_name == "envbot"


def test_agent_name_validation_rejects_invalid_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test agent_name validation rejects invalid characters."""
    # Setup temporary config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"

    # Write config with invalid agent_name
    config_data = {"agent_name": "my bot"}  # Space is invalid
    config_file.write_text(json.dumps(config_data, indent=2))

    # Mock the config path
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Attempt to load config should raise ValidationError
    with pytest.raises(ValidationError, match="alphanumeric, hyphen, or underscore"):
        LangclawConfig()
