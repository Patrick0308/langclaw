from __future__ import annotations

import json
from pathlib import Path

import pytest

from langclaw.config.schema import LangclawConfig


@pytest.fixture(autouse=True)
def restore_config_globals():
    """Restore config globals after each test to prevent pollution."""
    import langclaw.config.schema as schema_module

    original_home = schema_module._LANGCLAW_HOME
    original_path = schema_module._CONFIG_PATH

    yield

    # Restore after test completes
    schema_module._LANGCLAW_HOME = original_home
    schema_module._CONFIG_PATH = original_path


def test_config_dir_parameter_overrides_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test config_dir parameter takes priority over LANGCLAW__CONFIG_DIR."""
    # Setup two config directories
    env_config_dir = tmp_path / "env_config"
    env_config_dir.mkdir()
    env_config_file = env_config_dir / "config.json"
    env_config_file.write_text(json.dumps({"agent_name": "envbot"}, indent=2))

    param_config_dir = tmp_path / "param_config"
    param_config_dir.mkdir()
    param_config_file = param_config_dir / "config.json"
    param_config_file.write_text(json.dumps({"agent_name": "parambot"}, indent=2))

    # Set environment variable
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", str(env_config_dir))

    # Load config with parameter (should override env var)
    cfg = LangclawConfig(config_dir=param_config_dir)

    # Verify parameter took priority
    assert cfg.config_dir == param_config_dir
    assert cfg.agent_name == "parambot"


def test_config_dir_parameter_expands_tilde(tmp_path: Path) -> None:
    """Test config_dir parameter expands ~ to user home directory."""
    # Use actual home directory expansion
    cfg = LangclawConfig(config_dir="~")

    # Verify tilde was expanded
    assert "~" not in str(cfg.config_dir)
    assert cfg.config_dir.is_absolute()


def test_config_dir_parameter_resolves_relative_paths(tmp_path: Path) -> None:
    """Test config_dir parameter resolves relative paths to absolute."""
    # Create a relative path config
    relative_path = "./test_config"

    cfg = LangclawConfig(config_dir=relative_path)

    # Verify path is absolute
    assert cfg.config_dir.is_absolute()
    assert str(cfg.config_dir).endswith("test_config")


def test_agent_name_code_parameter_overrides_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test agent_name code parameter takes highest priority."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({"agent_name": "filebot"}, indent=2))

    # Set environment variable
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "envbot")
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", str(config_dir))

    # Load config with code parameter (highest priority)
    cfg = LangclawConfig(agent_name="codebot")

    # Verify code parameter overrides everything
    assert cfg.agent_name == "codebot"
