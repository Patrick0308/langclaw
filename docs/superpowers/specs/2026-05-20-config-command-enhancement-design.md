# Config Command Enhancement: agent_name and config_dir Support

**Date:** 2026-05-20
**Status:** Approved

## Summary

Enhance the Langclaw configuration system to support:
1. `agent_name` as a persistable field in `config.json` (while maintaining environment variable override)
2. `config_dir` as a runtime-only parameter (code or environment variable, not persisted)
3. New `langclaw config` CLI command for managing configuration

## Goals

- Allow `agent_name` to be saved in `config.json` for persistence
- Support `config_dir` as a runtime configuration (code parameter or environment variable)
- Provide a `langclaw config` command for easy configuration management
- Maintain clear configuration priority: code parameter > env var > config.json > default
- Zero breaking changes for existing users

## Non-Goals

- Persisting `config_dir` to `config.json` (creates circular dependency)
- Supporting all config fields through CLI (only safe, commonly-used fields)
- Runtime configuration reload (restart required for changes)
- Migration tooling (users can manually move directories)

## Architecture

### Configuration Priority

**`config_dir` (runtime-only, not persisted):**
```
1. Code parameter:     LangclawConfig(config_dir="~/.mybot")
2. Environment variable: LANGCLAW__CONFIG_DIR
3. Default:            ~/.langclaw
```

**`agent_name` (persistable configuration):**
```
1. Code parameter:     LangclawConfig(agent_name="mybot")
2. Environment variable: LANGCLAW__AGENT_NAME
3. config.json:        {"agent_name": "mybot"}
4. Default:            "langclaw"
```

### Key Design Decisions

**Why `config_dir` is runtime-only:**
- `config_dir` determines where `config.json` is located
- Saving it to `config.json` creates a circular dependency
- Bootstrap via environment variable or code parameter is clearer

**Why `agent_name` can be persisted:**
- Most commonly customized setting
- No circular dependency
- Environment variable override still works for temporary changes

---

## Component Design

### 1. LangclawConfig Class Changes

**File:** `langclaw/config/schema.py`

**Before:**
```python
class LangclawConfig(BaseSettings):
    # ...

    @property
    def agent_name(self) -> str:
        """Read-only property from _AGENT_NAME global"""
        return _AGENT_NAME

    @property
    def config_dir(self) -> Path:
        """Read-only property from _LANGCLAW_HOME global"""
        return _LANGCLAW_HOME
```

**After:**
```python
class LangclawConfig(BaseSettings):
    # agent_name is now a regular field (supports config.json persistence)
    agent_name: str = Field(default="langclaw")
    """
    Agent identifier used throughout the system.

    Used in:
    - Message bus queue/topic names: {agent_name}.inbound
    - Deepagents agent name parameter
    - CLI output and logging

    Priority: init parameter > env var > config.json > default
    """

    # Other fields...
    log_level: str = "WARNING"
    debug: bool = False
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    agents: AgentConfig = Field(default_factory=AgentConfig)
    # ...

    @property
    def config_dir(self) -> Path:
        """The configured root directory (read-only, runtime only).

        Priority: init parameter > LANGCLAW__CONFIG_DIR env var > default
        This value is NOT persisted to config.json.
        """
        return _LANGCLAW_HOME

    def __init__(
        self,
        config_dir: str | Path | None = None,
        **data: Any,
    ):
        """
        Initialize configuration with optional config_dir override.

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

**Validation (already exists in schema.py lines 120-124):**
```python
# agent_name validation
if not re.match(r"^[a-zA-Z0-9_-]+$", agent_name):
    raise ValueError(
        f"agent_name must contain only alphanumeric, hyphen, or underscore. "
        f"Got: {agent_name!r}"
    )
```

---

### 2. CLI Command Implementation

**File:** `langclaw/cli/app.py`

**New subcommand group:**
```python
config_app = typer.Typer(help="Manage configuration settings.", no_args_is_help=True)
app.add_typer(config_app, name="config")
```

**Commands:**

#### `langclaw config set <key> <value>`

Set a configuration value in `config.json`:

```python
@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Configuration key")],
    value: Annotated[str, typer.Argument(help="Configuration value")],
) -> None:
    """Set a configuration value in config.json."""
    asyncio.run(_config_set_async(key, value))

async def _config_set_async(key: str, value: str) -> None:
    from langclaw.config.schema import _CONFIG_PATH, load_config

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

#### `langclaw config get <key>`

Read a configuration value:

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

#### `langclaw config list` / `langclaw config show`

Display all effective configuration:

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

---

### 3. Usage Examples

#### Single instance, customizing agent_name

```bash
# Setup
langclaw init
langclaw config set agent_name mybot

# Verify
langclaw config get agent_name
# Output: mybot

# Run
langclaw gateway
# Uses ~/.langclaw/config.json with agent_name=mybot
```

#### Multiple instances with different directories

```bash
# Instance 1
export LANGCLAW__CONFIG_DIR=~/.bot1
langclaw init
langclaw config set agent_name bot1
langclaw gateway

# Instance 2 (separate terminal)
export LANGCLAW__CONFIG_DIR=~/.bot2
langclaw init
langclaw config set agent_name bot2
langclaw gateway
```

#### Programmatic configuration

```python
from langclaw.app import Langclaw
from langclaw.config.schema import LangclawConfig

# Development/testing with isolated config
config = LangclawConfig(
    config_dir="./test_data",
    agent_name="test_bot"
)

app = Langclaw.from_config(config)
app.run()
```

#### Temporary override with environment variable

```bash
# config.json has agent_name=mybot
# Temporarily override for this run
LANGCLAW__AGENT_NAME=test_bot langclaw gateway
```

---

## Data Flow

### Configuration Loading Flow

```
┌─────────────────────────────────────────┐
│ 1. Read LANGCLAW__CONFIG_DIR env var    │
│    or use LangclawConfig(config_dir)    │
│    → Determine config.json location     │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│ 2. Load from {config_dir}/config.json   │
│    Including agent_name field           │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│ 3. Apply environment variable overrides │
│    LANGCLAW__AGENT_NAME=mybot           │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│ 4. Apply code parameter overrides       │
│    LangclawConfig(agent_name="test")    │
└──────────────┬──────────────────────────┘
               ▼
        Final config in effect
```

### CLI config set Flow

```
langclaw config set agent_name mybot
  │
  ├─ 1. Load current config (determine config_dir)
  │
  ├─ 2. Validate key is in whitelist
  │      (agent_name ✓, config_dir ✗)
  │
  ├─ 3. Validate value format
  │      (agent_name: alphanumeric, hyphen, underscore only)
  │
  ├─ 4. Update config.json
  │      {"agent_name": "mybot", ...}
  │
  └─ 5. Prompt user to restart
         "Config updated. Restart required."
```

---

## Error Handling

### Validation Rules

**agent_name validation:**
```python
if not re.match(r'^[a-zA-Z0-9_-]+$', agent_name):
    raise ValueError(
        f"agent_name must contain only alphanumeric, hyphen, or underscore. "
        f"Got: {agent_name!r}"
    )
```

**config_dir validation (runtime):**
```python
if not config_dir.exists():
    config_dir.mkdir(parents=True, exist_ok=True)
if not os.access(config_dir, os.W_OK):
    raise RuntimeError(f"Config directory {config_dir} is not writable")
```

### CLI Error Messages

**Attempting to write config_dir:**
```bash
$ langclaw config set config_dir ~/.mybot
Error: 'config_dir' is read-only. Use environment variable:
  export LANGCLAW__CONFIG_DIR=~/.mybot
Or pass as parameter:
  LangclawConfig(config_dir="~/.mybot")
```

**Invalid agent_name:**
```bash
$ langclaw config set agent_name "my bot"
Error: agent_name must contain only alphanumeric, hyphen, or underscore.
Got: 'my bot'
```

**Unknown configuration key:**
```bash
$ langclaw config set unknown_key value
Error: Unknown configuration key: 'unknown_key'
Supported keys: agent_name
```

**Config file not found:**
```bash
$ langclaw config set agent_name mybot
Config file not found: ~/.langclaw/config.json
Run 'langclaw init' first.
```

---

## File Changes

### Files to Modify

**1. langclaw/config/schema.py**
- Change `agent_name` from property to regular field with `Field(default="langclaw")`
- Add `__init__` method supporting `config_dir` parameter
- Keep `config_dir` as read-only property
- Update validation to support both env var and config.json sources

**2. langclaw/cli/app.py**
- Add `config_app = typer.Typer()`
- Implement `config set <key> <value>`
- Implement `config get <key>`
- Implement `config list` and `config show` (alias)
- Add configuration key whitelist and validation

**3. langclaw/app.py**
- Update `Langclaw.from_env()` to pass through config
- Add `Langclaw.from_config(config: LangclawConfig)` factory method (or accept config in `__init__`)

**4. Documentation**
- Update `CLAUDE.md` configuration section
- Update `README.md` with configuration examples
- Keep `.env.example` comments current (document priority)

---

## Testing Strategy

### Unit Tests

**config/test_schema.py:**
- Test `agent_name` from config.json
- Test `agent_name` env var override
- Test `agent_name` code parameter override
- Test `config_dir` parameter override
- Test validation (invalid agent_name characters)
- Test config loading priority

**cli/test_config_command.py:**
- Test `config set agent_name`
- Test `config set config_dir` (should fail with helpful message)
- Test `config set unknown_key` (should fail)
- Test `config get agent_name`
- Test `config list/show`
- Test validation errors (invalid characters, missing config file)

### Integration Tests

**test_multi_instance.py:**
- Test two instances with different `config_dir` values
- Verify independent `agent_name` configurations
- Verify no cross-instance interference (message bus queues, databases)

---

## Backward Compatibility

### Guarantees

**✅ Existing users unaffected:**
- Default values: `agent_name="langclaw"`, `config_dir=~/.langclaw`
- Environment variable `LANGCLAW__AGENT_NAME` continues to work
- Environment variable `LANGCLAW__CONFIG_DIR` continues to work

**✅ Clear priority order:**
```
Code parameter > Environment variable > config.json > Default
```

**✅ Migration path:**
```bash
# Old approach (still works)
export LANGCLAW__AGENT_NAME=mybot
langclaw gateway

# New approach (more convenient)
langclaw config set agent_name mybot
langclaw gateway

# Both can coexist (env var takes precedence)
```

---

## Future Extensions

This design supports future enhancements:

### Phase 2: More configurable fields

Add to CLI whitelist:
- `log_level` (DEBUG, INFO, WARNING, ERROR)
- `agents.model` (model string)
- `agents.rate_limit_rpm` (integer)

### Phase 3: Nested key support

```bash
langclaw config set agents.model openai:gpt-4
langclaw config set channels.telegram.enabled true
```

Implementation:
- Parse dot-separated paths: `"agents.model"` → `["agents", "model"]`
- Navigate nested dicts in config.json
- Update only the leaf value

### Phase 4: Config profiles

```bash
langclaw config profile create production
langclaw config profile use production
langclaw config set agent_name prod-bot
```

Stores profiles in `~/.langclaw/profiles/production.json`.

---

## Summary

This design provides:
1. **Persistent `agent_name`** — Save in config.json, override with env var or code
2. **Runtime `config_dir`** — Specify via env var or code parameter (avoids circular dependency)
3. **CLI management** — `langclaw config set/get/list` for easy configuration
4. **Clear priority** — Code > Env > File > Default
5. **Backward compatible** — Existing setups continue to work unchanged
6. **Extensible** — Easy to add more configurable fields in the future

The implementation maintains simplicity while solving the core user need: making `agent_name` easy to customize without editing code or managing environment variables.
