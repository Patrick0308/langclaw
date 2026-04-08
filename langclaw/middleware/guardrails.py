"""
Guardrail middleware for langclaw.

Provides:
- ContentFilterMiddleware  — deterministic keyword/regex block (before_agent)
- PIIMiddleware            — re-exported from langchain for convenience

Reference: https://docs.langchain.com/oss/python/langchain/guardrails
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from loguru import logger

if TYPE_CHECKING:
    from re import Pattern

# Re-export langchain's built-in PII middleware so callers only need
# to import from langclaw.middleware
try:
    from langchain.agents.middleware import PIIMiddleware  # noqa: F401
except ImportError:
    # Graceful fallback: define a no-op stub so the import never breaks
    class PIIMiddleware(AgentMiddleware):  # type: ignore[no-redef]
        """Stub: install langchain>=1.0 for full PIIMiddleware support."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__()


# ─── Banned Pattern Rules ──────────────────────────────────────────────
# High-confidence security patterns for detecting malicious content.
# Curated to have low false-positive rates while catching common attacks.


class BannedPatternRule:
    """A rule for matching banned content patterns."""

    def __init__(self, rule_id: str, pattern: str, label: str, flags: int = 0) -> None:
        """
        Args:
            rule_id: Unique identifier (kebab-case) for analytics.
            pattern: Regex pattern source.
            label: Human-readable description.
            flags: Optional regex flags (e.g., re.IGNORECASE).
        """
        self.id = rule_id
        self.pattern = pattern
        self.label = label
        self.flags = flags


# Built-in high-confidence patterns for security threats
BUILTIN_BANNED_PATTERNS: list[BannedPatternRule] = [
    # ─── DDoS / Resource Exhaustion ───
    BannedPatternRule(
        "ddos-fork-bomb",
        r":\(\)\{.*:\|:.*\};:",
        "Fork bomb pattern",
    ),
    BannedPatternRule(
        "ddos-stress-command",
        r"\b(stress|stress-ng)\s+--(?:cpu|vm|hdd|io)\s+\d+",
        "Stress testing command (DDoS)",
    ),
    BannedPatternRule(
        "ddos-slowloris",
        r"\bslowloris\b.*(?:-s\s+\d+|--sockets)",
        "Slowloris attack pattern",
        re.IGNORECASE,
    ),
    # ─── Command Injection ───
    BannedPatternRule(
        "injection-shell-chain",
        r"[;|&`$]\s*(?:rm|dd|mkfs|:(){ :|: & };:|nc|bash|sh|curl|wget)\s",
        "Shell command injection chain",
    ),
    BannedPatternRule(
        "injection-eval-exec",
        r"\b(?:eval|exec|system|passthru|shell_exec|popen)\s*\(",
        "Code execution function (eval/exec)",
    ),
    # ─── SQL Injection ───
    BannedPatternRule(
        "sqli-union-select",
        r"\bunion\s+(?:all\s+)?select\b",
        "SQL injection (UNION SELECT)",
        re.IGNORECASE,
    ),
    BannedPatternRule(
        "sqli-or-1-equals-1",
        r"(?:'|\"|;)\s*(?:or|and)\s+[\w`]+\s*=\s*[\w`]+\s*(?:--|#|/\*)",
        "SQL injection (OR 1=1 pattern)",
        re.IGNORECASE,
    ),
    # ─── XSS / Script Injection ───
    BannedPatternRule(
        "xss-script-tag",
        r"<script[^>]*>.*(?:document\.cookie|XMLHttpRequest|fetch\()",
        "XSS script tag with suspicious payload",
        re.IGNORECASE | re.DOTALL,
    ),
    BannedPatternRule(
        "xss-javascript-protocol",
        r"javascript:\s*(?:alert|eval|document\.cookie)",
        "XSS javascript: protocol",
        re.IGNORECASE,
    ),
    # ─── Path Traversal ───
    BannedPatternRule(
        "path-traversal-dotdot",
        r"(?:\.\./){3,}|(?:\.\./.*){2,}(?:/etc/passwd|/etc/shadow|/windows/system32)",
        "Path traversal (../ sequences)",
    ),
    # ─── Cryptomining ───
    BannedPatternRule(
        "cryptominer-command",
        r"\b(?:xmrig|ethminer|cpuminer|ccminer|cgminer)\b.*(?:--url|--user|-o\s+\w+:)",
        "Cryptomining command",
        re.IGNORECASE,
    ),
    # ─── Network Scanning ───
    BannedPatternRule(
        "netscan-nmap",
        r"\bnmap\b.*(?:-s[SUSTAPNFXY]|-p-|--script)",
        "Network scanning (nmap)",
    ),
    BannedPatternRule(
        "netscan-masscan",
        r"\bmasscan\b.*(?:-p|--ports).*(?:--rate|--max-rate)",
        "Mass network scanning (masscan)",
    ),
    # ─── Malware / Backdoor ───
    BannedPatternRule(
        "backdoor-reverse-shell",
        r"\b(?:nc|netcat|ncat)\b.*(?:-e|--exec)\s+(?:/bin/bash|/bin/sh|cmd\.exe)",
        "Reverse shell command",
    ),
    BannedPatternRule(
        "backdoor-php-shell",
        r"<\?php\s+(?:eval|system|passthru)\s*\(\s*\$_(?:GET|POST|REQUEST)",
        "PHP backdoor shell",
        re.IGNORECASE,
    ),
]


class _CompiledPatternCache:
    """Lazy compilation cache for pattern rules."""

    def __init__(self) -> None:
        self._compiled: list[tuple[str, str, Pattern[str]]] | None = None

    def get(self, rules: list[BannedPatternRule]) -> list[tuple[str, str, Pattern[str]]]:
        """
        Returns list of (rule_id, label, compiled_pattern).
        Compiles on first call, then caches.
        """
        if self._compiled is None:
            self._compiled = [
                (rule.id, rule.label, re.compile(rule.pattern, rule.flags))
                for rule in rules
            ]
        return self._compiled


# Global cache instance
_PATTERN_CACHE = _CompiledPatternCache()


class ContentFilterMiddleware(AgentMiddleware):
    """
    Deterministic guardrail: blocks requests matching banned keywords or regex patterns.

    Runs on **every model call** via ``before_model`` hook, ensuring all content
    sent to the LLM is checked — including user input, tool results, and subagent
    messages. Also runs ``before_agent`` for early rejection at the agent boundary.

    On a match:
    - ``before_agent``: short-circuits the agent (``jump_to="end"``)
    - ``before_model``: raises ``ValueError`` to block the model call

    Features:
    - Built-in high-confidence security patterns (DDoS, injection, XSS, etc.)
    - Custom keyword and pattern support
    - Lazy pattern compilation for performance
    - Detailed blocking messages with matched rule info

    Args:
        banned_keywords: Case-insensitive literal strings to block.
        banned_pattern_sources: Regex pattern strings to compile and match.
        use_builtin_patterns: Enable built-in security patterns (default: True).
        block_message: Base reply when request is blocked (rule info is appended).
    """

    def __init__(
        self,
        banned_keywords: list[str] | tuple[str, ...] = (),
        banned_pattern_sources: list[str] | tuple[str, ...] = (),
        use_builtin_patterns: bool = True,
        block_message: str = "I cannot process that request.",
    ) -> None:
        super().__init__()
        self._keywords = [kw.lower() for kw in banned_keywords]
        self._block_message = block_message

        # Custom patterns from user config
        self._custom_patterns: list[Pattern[str]] = [
            re.compile(p) for p in banned_pattern_sources
        ]

        # Built-in rule-based patterns (lazily compiled)
        self._use_builtin = use_builtin_patterns
        self._builtin_rules = BUILTIN_BANNED_PATTERNS if use_builtin_patterns else []

    def _check_content(self, content: str) -> str | None:
        """
        Check content against all filter rules.

        Returns:
            Blocking reason if content violates a rule, None otherwise.
        """
        content_lower = content.lower()

        # 1. Keyword check (case-insensitive)
        for kw in self._keywords:
            if kw in content_lower:
                return f"Matched banned keyword: {kw}"

        # 2. Custom pattern check
        for idx, pattern in enumerate(self._custom_patterns):
            if pattern.search(content):
                return f"Matched custom pattern #{idx + 1}: {pattern.pattern}"

        # 3. Built-in security pattern check
        if self._use_builtin:
            compiled_rules = _PATTERN_CACHE.get(self._builtin_rules)
            for _rule_id, label, pattern in compiled_rules:
                if pattern.search(content):
                    return f"Matched security rule: {label}"

        return None

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Check user input at agent boundary (early rejection)."""
        msgs = state.get("messages", [])
        if not msgs:
            return None

        # Only inspect the latest human message
        human_msg = next(
            (m for m in reversed(msgs) if getattr(m, "type", None) == "human"),
            None,
        )
        if human_msg is None:
            return None

        content_raw = human_msg.content if isinstance(human_msg.content, str) else ""
        reason = self._check_content(content_raw)
        if reason:
            logger.warning(f"Blocked request (before_agent): {reason}")
            return self._block(state, reason=reason)

        return None

    @hook_config(can_jump_to=["end"])
    def before_model(
        self,
        state: AgentState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """
        Check new messages before every model call.

        This hook fires on **every** LLM invocation — main agent, subagents,
        retries, etc. Only the **latest message** is checked to avoid
        re-scanning conversation history.

        On a match, the agent is short-circuited (``jump_to="end"``) — no model call is made.
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        # Only check the latest message (avoid re-scanning history)
        latest_msg = messages[-1]
        content = ""
        if isinstance(latest_msg, dict):
            content = latest_msg.get("content", "")
        elif hasattr(latest_msg, "content"):
            content = latest_msg.content

        if not isinstance(content, str):
            return None

        reason = self._check_content(content)
        if reason:
            logger.warning(f"Blocked model call (before_model): {reason}")
            return self._block(state, reason=reason)

        return None

    def _block(self, state: AgentState, reason: str = "") -> dict[str, Any]:  # noqa: ARG002
        """Block the request and return a rejection message."""
        message = self._block_message
        if reason:
            message = f"{message} ({reason})"

        return {
            "messages": [AIMessage(content=message)],
            "jump_to": "end",
        }


__all__ = ["ContentFilterMiddleware", "PIIMiddleware"]
