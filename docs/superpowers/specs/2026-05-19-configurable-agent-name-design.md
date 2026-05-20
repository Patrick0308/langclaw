# Configurable Agent Name and Config Directory

**Date:** 2026-05-19
**Status:** Approved

## Summary

Make the agent name and config directory configurable via environment variables, allowing users to deploy multiple isolated Langclaw instances or customize the directory location without forking the codebase.

## Goals

- Allow users to customize the agent name used in message bus queue/topic names, deepagents, and display output
- Allow users to customize the config directory location (currently hardcoded to `~/.langclaw`)
- Maintain zero breaking changes for existing users (default values preserve current behavior)
- Support multi-tenant deployments where multiple agents run on shared infrastructure

## Non-Goals

- Runtime changes (requires restart to pick up new environment variable values)
- Per-named-agent override (all named agents within one deployment share the same root name)
- Automatic migration from `~/.langclaw` to custom directories
- Complex validation beyond basic sanity checks

## Architecture & Scope

### Two New Environment Variables

**`LANGCLAW__AGENT_NAME`**
- Controls the agent identifier throughout the system
- Default: `"langclaw"`
- Used in:
  - Message bus queue names: `{agent_name}.inbound`
  - Message bus exchange/topic names: `{agent_name}`
  - Deepagents agent name parameter (line 387 in `agents/builder.py`)
  - CLI output and logging

**`LANGCLAW__CONFIG_DIR`**
- Controls where config and state files are stored
- Default: `"~/.langclaw"`
- Supports `~` expansion and relative paths
- Used to compute:
  - `config.json` path
  - SQLite database paths (state.db, cron.db)
  - Workspace directory (skills, AGENTS.md, memories)
  - Gmail token storage

### Reading Strategy

Environment variables are read **once** at module import time in `config/schema.py`:

1. Use `os.getenv()` with defaults before any config loading
2. Compute all derived paths from the resolved directory
3. No dynamic reloading (restart required for changes)

### Usage Sites

Files that need updates:

1. **`langclaw/config/schema.py`**
   - Read environment variables at module load
   - Update `_LANGCLAW_HOME` computation
   - Update bus config defaults
   - Add read-only properties to `LangclawConfig`

2. **`langclaw/agents/builder.py`** (line 387)
   - Change `name="langclaw"` to `name=config.agent_name`

3. **`langclaw/bus/rabbitmq_bus.py`** (defaults)
   - Use computed `queue_name` and `exchange_name` from config

4. **`langclaw/bus/kafka_bus.py`** (defaults)
   - Use computed `topic` and `group_id` from config

5. **Documentation**
   - Update `CLAUDE.md` with configuration section
   - Update `.env.example` with commented examples
   - Add example in `examples/`

## Implementation Details

### Changes to `config/schema.py`

**At module top (before `_LANGCLAW_HOME` definition):**

```python
import os

# Read agent name and config directory from environment (once, at import time)
_AGENT_NAME = os.getenv("LANGCLAW__AGENT_NAME", "langclaw").strip() or "langclaw"
_CONFIG_DIR = os.getenv("LANGCLAW__CONFIG_DIR", "~/.langclaw").strip() or "~/.langclaw"

# Expand and resolve the config directory
_LANGCLAW_HOME = Path(_CONFIG_DIR).expanduser().resolve()
_CONFIG_PATH = _LANGCLAW_HOME / "config.json"
```

**Update bus config classes:**

```python
class RabbitMQBusConfig(BaseModel):
    amqp_url: str = "amqp://guest:guest@localhost/"
    queue_name: str = Field(default_factory=lambda: f"{_AGENT_NAME}.inbound")
    exchange_name: str = Field(default_factory=lambda: _AGENT_NAME)

class KafkaBusConfig(BaseModel):
    bootstrap_servers: str = "localhost:9092"
    topic: str = Field(default_factory=lambda: f"{_AGENT_NAME}.inbound")
    group_id: str = Field(default_factory=lambda: _AGENT_NAME)
```

**Add properties to `LangclawConfig`:**

```python
class LangclawConfig(BaseSettings):
    # ... existing fields ...

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

### Changes to `agents/builder.py`

**Line 387 (inside `create_deep_agent`):**

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

## Error Handling & Edge Cases

### Invalid Config Directory

If `LANGCLAW__CONFIG_DIR` points to a path that can't be created:
- Add validation in `load_config()` to check directory is writable
- Raise clear error with the problematic path and reason (permission denied, etc.)

```python
def load_config() -> LangclawConfig:
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

### Empty Agent Name

If `LANGCLAW__AGENT_NAME=""`, treat as unset and fall back to "langclaw":

```python
_AGENT_NAME = os.getenv("LANGCLAW__AGENT_NAME", "langclaw").strip() or "langclaw"
```

Optional: Add simple validation for safe characters (alphanumeric + hyphens/underscores):

```python
import re

_AGENT_NAME = os.getenv("LANGCLAW__AGENT_NAME", "langclaw").strip() or "langclaw"
if not re.match(r'^[a-zA-Z0-9_-]+$', _AGENT_NAME):
    raise ValueError(
        f"LANGCLAW__AGENT_NAME must contain only alphanumeric, hyphen, or underscore characters. "
        f"Got: {_AGENT_NAME!r}"
    )
```

### Migration from Hardcoded Paths

**No automatic migration needed** — default values preserve existing behavior.

Users who want to move their data:
```bash
# Move existing directory
mv ~/.langclaw ~/.mybot

# Set environment variable
export LANGCLAW__CONFIG_DIR=~/.mybot
export LANGCLAW__AGENT_NAME=mybot

# Continue using
langclaw gateway
```

Document this in the changelog as an optional manual step.

### Bus Name Conflicts

If multiple deployments share the same RabbitMQ/Kafka instance and use the same agent name, they will collide on queue/topic names.

**Expected behavior:** Each deployment should use a unique `LANGCLAW__AGENT_NAME` for proper isolation.

Document this in `CLAUDE.md`:
> **Note:** When running multiple Langclaw instances that share message bus infrastructure (RabbitMQ/Kafka), each instance must have a unique `LANGCLAW__AGENT_NAME` to avoid queue/topic collisions.

### CLI Commands

**`langclaw init`**
- Must respect `LANGCLAW__CONFIG_DIR` and create files in the configured location
- No code changes needed (already uses `config.agents.workspace_dir` which derives from `_LANGCLAW_HOME`)

**`langclaw status`**
- Should display the active agent name and config directory
- Add to status output:

```python
typer.echo(f"Agent name:      {config.agent_name}")
typer.echo(f"Config directory: {config.config_dir}")
```

## Testing Strategy

### Unit Tests

**`tests/test_config.py`**

Test environment variable reading:

```python
def test_agent_name_from_env(monkeypatch):
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "testbot")
    # Reload config module to pick up new env var
    import importlib
    from langclaw.config import schema
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "testbot"

def test_config_dir_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGCLAW__CONFIG_DIR", str(tmp_path))
    # Reload and verify
    import importlib
    from langclaw.config import schema
    importlib.reload(schema)

    config = schema.load_config()
    assert config.config_dir == tmp_path

def test_empty_agent_name_falls_back():
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "")
    # Reload
    import importlib
    from langclaw.config import schema
    importlib.reload(schema)

    config = schema.load_config()
    assert config.agent_name == "langclaw"  # Falls back to default
```

### Integration Tests

**Bus naming tests:**

```python
def test_rabbitmq_uses_agent_name(monkeypatch):
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "mybot")
    # Reload config
    import importlib
    from langclaw.config import schema
    importlib.reload(schema)

    config = schema.load_config()
    assert config.bus.rabbitmq.queue_name == "mybot.inbound"
    assert config.bus.rabbitmq.exchange_name == "mybot"

def test_kafka_uses_agent_name(monkeypatch):
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "mybot")
    # Reload config
    import importlib
    from langclaw.config import schema
    importlib.reload(schema)

    config = schema.load_config()
    assert config.bus.kafka.topic == "mybot.inbound"
    assert config.bus.kafka.group_id == "mybot"
```

**Agent builder tests:**

```python
def test_create_claw_agent_uses_config_agent_name(monkeypatch):
    monkeypatch.setenv("LANGCLAW__AGENT_NAME", "testbot")
    # Reload config
    import importlib
    from langclaw.config import schema
    importlib.reload(schema)

    config = schema.load_config()
    agent = create_claw_agent(config)

    # Verify deepagents agent has the correct name
    assert agent.name == "testbot"
```

### Manual Testing Checklist

```bash
# Test 1: Default behavior (no env vars)
unset LANGCLAW__AGENT_NAME
unset LANGCLAW__CONFIG_DIR
langclaw init
ls ~/.langclaw/  # Should exist with default structure
langclaw status  # Should show "langclaw" and "~/.langclaw"

# Test 2: Custom agent name
export LANGCLAW__AGENT_NAME=mybot
langclaw gateway
# Check logs — should show "mybot" in output

# Test 3: Custom config dir
export LANGCLAW__CONFIG_DIR=~/.myapp
langclaw init
ls ~/.myapp/  # Should exist with config.json, workspace/, etc.
langclaw status  # Should show custom directory

# Test 4: Multi-tenant setup
export LANGCLAW__AGENT_NAME=bot1
export LANGCLAW__CONFIG_DIR=~/.bot1
langclaw init
langclaw gateway &  # Run in background

export LANGCLAW__AGENT_NAME=bot2
export LANGCLAW__CONFIG_DIR=~/.bot2
langclaw init
langclaw gateway &  # Run second instance

# Both should run without collision
```

## Documentation & Examples

### Update `CLAUDE.md`

Add new section after "Environment Variables":

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

### Update `.env.example`

Add at the top:

```bash
# Agent identity (optional, defaults to "langclaw")
# Used in message bus queue/topic names and display output
# LANGCLAW__AGENT_NAME=mybot

# Config directory (optional, defaults to ~/.langclaw)
# All config, database, and workspace files are stored here
# LANGCLAW__CONFIG_DIR=~/.myapp

# ... rest of existing .env.example ...
```

### Add Example: `examples/custom_agent_name.py`

Create a new example showing multi-tenant setup:

```python
"""
Custom Agent Name Example

Demonstrates how to run multiple Langclaw instances with different agent names
for multi-tenant deployments.

Setup:
    export LANGCLAW__AGENT_NAME=tenant_a
    export LANGCLAW__CONFIG_DIR=~/.langclaw_tenant_a
    python examples/custom_agent_name.py
"""

from langclaw import Langclaw
from langclaw.config.schema import load_config

def main():
    config = load_config()

    # Show the configured agent name
    print(f"Starting agent: {config.agent_name}")
    print(f"Config directory: {config.config_dir}")
    print(f"Bus queue name: {config.bus.rabbitmq.queue_name}")

    app = Langclaw()

    @app.tool()
    def greet(name: str) -> dict:
        """Greet the user by name."""
        return {"message": f"Hello {name} from {config.agent_name}!"}

    app.run()

if __name__ == "__main__":
    main()
```

### Update Changelog

Add to `CHANGELOG.md`:

```markdown
## [Unreleased]

### Added
- Support for `LANGCLAW__AGENT_NAME` environment variable to customize agent name
  used in message bus queue/topic names, deepagents, and display output
- Support for `LANGCLAW__CONFIG_DIR` environment variable to customize config
  directory location (defaults to `~/.langclaw`)
- `config.agent_name` and `config.config_dir` read-only properties for introspection

### Changed
- Message bus queue/topic names now use configured agent name instead of hardcoded "langclaw"
- Deepagents agent name now uses configured value from `config.agent_name`

### Migration Notes
- No breaking changes — defaults preserve existing behavior
- To move existing `~/.langclaw` directory: `mv ~/.langclaw ~/.newpath` and set `LANGCLAW__CONFIG_DIR=~/.newpath`
- Multi-instance deployments sharing message bus infrastructure should use unique `LANGCLAW__AGENT_NAME` values
```

## Risks & Mitigations

### Risk: Users set conflicting agent names in shared infrastructure

**Mitigation:**
- Document clearly in `CLAUDE.md` and examples
- Consider adding a check in `langclaw gateway` startup that logs a warning if RabbitMQ/Kafka is detected and suggests unique naming

### Risk: Invalid characters in agent name break bus systems

**Mitigation:**
- Add simple validation (alphanumeric + hyphens/underscores only)
- Fail fast with clear error message

### Risk: Config directory permissions issues

**Mitigation:**
- Check writability in `load_config()` and fail with actionable error
- Include permission error handling in test suite

## Future Enhancements (Out of Scope)

- Per-named-agent override (e.g., `app.agent("researcher", agent_name="research_bot")`)
- Runtime reload of environment variables without restart
- Automatic migration tool for moving existing installations
- Config validation CLI command (`langclaw config validate`)

## Success Criteria

- [ ] Users can set `LANGCLAW__AGENT_NAME` and see it reflected in bus queue names
- [ ] Users can set `LANGCLAW__CONFIG_DIR` and all files are created in the new location
- [ ] Default behavior unchanged (existing users see no difference)
- [ ] Multiple instances can run with different agent names without collision
- [ ] All tests pass
- [ ] Documentation complete and clear
