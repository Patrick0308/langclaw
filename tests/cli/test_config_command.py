from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from langclaw.cli.app import app

runner = CliRunner()


def test_config_set_agent_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test langclaw config set agent_name writes to config.json."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({"log_level": "INFO"}, indent=2))

    # Mock config paths - only patch in config.schema where it's defined
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Run command
    result = runner.invoke(app, ["config", "set", "agent_name", "mybot"])

    # Verify success
    assert result.exit_code == 0
    assert "agent_name = mybot" in result.stdout
    assert "Config updated" in result.stdout

    # Verify file was updated
    config_data = json.loads(config_file.read_text())
    assert config_data["agent_name"] == "mybot"
    assert config_data["log_level"] == "INFO"  # Preserved


def test_config_set_rejects_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test config set rejects config_dir with helpful message."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({}, indent=2))

    # Mock config paths - only patch in config.schema where it's defined
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)

    # Run command
    result = runner.invoke(app, ["config", "set", "config_dir", "~/.mybot"])

    # Verify rejection
    assert result.exit_code == 1
    assert "config_dir' is read-only" in result.output
    assert "LANGCLAW__CONFIG_DIR" in result.output


def test_config_set_validates_agent_name_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test config set validates agent_name contains only valid characters."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({}, indent=2))

    # Mock config paths - only patch in config.schema where it's defined
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)

    # Run command with invalid name
    result = runner.invoke(app, ["config", "set", "agent_name", "my bot"])

    # Verify validation error
    assert result.exit_code == 1
    assert "alphanumeric, hyphen, or underscore" in result.output


def test_config_set_requires_config_file_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test config set fails gracefully if config.json doesn't exist."""
    # Mock non-existent config path
    fake_path = Path("/nonexistent/config.json")
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", fake_path)

    # Run command
    result = runner.invoke(app, ["config", "set", "agent_name", "mybot"])

    # Verify helpful error
    assert result.exit_code == 1
    assert "Config file not found" in result.output
    assert "langclaw init" in result.output
