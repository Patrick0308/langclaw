"""Tests for configurable agent name and config directory."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from langclaw.config import schema


@pytest.fixture(autouse=True)
def _reload_schema_after_test():
    """Reload schema module after each test to reset module-level state."""
    yield
    importlib.reload(schema)


def test_default_agent_name(monkeypatch):
    """Agent name defaults to 'langclaw' when env var not set."""
    # Ensure env var is not set
    monkeypatch.delenv("LANGCLAW__AGENT_NAME", raising=False)
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "langclaw"


def test_custom_agent_name(monkeypatch):
    """Agent name reads from LANGCLAW__AGENT_NAME env var."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "testbot")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "testbot"


def test_empty_agent_name_falls_back(monkeypatch):
    """Empty agent name falls back to default."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "langclaw"


def test_agent_name_with_whitespace(monkeypatch):
    """Agent name strips whitespace."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "  mybot  ")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "mybot"


def test_invalid_agent_name_raises(monkeypatch):
    """Invalid characters in agent name raise ValueError."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "my bot!")

    with pytest.raises(ValidationError, match="must contain only alphanumeric"):
        schema.load_config()


def test_custom_config_dir(monkeypatch, tmp_path):
    """Config directory reads from LANGCLAW__CONFIG_DIR env var."""
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", str(tmp_path))
    importlib.reload(schema)

    config = schema.load_config()
    assert config.config_dir == tmp_path


def test_config_dir_with_tilde(monkeypatch, tmp_path):
    """Config directory expands tilde."""
    # This test assumes HOME is set
    home = Path.home()
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", "~/.test_langclaw")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.config_dir == home / ".test_langclaw"


def test_rabbitmq_uses_agent_name(monkeypatch, tmp_path):
    """RabbitMQ bus config uses agent name from env var."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "mybot")
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", str(tmp_path))
    importlib.reload(schema)

    config = schema.load_config()
    assert config.bus.rabbitmq.queue_name == "mybot.inbound"
    assert config.bus.rabbitmq.exchange_name == "mybot"


def test_kafka_uses_agent_name(monkeypatch, tmp_path):
    """Kafka bus config uses agent name from env var."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "mybot")
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", str(tmp_path))
    importlib.reload(schema)

    config = schema.load_config()
    assert config.bus.kafka.topic == "mybot.inbound"
    assert config.bus.kafka.group_id == "mybot"


def test_default_bus_names(tmp_path):
    """Bus configs default to 'langclaw' when env var not set."""
    # Ensure env var is not set
    old_agent = os.environ.pop("LANGCLAW__AGENT_NAME", None)
    old_config = os.environ.get("LANGCLAW__CONFIG_DIR")
    os.environ["LANGCLAW__CONFIG_DIR"] = str(tmp_path)
    try:
        importlib.reload(schema)
        config = schema.load_config()

        assert config.bus.rabbitmq.queue_name == "langclaw.inbound"
        assert config.bus.rabbitmq.exchange_name == "langclaw"
        assert config.bus.kafka.topic == "langclaw.inbound"
        assert config.bus.kafka.group_id == "langclaw"
    finally:
        if old_agent:
            os.environ["LANGCLAW__AGENT_NAME"] = old_agent
        if old_config:
            os.environ["LANGCLAW__CONFIG_DIR"] = old_config
        else:
            os.environ.pop("LANGCLAW__CONFIG_DIR", None)
        importlib.reload(schema)


def test_agent_builder_uses_config_name(monkeypatch, tmp_path):
    """Agent builder passes config.agent_name to deepagents."""
    from langclaw.agents.builder import create_claw_agent

    # Set custom agent name and config dir
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "testbot")
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", str(tmp_path))
    importlib.reload(schema)

    config = schema.load_config()

    # Create workspace files
    workspace = config.agents.workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("# Test Agent")
    (workspace / "skills").mkdir(exist_ok=True)

    # Build agent
    agent = create_claw_agent(config)

    # Verify agent name
    assert agent.name == "testbot"
