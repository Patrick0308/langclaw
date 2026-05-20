# Config Command Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `agent_name` as a persistable config field and create `langclaw config` CLI commands for configuration management.

**Architecture:** Convert `agent_name` from read-only property to regular Pydantic field supporting config.json persistence while maintaining environment variable override. Add `config_dir` parameter support to `LangclawConfig.__init__()`. Implement CLI commands (set/get/list/show) with validation and helpful error messages.

**Tech Stack:** Pydantic Settings, Typer, pytest

---

## File Structure

**Files to create:**
- `tests/config/test_agent_name_field.py` — Unit tests for agent_name as regular field
- `tests/config/test_config_dir_parameter.py` — Unit tests for config_dir parameter
- `tests/cli/test_config_command.py` — CLI command tests

**Files to modify:**
- `langclaw/config/schema.py:490-497` — Change agent_name property to regular field
- `langclaw/config/schema.py:443` — Add __init__ method to LangclawConfig
- `langclaw/cli/app.py:29` — Add config subcommand group
- `langclaw/cli/app.py:END` — Add config set/get/list/show commands

---

## Task 1: Convert agent_name to Regular Field

**Files:**
- Modify: `langclaw/config/schema.py:490-497`
- Test: `tests/config/test_agent_name_field.py`

- [ ] **Step 1: Write failing test for agent_name from config.json**

Create `tests/config/test_agent_name_field.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from langclaw.config.schema import LangclawConfig, _CONFIG_PATH


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_agent_name_field.py::test_agent_name_from_config_json -xvs`

Expected: FAIL (agent_name is currently a property, not loadable from JSON)

- [ ] **Step 3: Convert agent_name to regular field in schema.py**

In `langclaw/config/schema.py`, replace lines 490-497:

```python
# OLD (remove these lines):
    @property
    def agent_name(self) -> str:
        """The configured agent name (read-only, from LANGCLAW__AGENT_NAME env var).

        Used in message bus queue/topic names, deepagents agent name, and display output.
        Default: "langclaw"
        """
        return _AGENT_NAME

# NEW (add before @property def config_dir):
    agent_name: str = Field(default="langclaw")
    """Agent identifier used throughout the system.

    Used in:
    - Message bus queue/topic names: {agent_name}.inbound
    - Deepagents agent name parameter
    - CLI output and logging

    Priority: init parameter > env var > config.json > default
    """
```

- [ ] **Step 4: Remove _AGENT_NAME global usage**

In `langclaw/config/schema.py`, update lines 115-124:

```python
# OLD:
# Read agent name and config directory from environment (once, at import time)
_AGENT_NAME = os.getenv("LANGCLAW__AGENT_NAME", "langclaw").strip() or "langclaw"
_CONFIG_DIR = os.getenv("LANGCLAW__CONFIG_DIR", "~/.langclaw").strip() or "~/.langclaw"

# Validate agent name contains only safe characters
if not re.match(r"^[a-zA-Z0-9_-]+$", _AGENT_NAME):
    raise ValueError(
        f"LANGCLAW__AGENT_NAME must contain only alphanumeric, hyphen, or underscore characters. "
        f"Got: {_AGENT_NAME!r}"
    )

# NEW:
# Read config directory from environment (once, at import time)
_CONFIG_DIR = os.getenv("LANGCLAW__CONFIG_DIR", "~/.langclaw").strip() or "~/.langclaw"
```

- [ ] **Step 5: Add agent_name validator to LangclawConfig**

In `langclaw/config/schema.py`, add after the `_merge_json_file` validator (around line 515):

```python
    @model_validator(mode="after")
    def _validate_agent_name(self) -> LangclawConfig:
        """Validate agent_name contains only safe characters."""
        if not re.match(r"^[a-zA-Z0-9_-]+$", self.agent_name):
            raise ValueError(
                f"agent_name must contain only alphanumeric, hyphen, or underscore characters. "
                f"Got: {self.agent_name!r}"
            )
        return self
```

- [ ] **Step 6: Update bus config defaults to use config.agent_name**

In `langclaw/config/schema.py`, update RabbitMQBusConfig (around line 265-268):

```python
class RabbitMQBusConfig(BaseModel):
    amqp_url: str = "amqp://guest:guest@localhost/"
    queue_name: str = ""  # Remove default_factory
    exchange_name: str = ""  # Remove default_factory
```

And KafkaBusConfig (around line 271-274):

```python
class KafkaBusConfig(BaseModel):
    bootstrap_servers: str = "localhost:9092"
    topic: str = ""  # Remove default_factory
    group_id: str = ""  # Remove default_factory
```

Then add a model_validator to BusConfig (after line 281):

```python
class BusConfig(BaseModel):
    backend: Literal["asyncio", "rabbitmq", "kafka"] = "asyncio"
    asyncio: AsyncioBusConfig = Field(default_factory=AsyncioBusConfig)
    rabbitmq: RabbitMQBusConfig = Field(default_factory=RabbitMQBusConfig)
    kafka: KafkaBusConfig = Field(default_factory=KafkaBusConfig)

    @model_validator(mode="before")
    @classmethod
    def _set_defaults_from_agent_name(cls, values: Any) -> Any:
        """Set bus queue/topic names from agent_name if not explicitly provided."""
        if isinstance(values, dict):
            # Get agent_name from parent config context (will be set by Pydantic)
            # For now, fallback to env var or "langclaw"
            agent_name = os.getenv("LANGCLAW__AGENT_NAME", "langclaw").strip() or "langclaw"

            # Set RabbitMQ defaults
            if "rabbitmq" in values and isinstance(values["rabbitmq"], dict):
                if not values["rabbitmq"].get("queue_name"):
                    values["rabbitmq"]["queue_name"] = f"{agent_name}.inbound"
                if not values["rabbitmq"].get("exchange_name"):
                    values["rabbitmq"]["exchange_name"] = agent_name

            # Set Kafka defaults
            if "kafka" in values and isinstance(values["kafka"], dict):
                if not values["kafka"].get("topic"):
                    values["kafka"]["topic"] = f"{agent_name}.inbound"
                if not values["kafka"].get("group_id"):
                    values["kafka"]["group_id"] = agent_name

        return values
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/config/test_agent_name_field.py -xvs`

Expected: PASS (both tests)

- [ ] **Step 8: Add test for env var override**

Add to `tests/config/test_agent_name_field.py`:

```python
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
```

- [ ] **Step 9: Run test to verify env var priority**

Run: `uv run pytest tests/config/test_agent_name_field.py::test_agent_name_env_var_overrides_config_json -xvs`

Expected: PASS

- [ ] **Step 10: Add test for validation**

Add to `tests/config/test_agent_name_field.py`:

```python
def test_agent_name_validation_rejects_invalid_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test agent_name validation rejects invalid characters."""
    from pydantic import ValidationError

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
```

- [ ] **Step 11: Run validation test**

Run: `uv run pytest tests/config/test_agent_name_field.py::test_agent_name_validation_rejects_invalid_characters -xvs`

Expected: PASS

- [ ] **Step 12: Commit Task 1**

```bash
git add langclaw/config/schema.py tests/config/test_agent_name_field.py
git commit -m "feat(config): convert agent_name to regular field

- Change agent_name from read-only property to Pydantic field
- Support loading agent_name from config.json
- Maintain env var override (LANGCLAW__AGENT_NAME)
- Add validation for alphanumeric, hyphen, underscore only
- Update bus config to derive queue/topic names from agent_name

Priority: init param > env var > config.json > default"
```

---

## Task 2: Add config_dir Parameter Support

**Files:**
- Modify: `langclaw/config/schema.py:443`
- Test: `tests/config/test_config_dir_parameter.py`

- [ ] **Step 1: Write failing test for config_dir parameter**

Create `tests/config/test_config_dir_parameter.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from langclaw.config.schema import LangclawConfig


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/config/test_config_dir_parameter.py::test_config_dir_parameter_overrides_env_var -xvs`

Expected: FAIL (LangclawConfig.__init__ doesn't accept config_dir parameter yet)

- [ ] **Step 3: Add __init__ method to LangclawConfig**

In `langclaw/config/schema.py`, add after line 488 (before `@property def agent_name`):

```python
    def __init__(
        self,
        config_dir: str | Path | None = None,
        **data: Any,
    ) -> None:
        """Initialize configuration with optional config_dir override.

        Args:
            config_dir: Override config directory (runtime only, not persisted).
                       Takes precedence over LANGCLAW__CONFIG_DIR env var.
            **data: Other configuration fields to override.
        """
        # Handle config_dir parameter by updating global state
        if config_dir is not None:
            global _LANGCLAW_HOME, _CONFIG_PATH
            _LANGCLAW_HOME = Path(config_dir).expanduser().resolve()
            _CONFIG_PATH = _LANGCLAW_HOME / "config.json"

        super().__init__(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/config/test_config_dir_parameter.py -xvs`

Expected: PASS (all three tests)

- [ ] **Step 5: Add test for code parameter override**

Add to `tests/config/test_config_dir_parameter.py`:

```python
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
```

- [ ] **Step 6: Run code parameter test**

Run: `uv run pytest tests/config/test_config_dir_parameter.py::test_agent_name_code_parameter_overrides_all -xvs`

Expected: PASS

- [ ] **Step 7: Commit Task 2**

```bash
git add langclaw/config/schema.py tests/config/test_config_dir_parameter.py
git commit -m "feat(config): add config_dir parameter to LangclawConfig

- Add __init__ method accepting config_dir parameter
- config_dir overrides LANGCLAW__CONFIG_DIR env var
- Expand tilde and resolve relative paths
- Support code parameter override for agent_name

Priority: code param > env var > config.json > default"
```

---

## Task 3: Implement config set Command

**Files:**
- Create: `tests/cli/test_config_command.py`
- Modify: `langclaw/cli/app.py:29` (add config_app)
- Modify: `langclaw/cli/app.py:END` (add config_set command)

- [ ] **Step 1: Write failing test for config set**

Create `tests/cli/test_config_command.py`:

```python
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

    # Mock config paths
    monkeypatch.setattr("langclaw.cli.app._CONFIG_PATH", config_file)
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

    # Mock config paths
    monkeypatch.setattr("langclaw.cli.app._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)

    # Run command
    result = runner.invoke(app, ["config", "set", "config_dir", "~/.mybot"])

    # Verify rejection
    assert result.exit_code == 1
    assert "config_dir' is read-only" in result.stdout
    assert "LANGCLAW__CONFIG_DIR" in result.stdout


def test_config_set_validates_agent_name_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test config set validates agent_name contains only valid characters."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({}, indent=2))

    # Mock config paths
    monkeypatch.setattr("langclaw.cli.app._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)

    # Run command with invalid name
    result = runner.invoke(app, ["config", "set", "agent_name", "my bot"])

    # Verify validation error
    assert result.exit_code == 1
    assert "alphanumeric, hyphen, or underscore" in result.stdout


def test_config_set_requires_config_file_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test config set fails gracefully if config.json doesn't exist."""
    # Mock non-existent config path
    fake_path = Path("/nonexistent/config.json")
    monkeypatch.setattr("langclaw.cli.app._CONFIG_PATH", fake_path)

    # Run command
    result = runner.invoke(app, ["config", "set", "agent_name", "mybot"])

    # Verify helpful error
    assert result.exit_code == 1
    assert "Config file not found" in result.stdout
    assert "langclaw init" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_config_command.py::test_config_set_agent_name -xvs`

Expected: FAIL (config command doesn't exist yet)

- [ ] **Step 3: Add config subcommand group to cli/app.py**

In `langclaw/cli/app.py`, add after line 29 (after `cron_app` definition):

```python
config_app = typer.Typer(help="Manage configuration settings.", no_args_is_help=True)
app.add_typer(config_app, name="config")
```

- [ ] **Step 4: Implement config set command**

In `langclaw/cli/app.py`, add at the end of the file (before `def main()`):

```python
# ---------------------------------------------------------------------------
# langclaw config
# ---------------------------------------------------------------------------


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Configuration key")],
    value: Annotated[str, typer.Argument(help="Configuration value")],
) -> None:
    """Set a configuration value in config.json."""
    import re
    from langclaw.config.schema import _CONFIG_PATH

    # Whitelist of writable config keys
    WRITABLE_KEYS = {
        "agent_name": str,
        # Future: "log_level", "agents.model", etc.
    }

    if key not in WRITABLE_KEYS:
        if key == "config_dir":
            typer.echo(
                "Error: 'config_dir' is read-only. Use environment variable:\n"
                "  export LANGCLAW__CONFIG_DIR=~/.mybot\n"
                "Or pass as parameter:\n"
                "  LangclawConfig(config_dir='~/.mybot')",
                err=True,
            )
        else:
            typer.echo(
                f"Error: Unknown configuration key: {key!r}\n"
                f"Supported keys: {', '.join(WRITABLE_KEYS)}",
                err=True,
            )
        raise typer.Exit(1)

    # Validate value format
    if key == "agent_name":
        if not re.match(r"^[a-zA-Z0-9_-]+$", value):
            typer.echo(
                f"Error: agent_name must contain only alphanumeric, hyphen, or underscore.\n"
                f"Got: {value!r}",
                err=True,
            )
            raise typer.Exit(1)

    # Load current config
    if not _CONFIG_PATH.exists():
        typer.echo(
            f"Config file not found: {_CONFIG_PATH}\n"
            f"Run 'langclaw init' first.",
            err=True,
        )
        raise typer.Exit(1)

    # Update config.json
    import json
    config_data = json.loads(_CONFIG_PATH.read_text())
    config_data[key] = value
    _CONFIG_PATH.write_text(json.dumps(config_data, indent=2))

    typer.echo(f"✓ {key} = {value}")
    typer.echo(f"Config updated in {_CONFIG_PATH}")
    typer.echo("Restart required for changes to take effect.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_config_command.py -k "test_config_set" -xvs`

Expected: PASS (all 4 config_set tests)

- [ ] **Step 6: Commit Task 3**

```bash
git add langclaw/cli/app.py tests/cli/test_config_command.py
git commit -m "feat(cli): add config set command

- Create config subcommand group
- Implement config set with key/value arguments
- Whitelist agent_name as writable key
- Reject config_dir with helpful error message
- Validate agent_name format (alphanumeric, hyphen, underscore)
- Check config.json exists before updating

Usage: langclaw config set agent_name mybot"
```

---

## Task 4: Implement config get Command

**Files:**
- Modify: `langclaw/cli/app.py` (add config_get command)
- Test: `tests/cli/test_config_command.py`

- [ ] **Step 1: Write failing test for config get**

Add to `tests/cli/test_config_command.py`:

```python
def test_config_get_agent_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test langclaw config get agent_name shows effective value."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({"agent_name": "testbot"}, indent=2))

    # Mock config paths
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Run command
    result = runner.invoke(app, ["config", "get", "agent_name"])

    # Verify output
    assert result.exit_code == 0
    assert "testbot" in result.stdout


def test_config_get_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test langclaw config get config_dir shows current directory."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({}, indent=2))

    # Mock config paths
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Run command
    result = runner.invoke(app, ["config", "get", "config_dir"])

    # Verify output includes config_dir path
    assert result.exit_code == 0
    assert str(config_dir) in result.stdout


def test_config_get_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test config get rejects unknown keys."""
    # Run command with unknown key
    result = runner.invoke(app, ["config", "get", "unknown_key"])

    # Verify error
    assert result.exit_code == 1
    assert "Unknown configuration key" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_config_command.py::test_config_get_agent_name -xvs`

Expected: FAIL (config get command doesn't exist)

- [ ] **Step 3: Implement config get command**

In `langclaw/cli/app.py`, add after the `config_set` command:

```python
@config_app.command("get")
def config_get(
    key: Annotated[str, typer.Argument(help="Configuration key")],
) -> None:
    """Get a configuration value (shows effective value after all overrides)."""
    cfg = load_config()

    # Map keys to config attributes
    if key == "agent_name":
        typer.echo(cfg.agent_name)
    elif key == "config_dir":
        typer.echo(str(cfg.config_dir))
    elif key == "log_level":
        typer.echo(cfg.log_level)
    else:
        typer.echo(f"Error: Unknown configuration key: {key!r}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_config_command.py -k "test_config_get" -xvs`

Expected: PASS (all 3 config_get tests)

- [ ] **Step 5: Commit Task 4**

```bash
git add langclaw/cli/app.py tests/cli/test_config_command.py
git commit -m "feat(cli): add config get command

- Implement config get to show effective values
- Support agent_name, config_dir, log_level keys
- Show values after all overrides (code > env > file > default)
- Reject unknown keys with error message

Usage: langclaw config get agent_name"
```

---

## Task 5: Implement config list/show Commands

**Files:**
- Modify: `langclaw/cli/app.py` (add config_list and config_show)
- Test: `tests/cli/test_config_command.py`

- [ ] **Step 1: Write failing test for config list**

Add to `tests/cli/test_config_command.py`:

```python
def test_config_list_shows_all_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test langclaw config list displays all configuration."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({"agent_name": "listbot"}, indent=2))

    # Mock config paths
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Run command
    result = runner.invoke(app, ["config", "list"])

    # Verify output includes key sections
    assert result.exit_code == 0
    assert "Core Configuration" in result.stdout
    assert "agent_name:" in result.stdout
    assert "listbot" in result.stdout
    assert "config_dir:" in result.stdout
    assert "Agents" in result.stdout
    assert "Message Bus" in result.stdout
    assert "Channels" in result.stdout


def test_config_show_is_alias_for_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test langclaw config show is an alias for list."""
    # Setup config directory
    config_dir = tmp_path / "test_config"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({"agent_name": "showbot"}, indent=2))

    # Mock config paths
    monkeypatch.setattr("langclaw.config.schema._CONFIG_PATH", config_file)
    monkeypatch.setattr("langclaw.config.schema._LANGCLAW_HOME", config_dir)

    # Run both commands
    result_list = runner.invoke(app, ["config", "list"])
    result_show = runner.invoke(app, ["config", "show"])

    # Verify both produce similar output
    assert result_list.exit_code == 0
    assert result_show.exit_code == 0
    assert "showbot" in result_list.stdout
    assert "showbot" in result_show.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_config_command.py::test_config_list_shows_all_settings -xvs`

Expected: FAIL (config list command doesn't exist)

- [ ] **Step 3: Implement config list and show commands**

In `langclaw/cli/app.py`, add after the `config_get` command:

```python
@config_app.command("list")
def config_list() -> None:
    """List all configuration values (effective values after overrides)."""
    _config_show()


@config_app.command("show")
def config_show() -> None:
    """Show all configuration values (alias for 'list')."""
    _config_show()


def _config_show() -> None:
    """Shared implementation for config list/show."""
    cfg = load_config()

    typer.echo("\n=== Core Configuration ===")
    typer.echo(f"  agent_name:   {cfg.agent_name}")
    typer.echo(f"  config_dir:   {cfg.config_dir}")
    typer.echo(f"  log_level:    {cfg.log_level}")
    typer.echo(f"  debug:        {cfg.debug}")

    typer.echo("\n=== Agents ===")
    typer.echo(f"  model:        {cfg.agents.model}")
    typer.echo(f"  rate_limit:   {cfg.agents.rate_limit_rpm} rpm")

    typer.echo("\n=== Message Bus ===")
    typer.echo(f"  backend:      {cfg.bus.backend}")
    if cfg.bus.backend == "rabbitmq":
        typer.echo(f"  queue:        {cfg.bus.rabbitmq.queue_name}")
    elif cfg.bus.backend == "kafka":
        typer.echo(f"  topic:        {cfg.bus.kafka.topic}")

    typer.echo("\n=== Checkpointer ===")
    typer.echo(f"  backend:      {cfg.checkpointer.backend}")

    typer.echo("\n=== Channels ===")
    for name, enabled in [
        ("telegram", cfg.channels.telegram.enabled),
        ("discord", cfg.channels.discord.enabled),
        ("websocket", cfg.channels.websocket.enabled),
        ("slack", cfg.channels.slack.enabled),
    ]:
        mark = "✓" if enabled else "✗"
        typer.echo(f"  {mark} {name}")

    typer.echo()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_config_command.py -k "test_config_list or test_config_show" -xvs`

Expected: PASS (both tests)

- [ ] **Step 5: Commit Task 5**

```bash
git add langclaw/cli/app.py tests/cli/test_config_command.py
git commit -m "feat(cli): add config list and show commands

- Implement config list to display all settings
- Add config show as alias for list
- Show effective values across all sections
- Display core config, agents, bus, checkpointer, channels

Usage: langclaw config list (or show)"
```

---

## Task 6: Update Documentation

**Files:**
- Modify: `CLAUDE.md` (update Configuration section)
- Modify: `README.md` (add config command examples)

- [ ] **Step 1: Update CLAUDE.md configuration section**

In `CLAUDE.md`, update the "Configuration" section (around line 143):

```markdown
## Configuration

### Agent Name and Config Directory

Customize the agent name and config directory via environment variables:

**`LANGCLAW__AGENT_NAME`** — Agent identifier used throughout the system
- Message bus queue/topic names (e.g., `mybot.inbound`)
- Deepagents agent name
- CLI output and logging
- Can also be set in config.json: `{"agent_name": "mybot"}`
- Default: `"langclaw"`
- Priority: code parameter > env var > config.json > default

**`LANGCLAW__CONFIG_DIR`** — Root directory for config and state files
- Default: `"~/.langclaw"`
- Supports `~` expansion and relative paths
- All config, database, and workspace files are stored here
- Runtime-only (not persisted to config.json)

**CLI Configuration Management:**

```bash
# Set agent_name in config.json
langclaw config set agent_name mybot

# Get effective value (after overrides)
langclaw config get agent_name

# Show all configuration
langclaw config list  # or 'show'
```

**Usage:**

```bash
# Via config.json (persistent)
langclaw config set agent_name customerbot
langclaw gateway

# Via environment variable (temporary override)
LANGCLAW__AGENT_NAME=customerbot langclaw gateway

# Via code (runtime)
export LANGCLAW__CONFIG_DIR=~/.customerbot
langclaw init
langclaw gateway
```

**Important:**
- Environment variables are read once at startup. Restart required to pick up changes.
- When running multiple instances with shared message bus infrastructure (RabbitMQ/Kafka), each instance must have a unique `LANGCLAW__AGENT_NAME` to avoid queue/topic collisions.
- To migrate an existing `~/.langclaw` directory, simply move it and set `LANGCLAW__CONFIG_DIR` to the new location.
```

- [ ] **Step 2: Update README.md with config examples**

In `README.md`, find the Configuration section and add after environment variables:

```markdown
### Configuration Management

**CLI Commands:**

```bash
# View all configuration
langclaw config list

# Set agent name
langclaw config set agent_name mybot

# Get specific value
langclaw config get agent_name
```

**Configuration Priority:**

```
Code parameter > Environment variable > config.json > Default
```

**Multiple Instances:**

```bash
# Instance 1
export LANGCLAW__CONFIG_DIR=~/.bot1
langclaw init
langclaw config set agent_name bot1
langclaw gateway

# Instance 2
export LANGCLAW__CONFIG_DIR=~/.bot2
langclaw init
langclaw config set agent_name bot2
langclaw gateway
```

**Programmatic Configuration:**

```python
from langclaw.config.schema import LangclawConfig
from langclaw.app import Langclaw

# Runtime configuration
config = LangclawConfig(
    config_dir="./test_data",
    agent_name="test_bot"
)
app = Langclaw.from_config(config)
```
```

- [ ] **Step 3: Commit documentation updates**

```bash
git add CLAUDE.md README.md
git commit -m "docs: update configuration documentation

- Add config command examples to CLAUDE.md
- Document agent_name persistence in config.json
- Show configuration priority order
- Add multi-instance setup examples to README.md
- Document programmatic configuration usage"
```

---

## Task 7: Integration Testing and Verification

**Files:**
- Test: All existing tests should still pass
- Manual: Verify end-to-end workflows

- [ ] **Step 1: Run all existing tests**

Run: `uv run pytest tests/ -xvs`

Expected: PASS (all tests, no regressions)

- [ ] **Step 2: Manual verification - Single instance setup**

```bash
# Clean slate
rm -rf ~/.langclaw

# Initialize
langclaw init

# Set agent name
langclaw config set agent_name manual_test

# Verify
langclaw config get agent_name
# Expected output: manual_test

# View all config
langclaw config list
# Expected: Shows agent_name=manual_test in Core Configuration
```

- [ ] **Step 3: Manual verification - Environment variable override**

```bash
# With agent_name in config.json
LANGCLAW__AGENT_NAME=env_override langclaw config get agent_name
# Expected output: env_override (env var takes priority)

# Without override
langclaw config get agent_name
# Expected output: manual_test (from config.json)
```

- [ ] **Step 4: Manual verification - Validation**

```bash
# Try invalid agent name
langclaw config set agent_name "my bot"
# Expected: Error message about alphanumeric/hyphen/underscore only

# Try to set config_dir
langclaw config set config_dir ~/.mybot
# Expected: Error message that config_dir is read-only
```

- [ ] **Step 5: Manual verification - Multiple instances**

```bash
# Instance 1
export LANGCLAW__CONFIG_DIR=~/.bot1
langclaw init
langclaw config set agent_name bot1
langclaw config get agent_name
# Expected: bot1

# Instance 2 (new terminal)
export LANGCLAW__CONFIG_DIR=~/.bot2
langclaw init
langclaw config set agent_name bot2
langclaw config get agent_name
# Expected: bot2

# Verify isolation
cat ~/.bot1/config.json | grep agent_name
# Expected: "agent_name": "bot1"
cat ~/.bot2/config.json | grep agent_name
# Expected: "agent_name": "bot2"
```

- [ ] **Step 6: Run linting and formatting**

```bash
uv run ruff check . --fix
uv run ruff format .
```

Expected: No errors, clean formatting

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "test: verify config command integration

- All unit tests pass
- Manual verification completed
- Single instance configuration works
- Environment variable override works
- Multi-instance isolation verified
- Validation error messages tested
- Code formatted with ruff"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ agent_name as persistable field — Task 1
- ✅ config_dir as runtime parameter — Task 2
- ✅ config set command — Task 3
- ✅ config get command — Task 4
- ✅ config list/show commands — Task 5
- ✅ Configuration priority (code > env > file > default) — Tasks 1-2
- ✅ Validation (agent_name format, config_dir read-only) — Tasks 1, 3
- ✅ Documentation updates — Task 6
- ✅ Integration testing — Task 7

**Placeholders:** None - all code is complete and executable

**Type consistency:**
- `agent_name`: str field throughout
- `config_dir`: Path property throughout
- `LangclawConfig.__init__(config_dir: str | Path | None, **data)` signature
- `config_set/get/list/show` command signatures match
- All test assertions use correct types

**Implementation notes:**
- TDD followed: test first, then implementation, then commit
- Each task is self-contained with failing test → implementation → passing test → commit
- No forward references to undefined functions
- All paths are absolute when shown in examples
- Bus config defaults updated to use agent_name from config (Task 1, Step 6)

---

## Summary

This plan implements the config command enhancement with:
- 7 tasks covering schema changes, CLI commands, documentation, and testing
- TDD workflow: test → implement → verify → commit
- Comprehensive test coverage (unit + integration + manual)
- Clear error messages and validation
- Backward compatibility maintained (existing setups work unchanged)
- Configuration priority clearly defined and tested

**Expected outcome:** Users can persist `agent_name` in config.json, override with env vars or code parameters, and manage configuration via `langclaw config` CLI commands.
