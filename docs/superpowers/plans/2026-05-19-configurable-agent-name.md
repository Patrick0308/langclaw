# Configurable Agent Name and Config Directory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to customize agent name and config directory via environment variables

**Architecture:** Read `LANGCLAW__AGENT_NAME` and `LANGCLAW__CONFIG_DIR` environment variables at module import time in `config/schema.py`, use them to compute paths and bus queue names, expose via read-only properties

**Tech Stack:** Python 3.11+, Pydantic Settings, pytest, monkeypatch

---

## File Structure

Files to modify:
- `langclaw/config/schema.py` - Read env vars, update `_LANGCLAW_HOME`, update bus configs, add properties
- `langclaw/agents/builder.py:387` - Change `name="langclaw"` to `name=config.agent_name`
- `langclaw/cli/app.py` - Add agent name and config dir to status output
- `.env.example` - Add commented examples for new env vars
- `CLAUDE.md` - Add configuration section
- `CHANGELOG.md` - Document changes

Files to create:
- `tests/test_config_agent_name.py` - New test file for environment variable reading tests
- `examples/custom_agent_name.py` - Example showing multi-tenant setup

---

### Task 1: Update Config Schema - Read Environment Variables

**Files:**
- Modify: `langclaw/config/schema.py:10-114`

- [ ] **Step 1: Add os import and read environment variables**

At line 12 (after `from pathlib import Path`), add os import and read env vars before `_LANGCLAW_HOME`:

```python
import os
import re
```

Then replace lines 113-114 with:

```python
# ---------------------------------------------------------------------------
# Langclaw home
# ---------------------------------------------------------------------------

# Read agent name and config directory from environment (once, at import time)
_AGENT_NAME = os.getenv("LANGCLAW__AGENT_NAME", "langclaw").strip() or "langclaw"
_CONFIG_DIR = os.getenv("LANGCLAW__CONFIG_DIR", "~/.langclaw").strip() or "~/.langclaw"

# Validate agent name contains only safe characters
if not re.match(r'^[a-zA-Z0-9_-]+$', _AGENT_NAME):
    raise ValueError(
        f"LANGCLAW__AGENT_NAME must contain only alphanumeric, hyphen, or underscore characters. "
        f"Got: {_AGENT_NAME!r}"
    )

# Expand and resolve the config directory
_LANGCLAW_HOME = Path(_CONFIG_DIR).expanduser().resolve()
_CONFIG_PATH = _LANGCLAW_HOME / "config.json"
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import langclaw.config.schema"`

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add langclaw/config/schema.py
git commit -m "feat(config): read LANGCLAW__AGENT_NAME and LANGCLAW__CONFIG_DIR from environment

Read agent name and config directory from environment variables at module import time.
Validate agent name contains only safe characters.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 2: Update Bus Configs to Use Agent Name

**Files:**
- Modify: `langclaw/config/schema.py:211-220`

- [ ] **Step 1: Update RabbitMQBusConfig to use dynamic defaults**

Replace lines 211-214:

```python
class RabbitMQBusConfig(BaseModel):
    amqp_url: str = "amqp://guest:guest@localhost/"
    queue_name: str = Field(default_factory=lambda: f"{_AGENT_NAME}.inbound")
    exchange_name: str = Field(default_factory=lambda: _AGENT_NAME)
```

- [ ] **Step 2: Update KafkaBusConfig to use dynamic defaults**

Replace lines 217-220:

```python
class KafkaBusConfig(BaseModel):
    bootstrap_servers: str = "localhost:9092"
    topic: str = Field(default_factory=lambda: f"{_AGENT_NAME}.inbound")
    group_id: str = Field(default_factory=lambda: _AGENT_NAME)
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "from langclaw.config.schema import load_config; c = load_config(); print(c.bus.rabbitmq.queue_name)"`

Expected: Prints `langclaw.inbound`

- [ ] **Step 4: Commit**

```bash
git add langclaw/config/schema.py
git commit -m "feat(config): use agent name in bus queue/topic names

RabbitMQ and Kafka bus configs now use _AGENT_NAME from environment
for queue/exchange/topic/group names.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Add Properties to LangclawConfig

**Files:**
- Modify: `langclaw/config/schema.py:414` (after `heartbeat: HeartbeatConfig` line)

- [ ] **Step 1: Add agent_name and config_dir properties**

After line 414 (`heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)`), add:

```python

    @property
    def agent_name(self) -> str:
        """The configured agent name (read-only, from LANGCLAW__AGENT_NAME env var).

        Used in message bus queue/topic names, deepagents agent name, and display output.
        Default: "langclaw"
        """
        return _AGENT_NAME

    @property
    def config_dir(self) -> Path:
        """The configured root directory (read-only, from LANGCLAW__CONFIG_DIR env var).

        Default: ~/.langclaw
        """
        return _LANGCLAW_HOME
```

- [ ] **Step 2: Test properties work**

Run: `python -c "from langclaw.config.schema import load_config; c = load_config(); print(c.agent_name, c.config_dir)"`

Expected: Prints `langclaw <path-to-home>/.langclaw`

- [ ] **Step 3: Commit**

```bash
git add langclaw/config/schema.py
git commit -m "feat(config): add agent_name and config_dir properties

Add read-only properties to LangclawConfig for introspection of
configured agent name and config directory.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 4: Add Config Directory Validation to load_config

**Files:**
- Modify: `langclaw/config/schema.py:447-457`

- [ ] **Step 1: Add directory validation**

Replace the `load_config` function (lines 447-457):

```python
def load_config() -> LangclawConfig:
    """Load and return the merged LangclawConfig.

    Also calls ``load_dotenv()`` so that standard provider env vars
    (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, etc.) from ``.env``
    are available in ``os.environ`` for ``init_chat_model``.

    Raises:
        RuntimeError: If config directory cannot be created or is not writable.
    """
    from dotenv import load_dotenv

    load_dotenv(override=False)

    # Ensure config directory is accessible
    if not _LANGCLAW_HOME.exists():
        try:
            _LANGCLAW_HOME.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError(
                f"Cannot create config directory {_LANGCLAW_HOME}: {e}"
            ) from e

    if not os.access(_LANGCLAW_HOME, os.W_OK):
        raise RuntimeError(
            f"Config directory {_LANGCLAW_HOME} is not writable"
        )

    return LangclawConfig()
```

- [ ] **Step 2: Test validation works**

Run: `python -c "from langclaw.config.schema import load_config; load_config()"`

Expected: No errors, directory created if needed

- [ ] **Step 3: Commit**

```bash
git add langclaw/config/schema.py
git commit -m "feat(config): validate config directory in load_config

Ensure config directory can be created and is writable when loading config.
Raise RuntimeError with clear message if validation fails.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 5: Write Tests for Environment Variable Reading

**Files:**
- Create: `tests/test_config_agent_name.py`

- [ ] **Step 1: Create test file with basic env var tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_config_agent_name.py -v`

Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_agent_name.py
git commit -m "test(config): add tests for agent name and config dir env vars

Test environment variable reading, defaults, validation, and whitespace handling.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 6: Write Tests for Bus Config Usage

**Files:**
- Modify: `tests/test_config_agent_name.py` (append)

- [ ] **Step 1: Add bus config tests**

Append to `tests/test_config_agent_name.py`:

```python


def test_rabbitmq_uses_agent_name(monkeypatch):
    """RabbitMQ bus config uses agent name from env var."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "mybot")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.bus.rabbitmq.queue_name == "mybot.inbound"
    assert config.bus.rabbitmq.exchange_name == "mybot"

    # Cleanup
    importlib.reload(schema)


def test_kafka_uses_agent_name(monkeypatch):
    """Kafka bus config uses agent name from env var."""
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "mybot")
    importlib.reload(schema)

    config = schema.load_config()
    assert config.bus.kafka.topic == "mybot.inbound"
    assert config.bus.kafka.group_id == "mybot"

    # Cleanup
    importlib.reload(schema)


def test_default_bus_names():
    """Bus configs default to 'langclaw' when env var not set."""
    # Ensure env var is not set
    old_val = os.environ.pop("LANGCLAW__AGENT_NAME", None)
    try:
        importlib.reload(schema)
        config = schema.load_config()

        assert config.bus.rabbitmq.queue_name == "langclaw.inbound"
        assert config.bus.rabbitmq.exchange_name == "langclaw"
        assert config.bus.kafka.topic == "langclaw.inbound"
        assert config.bus.kafka.group_id == "langclaw"
    finally:
        if old_val:
            os.environ["LANGCLAW__AGENT_NAME"] = old_val
        importlib.reload(schema)
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_config_agent_name.py::test_rabbitmq_uses_agent_name -v`

Expected: Test passes

- [ ] **Step 3: Run all tests**

Run: `pytest tests/test_config_agent_name.py -v`

Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_config_agent_name.py
git commit -m "test(config): add tests for bus queue/topic name configuration

Test that RabbitMQ and Kafka configs use agent name from environment.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 7: Update Agent Builder to Use Config Agent Name

**Files:**
- Modify: `langclaw/agents/builder.py:387`

- [ ] **Step 1: Change hardcoded name to use config**

Find line 387 (inside `create_deep_agent`, the `create_deep_agent(...)` call), change:

```python
    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        skills=skills,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        backend=FilesystemBackend(
            root_dir=str(workspace_dir),
            virtual_mode=True,
        ),
        middleware=middleware,
        context_schema=context_schema,
        subagents=final_subagents,
        name=config.agent_name,  # Changed from hardcoded "langclaw"
    )
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "from langclaw.agents.builder import create_claw_agent"`

Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add langclaw/agents/builder.py
git commit -m "feat(agents): use config.agent_name in deepagents

Pass config.agent_name to deepagents instead of hardcoded 'langclaw'.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 8: Add Test for Agent Builder Name

**Files:**
- Modify: `tests/test_config_agent_name.py` (append)

- [ ] **Step 1: Add agent builder test**

Append to `tests/test_config_agent_name.py`:

```python


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

    # Cleanup
    importlib.reload(schema)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_config_agent_name.py::test_agent_builder_uses_config_name -v`

Expected: Test passes

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_agent_name.py
git commit -m "test(agents): verify agent builder uses config.agent_name

Test that create_claw_agent passes config.agent_name to deepagents.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 9: Update CLI Status Command

**Files:**
- Modify: `langclaw/cli/app.py:377-397`

- [ ] **Step 1: Add agent name and config dir to status output**

After line 382 (`cfg = load_config()`), add:

```python

    typer.echo("\n=== Agent Configuration ===")
    typer.echo(f"  Agent name:       {cfg.agent_name}")
    typer.echo(f"  Config directory: {cfg.config_dir}")
```

- [ ] **Step 2: Test status command**

Run: `python -m langclaw.cli.app status`

Expected: Shows agent name and config directory in output

- [ ] **Step 3: Test with custom values**

Run: `LANGCLAW__AGENT_NAME=mybot LANGCLAW__CONFIG_DIR=/tmp/test python -m langclaw.cli.app status`

Expected: Shows "mybot" and "/tmp/test" in output

- [ ] **Step 4: Commit**

```bash
git add langclaw/cli/app.py
git commit -m "feat(cli): show agent name and config dir in status command

Display configured agent name and config directory in status output.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 10: Update .env.example

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add new environment variables**

At the top of `.env.example` (before existing content), add:

```bash
# Agent identity (optional, defaults to "langclaw")
# Used in message bus queue/topic names and display output
# LANGCLAW__AGENT_NAME=mybot

# Config directory (optional, defaults to ~/.langclaw)
# All config, database, and workspace files are stored here
# LANGCLAW__CONFIG_DIR=~/.myapp

```

- [ ] **Step 2: Verify file is valid**

Run: `cat .env.example | head -10`

Expected: Shows new lines at top

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add LANGCLAW__AGENT_NAME and LANGCLAW__CONFIG_DIR to .env.example

Document new environment variables for agent name and config directory.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 11: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find insertion point**

Run: `grep -n "## Environment Variables" CLAUDE.md`

Expected: Shows line number (around line 80-100)

- [ ] **Step 2: Add configuration section after Environment Variables**

After the "## Environment Variables" section, add:

```markdown

## Configuration

### Agent Name and Config Directory

Customize the agent name and config directory via environment variables:

**`LANGCLAW__AGENT_NAME`** — Agent identifier used throughout the system
- Message bus queue/topic names (e.g., `mybot.inbound`)
- Deepagents agent name
- CLI output and logging
- Default: `"langclaw"`

**`LANGCLAW__CONFIG_DIR`** — Root directory for config and state files
- Default: `"~/.langclaw"`
- Supports `~` expansion and relative paths
- All config, database, and workspace files are stored here

**Usage:**

```bash
export LANGCLAW__AGENT_NAME=customerbot
export LANGCLAW__CONFIG_DIR=~/.customerbot
langclaw init
langclaw gateway
```

**Important:**
- Environment variables are read once at startup. Restart required to pick up changes.
- When running multiple instances with shared message bus infrastructure (RabbitMQ/Kafka), each instance must have a unique `LANGCLAW__AGENT_NAME` to avoid queue/topic collisions.
- To migrate an existing `~/.langclaw` directory, simply move it and set `LANGCLAW__CONFIG_DIR` to the new location.
```

- [ ] **Step 3: Verify markdown is valid**

Run: `grep -A 5 "LANGCLAW__AGENT_NAME" CLAUDE.md`

Expected: Shows new content

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add configuration section for agent name and config directory

Document LANGCLAW__AGENT_NAME and LANGCLAW__CONFIG_DIR environment variables
with usage examples and important notes.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 12: Update CHANGELOG.md

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add changes to unreleased section**

At the top of CHANGELOG.md, under `## [Unreleased]` (or create the section if it doesn't exist), add:

```markdown
## [Unreleased]

### Added
- Support for `LANGCLAW__AGENT_NAME` environment variable to customize agent name
  used in message bus queue/topic names, deepagents, and display output
- Support for `LANGCLAW__CONFIG_DIR` environment variable to customize config
  directory location (defaults to `~/.langclaw`)
- `config.agent_name` and `config.config_dir` read-only properties for introspection
- Config directory validation in `load_config()` ensures directory is writable

### Changed
- Message bus queue/topic names now use configured agent name instead of hardcoded "langclaw"
- Deepagents agent name now uses configured value from `config.agent_name`
- CLI `status` command now displays agent name and config directory

### Migration Notes
- No breaking changes — defaults preserve existing behavior
- To move existing `~/.langclaw` directory: `mv ~/.langclaw ~/.newpath` and set `LANGCLAW__CONFIG_DIR=~/.newpath`
- Multi-instance deployments sharing message bus infrastructure should use unique `LANGCLAW__AGENT_NAME` values
```

- [ ] **Step 2: Verify changelog format**

Run: `head -30 CHANGELOG.md`

Expected: Shows new unreleased section

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: update CHANGELOG with configurable agent name feature

Document new environment variables, changes, and migration notes.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 13: Create Example File

**Files:**
- Create: `examples/custom_agent_name.py`

- [ ] **Step 1: Create example file**

```python
"""
Custom Agent Name Example

Demonstrates how to run multiple Langclaw instances with different agent names
for multi-tenant deployments.

Setup:
    export LANGCLAW__AGENT_NAME=tenant_a
    export LANGCLAW__CONFIG_DIR=~/.langclaw_tenant_a
    python examples/custom_agent_name.py

To run multiple instances:
    # Terminal 1
    export LANGCLAW__AGENT_NAME=tenant_a
    export LANGCLAW__CONFIG_DIR=~/.langclaw_tenant_a
    python examples/custom_agent_name.py

    # Terminal 2
    export LANGCLAW__AGENT_NAME=tenant_b
    export LANGCLAW__CONFIG_DIR=~/.langclaw_tenant_b
    python examples/custom_agent_name.py
"""

from langclaw import Langclaw
from langclaw.config.schema import load_config


def main():
    config = load_config()

    # Show the configured agent name and paths
    print(f"Starting agent: {config.agent_name}")
    print(f"Config directory: {config.config_dir}")
    print(f"RabbitMQ queue: {config.bus.rabbitmq.queue_name}")
    print(f"Kafka topic: {config.bus.kafka.topic}")
    print()

    app = Langclaw()

    @app.tool()
    def greet(name: str) -> dict:
        """Greet the user by name."""
        return {"message": f"Hello {name} from {config.agent_name}!"}

    @app.tool()
    def whoami() -> dict:
        """Show agent identity."""
        return {
            "agent_name": config.agent_name,
            "config_dir": str(config.config_dir),
            "queue_name": config.bus.rabbitmq.queue_name,
        }

    print(f"Agent '{config.agent_name}' is ready!")
    print("Try the /agent command or send a message to test.")
    print()

    app.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test example runs without errors**

Run: `LANGCLAW__AGENT_NAME=test python examples/custom_agent_name.py --help`

Expected: Shows help output (won't start agent, just verify syntax)

- [ ] **Step 3: Commit**

```bash
git add examples/custom_agent_name.py
git commit -m "docs: add custom agent name example

Example demonstrates multi-tenant setup with configurable agent names.

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 14: Run Full Test Suite

**Files:**
- Test: `tests/`

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`

Expected: All tests pass (including new config tests)

- [ ] **Step 2: Run specific config tests**

Run: `pytest tests/test_config_agent_name.py -v`

Expected: All config agent name tests pass

- [ ] **Step 3: Check for any test failures**

If any tests fail, fix them and commit the fixes.

- [ ] **Step 4: Run linter**

Run: `uv run ruff check langclaw/config/schema.py langclaw/agents/builder.py langclaw/cli/app.py tests/test_config_agent_name.py`

Expected: No linting errors

- [ ] **Step 5: Fix any linting errors**

Run: `uv run ruff check . --fix`

Expected: All errors auto-fixed

- [ ] **Step 6: Format code**

Run: `uv run ruff format .`

Expected: All files formatted

- [ ] **Step 7: Commit any formatting changes**

```bash
git add -u
git commit -m "style: apply ruff formatting

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 15: Manual Testing

**Files:**
- Test: Manual testing with different environment variable configurations

- [ ] **Step 1: Test default behavior**

```bash
# Clean env
unset LANGCLAW__AGENT_NAME
unset LANGCLAW__CONFIG_DIR

# Test config loading
python -c "from langclaw.config.schema import load_config; c = load_config(); print(f'Agent: {c.agent_name}, Dir: {c.config_dir}')"
```

Expected output: `Agent: langclaw, Dir: /Users/<you>/.langclaw`

- [ ] **Step 2: Test custom agent name**

```bash
export LANGCLAW__AGENT_NAME=mybot
python -c "from langclaw.config.schema import load_config; c = load_config(); print(f'Agent: {c.agent_name}, Queue: {c.bus.rabbitmq.queue_name}')"
```

Expected output: `Agent: mybot, Queue: mybot.inbound`

- [ ] **Step 3: Test custom config directory**

```bash
export LANGCLAW__CONFIG_DIR=/tmp/test_langclaw
python -c "from langclaw.config.schema import load_config; c = load_config(); print(f'Dir: {c.config_dir}')"
ls /tmp/test_langclaw/
```

Expected: Directory exists

- [ ] **Step 4: Test invalid agent name**

```bash
export LANGCLAW__AGENT_NAME="my bot!"
python -c "from langclaw.config.schema import load_config"
```

Expected: ValueError about invalid characters

- [ ] **Step 5: Test CLI status command**

```bash
export LANGCLAW__AGENT_NAME=testbot
export LANGCLAW__CONFIG_DIR=/tmp/test_langclaw
langclaw status
```

Expected: Shows "testbot" and "/tmp/test_langclaw" in output

- [ ] **Step 6: Document manual test results**

Create a simple test report showing the results of manual testing.

---

## Verification Checklist

Before marking implementation complete, verify:

- [ ] All tests pass: `pytest tests/ -v`
- [ ] No linting errors: `uv run ruff check .`
- [ ] Code formatted: `uv run ruff format .`
- [ ] Documentation updated (CLAUDE.md, CHANGELOG.md, .env.example)
- [ ] Example file created and tested
- [ ] Manual testing completed successfully
- [ ] Config schema reads env vars correctly
- [ ] Bus configs use agent name
- [ ] Agent builder uses config.agent_name
- [ ] CLI status shows agent info
- [ ] Default behavior unchanged (backwards compatible)

## Success Criteria

- [x] Users can set `LANGCLAW__AGENT_NAME` and see it reflected in bus queue names
- [x] Users can set `LANGCLAW__CONFIG_DIR` and all files are created in the new location
- [x] Default behavior unchanged (existing users see no difference)
- [x] Multiple instances can run with different agent names without collision
- [x] All tests pass
- [x] Documentation complete and clear
