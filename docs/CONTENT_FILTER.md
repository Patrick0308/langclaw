# Content Filter — Defense-in-Depth Strategy

## Overview

`ContentFilterMiddleware` implements a **two-hook strategy** to ensure malicious content never reaches the LLM:

1. **`before_agent` hook** — Early rejection at the agent boundary (user input only)
2. **`before_model` hook** — Blocks **every** LLM call (user input, tool results, subagent messages, retries)

This defense-in-depth approach prevents malicious content from reaching the LLM even if injected via:
- Tool results (e.g., a compromised web search tool returning malicious content)
- Multi-turn conversations (e.g., user input → tool call → LLM sees tool result)
- Subagent messages (e.g., a subagent generating malicious prompts)

## How It Works

### before_agent Hook
- **When**: Once at the start of each agent invocation
- **What**: Checks only the latest human message
- **Action**: Short-circuits the agent (`jump_to="end"`) with an error message
- **Use case**: Fast rejection of obviously malicious user input

### before_model Hook
- **When**: Before **every** call to the LLM (main agent, subagents, retries)
- **What**: Checks **only the latest message** (avoids re-scanning conversation history)
- **Action**: Short-circuits the agent (`jump_to="end"`) with an error message
- **Use case**: Prevents malicious content in tool results or subsequent user messages
- **Note**: Only the newest message is checked to avoid false positives from historical context

## Configuration

```bash
# Block case-insensitive keywords (checked on ALL messages to LLM)
LANGCLAW__AGENTS__BANNED_KEYWORDS=malware,hack,exploit

# Block regex patterns (checked on ALL messages to LLM)
LANGCLAW__AGENTS__BANNED_PATTERNS='rm\s+-rf\s+/,eval\s*\('

# Enable built-in security patterns (DDoS, injection, XSS, etc.)
LANGCLAW__AGENTS__BANNED_PATTERNS_ENABLED=true
```

Or in code:

```python
from langclaw import Langclaw

app = Langclaw()

# The middleware is automatically added with config values
# No need to manually instantiate it
```

## Built-in Security Patterns

When `banned_patterns_enabled=true`, the filter detects:

- **DDoS / Resource Exhaustion**: Fork bombs, stress commands, slowloris
- **Command Injection**: Shell chains, eval/exec calls
- **SQL Injection**: UNION SELECT, OR 1=1 patterns
- **XSS**: Script tags with suspicious payloads, javascript: protocol
- **Path Traversal**: `../` sequences targeting sensitive files
- **Cryptomining**: XMRig, ethminer, ccminer commands
- **Network Scanning**: nmap, masscan patterns
- **Backdoors**: Reverse shells, PHP shells

See `langclaw/middleware/guardrails.py::BUILTIN_BANNED_PATTERNS` for the full list.

## Example Scenarios

### Scenario 1: User Input Blocked at Agent Boundary

```
User: "How do I run: rm -rf / ?"
→ before_agent hook detects pattern
→ Agent short-circuited with: "I cannot process that request. (Matched custom pattern #1)"
→ LLM never called
```

### Scenario 2: Tool Result Blocked Before Model Call

```
User: "Search the web for XYZ"
→ before_agent: ✓ passes (benign user input)
→ Agent calls web_search tool
→ Tool returns malicious content: "<script>alert(document.cookie)</script>"
→ before_model hook detects XSS pattern in tool result
→ Agent short-circuited with: "I cannot process that request. (Matched security rule: XSS script tag)"
→ LLM never called
```

### Scenario 3: Subagent Message Blocked

```
User: "Ask the research subagent about security exploits"
→ before_agent: ✓ passes
→ Main agent delegates to research subagent
→ Subagent generates response containing banned keyword "exploit"
→ before_model hook blocks when subagent tries to call LLM
→ Subagent fails gracefully
```

## Testing

Run the test suite to verify all filters work correctly:

```bash
uv run pytest tests/test_content_filter_patterns.py -v
```

All tests validate both hooks:
- `before_agent`: User input rejection
- `before_model`: Message-level filtering across all LLM calls

## Performance Impact

- **Pattern compilation**: Lazy (only once on first use, then cached)
- **Keyword matching**: O(n) where n = number of keywords (case-insensitive substring search)
- **Regex matching**: O(m) where m = number of patterns (pre-compiled)
- **Total overhead**: ~0.1-1ms per message (negligible compared to LLM call latency)

## Best Practices

1. **Start with built-in patterns** (`banned_patterns_enabled=true`)
2. **Add custom keywords** for domain-specific threats
3. **Test false positives** — use `test_benign_content_not_blocked` as a template
4. **Monitor logs** — blocked requests are logged with `logger.warning()`
5. **Update patterns** as new attack vectors emerge

## Limitations

- **Content-based only**: Cannot detect semantic attacks (e.g., "Please ignore previous instructions")
- **Regex performance**: Complex patterns may slow down filtering (keep patterns simple)
- **False positives**: Aggressive patterns may block legitimate requests (tune carefully)

For semantic safety, combine with:
- LLM-native guardrails (e.g., Claude Constitutional AI)
- RBAC via `ToolPermissionMiddleware`
- Rate limiting via `RateLimitMiddleware`
