"""Example: Using Claude Agent SDK mode in Langclaw.

This example demonstrates how to use the /claude command to switch between
the full LangGraph agent framework and direct Claude SDK conversation mode.

Installation:
    uv add langclaw[claude-sdk]
    # or
    pip install langclaw[claude-sdk]

Usage via chat interface (Telegram, Slack, WebSocket, etc.):
    User: /claude start
    Bot:  Entered Claude direct conversation mode (using Agent SDK).
          You can now chat directly with Claude with persistent memory.
          Use /claude quit to exit this mode.

    User: /claude workspace set ~/my-project
    Bot:  Workspace set to: /Users/username/my-project
          Your Claude SDK session will be restarted with the new workspace.

    User: Hello! What can you help me with?
    Bot:  [Claude responds via Agent SDK - faster, with memory, no tools]

    User: /claude quit
    Bot:  Exited Claude direct conversation mode. Back to normal agent mode.

Workspace Management:
    The workspace directory determines where Claude SDK operates:
    - /claude workspace show - Display current workspace
    - /claude workspace set ~/path - Set workspace directory
    - Workspace paths support ~ expansion and spaces
    - Session is automatically restarted with new workspace

Tool Approval in Claude Mode:
    Enable approval for tool executions by setting:
        LANGCLAW__AGENTS__CLAUDE_SDK_REQUIRE_APPROVAL=true

    When enabled:
    User: Create a test file
    Bot:  🔐 **Tool Approval Required**

          **Tool:** `Bash`
          **Command:** `touch test.txt`
          **Description:** Create a test file

          Reply with:
          - `/approve abc-123` to allow
          - `/deny abc-123` to block

    User: /approve abc-123
    Bot:  ✅ Tool execution approved.
          [Claude executes the command]

When to use Claude mode:
- Quick conversations without needing tools
- Lightweight interactions with persistent memory
- Testing Claude responses without LangGraph overhead
- Contexts where simpler is better (no middleware, RBAC, etc.)

When to use normal agent mode:
- Need access to tools (web search, file operations, etc.)
- Require middleware (rate limiting, content filtering, PII redaction)
- Want full LangGraph features (checkpointers, complex workflows)
- Need RBAC and permission controls
"""

from __future__ import annotations

from langclaw import Langclaw
from langclaw.config.schema import LangclawConfig

# Create Langclaw app instance
app = Langclaw(
    config=LangclawConfig(
        agents={"model": "anthropic:claude-3-5-sonnet-20241022"},
        channels={"websocket": {"enabled": True, "port": 8000}},
    )
)


# Define a simple tool for the main agent
@app.tool()
async def get_weather(city: str) -> dict[str, str]:
    """Get current weather for a city.

    Args:
        city: Name of the city.

    Returns:
        Weather information dictionary.
    """
    # Mock weather data
    return {
        "city": city,
        "temperature": "22°C",
        "condition": "Sunny",
        "humidity": "45%",
    }


if __name__ == "__main__":
    # Run the gateway
    # Users can now use:
    #   - Normal mode: ask questions, use tools like get_weather
    #   - /claude: switch to Claude SDK mode (no tools, just chat)
    #   - /claude quit: return to normal mode with tools
    app.gateway()
