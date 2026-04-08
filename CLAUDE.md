# Langclaw Development Guide

Multi-channel AI agent framework built on LangChain, LangGraph, and deepagents.

See @AGENTS.md for package map and code conventions.
See @docs/ARCHITECTURE.md for design rationale.

## Quick Reference

```bash
uv sync --group dev              # Install all deps
uv run pytest tests/ -v          # Run tests
uv run ruff check . --fix        # Lint + auto-fix
uv run ruff format .             # Format code
uv run pre-commit run --all-files  # Full pre-commit suite
```

## Key File Locations

| Task | Primary File(s) |
|------|-----------------|
| Add built-in tool | `langclaw/agents/tools/` + export in `__init__.py` |
| Add channel | `langclaw/gateway/<name>.py` subclassing `BaseChannel` |
| Add middleware | `langclaw/middleware/` + wire in `agents/builder.py` |
| Add message bus | `langclaw/bus/<name>.py` + factory in `bus/__init__.py` |
| Add checkpointer | `langclaw/checkpointer/<name>.py` + factory in `checkpointer/__init__.py` |
| Modify config schema | `langclaw/config/schema.py` (Pydantic Settings) |
| CLI commands | `langclaw/cli/app.py` (Typer) |
| Agent construction | `langclaw/agents/builder.py` |
| Gateway orchestration | `langclaw/gateway/manager.py` |
| Register named agents | `langclaw/app.py` (`app.agent()`) |
| Agent routing logic | `langclaw/gateway/manager.py` (`_resolve_agent_name`) |
| Active agent persistence | `langclaw/session/manager.py` (`get_active_agent` / `set_active_agent`) |

## Extension Patterns

### Adding a Channel

Subclass `BaseChannel` in `langclaw/gateway/base.py`:

```python
class MyChannel(BaseChannel):
    name = "my_channel"

    async def start(self, bus: BaseMessageBus) -> None:
        # Connect and publish InboundMessage to bus
        ...

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        # Deliver AI response to user (required)
        ...

    async def stop(self) -> None:
        # Cleanup resources
        ...

    # Optional overrides:
    # async def send_tool_progress(self, msg) -> None: ...
    # async def send_tool_result(self, msg) -> None: ...
```

Add config in `config/schema.py`, enable in `app.py:_build_all_channels()`.

### Adding a Message Bus

Subclass `BaseMessageBus` in `langclaw/bus/base.py`:

```python
class MyBus(BaseMessageBus):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, msg: InboundMessage) -> None: ...
    def subscribe(self) -> AsyncIterator[InboundMessage]: ...
```

Register in `bus/__init__.py:make_message_bus()` factory.

### Adding Middleware

Create in `langclaw/middleware/`, then add to stack in `agents/builder.py`:

```python
middleware: list[Any] = [
    ChannelContextMiddleware(),      # 1. Inject channel metadata (first)
    # ToolPermissionMiddleware,      # 2. RBAC filtering (if enabled)
    RateLimitMiddleware(...),        # 3. Rate limiting
    ContentFilterMiddleware(...),    # 4. Content filtering (before_agent + before_model)
    PIIMiddleware(...),              # 5. PII redaction
    *(extra_middleware or []),       # 6. User-provided (last)
]
```

Order matters: earlier middleware runs first on input, last on output.

**Content filtering strategy:**
- `ContentFilterMiddleware` uses **two hooks** for defense-in-depth:
  - `before_agent`: Early rejection at agent boundary (user input only)
  - `before_model`: Blocks **every** LLM call (user input, tool results, subagent messages)
- This ensures malicious content cannot reach the LLM even if injected via tool results or multi-turn conversations

### Adding a Checkpointer

Subclass `BaseCheckpointerBackend` in `langclaw/checkpointer/base.py`:

```python
class MyCheckpointer(BaseCheckpointerBackend):
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *_) -> None: ...
    def get(self) -> Checkpointer: ...  # Return LangGraph checkpointer
```

Register in `checkpointer/__init__.py:make_checkpointer_backend()`.

### Named Agents (multi-agent switching)

Register independent named agents on the app. Each gets its own LangGraph thread
(`context_id = "agent:<name>"`) so conversation history never bleeds across agents.

```python
app.agent(
    "researcher",
    description="Deep research with web tools",
    system_prompt="You are a meticulous researcher. Always cite sources.",
    tools=[web_search, web_fetch],          # None → inherits config-driven tools
    model="openai:gpt-4.1",                 # None → inherits default model
)
```

Users interact via the built-in `/agent` command (registered automatically):

```
/agent                          → list all agents with active marker
/agent researcher               → switch to researcher agent (persistent)
/agent default                  → return to main agent
/agent researcher What is X?    → one-off message to researcher (no session change)
```

WebSocket clients can also specify the target agent via metadata:

```json
{
  "type": "message",
  "content": "Summarize the quarterly report",
  "metadata": { "agent_name": "researcher" }
}
```

**`_resolve_agent_name` priority order** (in `gateway/manager.py`):
1. `msg.metadata["agent_name"]` — stamped by cron at schedule time (deterministic, restart-safe)
2. Phase 2 `agent_resolver` hook — auto-routing (not yet implemented; stub in `_resolve_agent_name`)
3. `SessionManager.get_active_agent()` — set by `/agent` (per user, in-memory)
4. `"default"` — fallback

**Cron + named agents:** The cron tool derives `agent_name` from `ctx.context_id`
(set to `"agent:<name>"` when a named agent is active) at schedule time and stamps it
into the job's `fire_kwargs`. On fire, it appears in `InboundMessage.metadata["agent_name"]`
and takes priority over the user's current interactive session. Old persisted jobs without
the field default to `""` and fall through to the next priority level — fully backward compatible.

**Adding Phase 2 auto-routing:** Uncomment the `agent_resolver` stub in
`GatewayManager._resolve_agent_name` and wire a `Callable[[InboundMessage], Awaitable[str | None]]`
through `GatewayManager.__init__` and `Langclaw._run_async`.

## Message Flow

```
User message flow:
Channel → InboundMessage → Bus → GatewayManager._handle()
  → _resolve_agent_name()          # pick agent (metadata > session > default)
  → SessionManager.get_config()    # get/create LangGraph thread
  → active_agent.astream()         # run chosen agent
  → OutboundMessage → Channel

Command flow (bypasses LLM):
Channel → /command → CommandRouter → instant response

Cron flow:
APScheduler → _fire_job() → InboundMessage(origin="cron", metadata={agent_name}) → Bus → same as user flow
```

Key routing fields on `InboundMessage`:
- `origin`: `"user"` | `"cron"` | `"heartbeat"` | `"subagent"`
- `to`: `"agent"` (default) | `"channel"` (bypass agent)
- `metadata["agent_name"]`: explicit agent target (stamped by cron at schedule time)

## Common Pitfalls

### Tool Error Handling

Tools must return error dicts, never raise into the agent:

```python
@app.tool()
async def my_tool(query: str) -> dict:
    try:
        return {"result": do_work(query)}
    except SomeError as e:
        return {"error": str(e)}  # Correct
        # raise  # Wrong — breaks agent loop
```

### Type Annotations

Use modern syntax (Python 3.11+):

```python
# Correct
def foo(items: list[str], value: int | None = None) -> dict[str, Any]: ...

# Wrong — never use typing module equivalents
def foo(items: List[str], value: Optional[int] = None) -> Dict[str, Any]: ...
```

### Logging

Use loguru with f-strings, not stdlib logging:

```python
from loguru import logger

logger.info(f"Processing message from {user_id}")
logger.error(f"Failed to connect: {exc}")
```

### Commands vs Tools

- **Commands** (`/start`, `/reset`, `/help`, `/agent`, `/claude`): Fast system ops, bypass bus and LLM entirely
- **Tools**: LLM-invoked functions, go through full middleware pipeline

Don't implement user-facing quick actions as tools — use `@app.command()`.

`/agent` is registered automatically by `GatewayManager._setup_agent_command()` as a closure
when at least one named agent exists. It calls `SessionManager.set_active_agent()` for persistent
switches and publishes directly to the bus for one-off messages.

### Claude Agent SDK Mode (`/claude`)

The `/claude` command lets users bypass the LangGraph framework and interact directly with Claude
via the **Claude Agent SDK**, which provides:
- Stateful conversations with automatic memory persistence
- Simpler interaction model (no tools, middleware, or LangGraph)
- Ideal for lightweight, conversational use cases

**Installation:**
```bash
uv add langclaw[claude-sdk]
# or
pip install claude-agent-sdk
```

**Usage:**
```
/claude start              → enter Claude SDK mode (persistent session)
/claude workspace show     → show current workspace directory
/claude workspace set ~/path → set workspace directory
<chat normally>            → all messages go to Claude SDK with memory
/claude quit               → exit back to normal agent mode
```

**Workspace Management:**

The workspace directory determines where Claude SDK operates (reads files, executes commands, loads skills):

```bash
# Show current workspace
/claude workspace show

# Set workspace to a specific directory
/claude workspace set ~/my-project

# Set workspace with spaces in path
/claude workspace set ~/My Documents/Project

# Claude SDK will use the new workspace immediately
# The session is automatically restarted with the new configuration
```

**How it works:**
1. `GatewayManager._init_claude_agent()` creates a Claude Agent SDK `Agent` instance at startup
2. `SessionManager.get_or_create_claude_client()` maintains one `ClaudeSDKClient` per (channel, user)
3. When in Claude mode, `_handle_claude_mode()` routes messages to `client.query()`
4. No LangGraph, checkpointers, or middleware — just Claude SDK + persistent memory

The SDK session state lives entirely in-memory (SDK manages its own checkpointing).
When a user exits with `/claude quit`, their session is cleaned up.

**Tool Approval (Optional):**

Enable user approval for tool executions in Claude SDK mode:

```bash
LANGCLAW__AGENTS__CLAUDE_SDK_REQUIRE_APPROVAL=true
```

When enabled:
1. Claude requests approval before executing any tool (e.g., Bash commands)
2. User receives a formatted approval request with tool details
3. User responds with `/approve <request_id>` or `/deny <request_id>`
4. Tool executes only if approved within 5 minutes (otherwise times out)

Example approval request:
```
🔐 **Tool Approval Required**

**Tool:** `Bash`
**Command:** `rm important_file.txt`
**Description:** Delete a file

Reply with:
- `/approve abc-123` to allow
- `/deny abc-123` to block
```

The approval system integrates with the channel layer, so users receive approval
requests and can respond through their normal chat interface (Telegram, Slack, etc.).

## Testing

```bash
uv run pytest tests/ -v                    # All tests
uv run pytest tests/test_gateway.py -v     # Specific module
uv run pytest -k "test_telegram" -v        # Pattern match
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.

## Environment Variables

Config uses `LANGCLAW__` prefix with nested `__` delimiters:

```bash
LANGCLAW__AGENTS__MODEL=openai:gpt-4.1
LANGCLAW__CHANNELS__TELEGRAM__TOKEN=bot123:abc
LANGCLAW__CHANNELS__TELEGRAM__ENABLED=true
LANGCLAW__BUS__BACKEND=rabbitmq
LANGCLAW__CHECKPOINTER__BACKEND=postgres
```
