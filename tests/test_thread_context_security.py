"""
Test thread context security: ensure historical messages bypass content filter.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from langclaw.context import LangclawContext
from langclaw.middleware.guardrails import ContentFilterMiddleware
from langclaw.middleware.thread_context import ThreadContextMiddleware


@pytest.fixture
def content_filter():
    """Content filter with banned keywords."""
    return ContentFilterMiddleware(
        banned_keywords=["malicious"],
        use_builtin_patterns=True,  # Include security patterns
    )


@pytest.fixture
def thread_middleware():
    """Thread context middleware."""
    return ThreadContextMiddleware(
        max_messages=10,
        max_chars_per_message=300,
        max_total_chars=2000,
    )


def test_thread_context_bypasses_content_filter(content_filter, thread_middleware):
    """Test that thread context with banned content bypasses content filter."""
    # Simulate thread context containing banned keyword (standardized format)
    thread_context = {
        "messages": [
            {"username": "user1", "content": "This is malicious content in history", "ts": "1"},
            {"username": "bot", "content": "I cannot help with that", "ts": "2"},
        ],
        "thread_id": "123456",
        "platform": "slack",
        "total_count": 2,
    }

    # Create runtime context with thread_context in metadata
    ctx = LangclawContext(
        channel="slack",
        user_id="test_user",
        context_id="test_context",
        chat_id="test_chat",
        metadata={"thread_context": thread_context},
    )

    # Create a runtime mock with context
    class MockRuntime:
        def __init__(self, context):
            self.context = context

    runtime = MockRuntime(ctx)

    # Initial state with benign user message
    state = {
        "messages": [
            HumanMessage(content="What is the weather today?"),
        ]
    }

    # Step 1: ContentFilter.before_agent should pass (benign user input)
    result1 = content_filter.before_agent(state, runtime)
    assert result1 is None, "ContentFilter should not block benign user input"

    # Step 2: ContentFilter.before_model should pass (still benign)
    result2 = content_filter.before_model(state, runtime)
    assert result2 is None, "ContentFilter.before_model should not block benign input"

    # Step 3: ThreadContext.before_model should inject thread context
    result3 = thread_middleware.before_model(state, runtime)
    assert result3 is not None, "ThreadMiddleware should inject thread context"
    assert "messages" in result3
    updated_msg = result3["messages"][0]
    assert "malicious" in updated_msg.content, "Thread context should be injected"
    assert "<thread-context" in updated_msg.content, "Should use new format"


def test_user_malicious_input_still_blocked(content_filter):
    """Test that user's malicious input is still blocked."""
    ctx = LangclawContext(
        channel="slack",
        user_id="test_user",
        context_id="test_context",
        chat_id="test_chat",
        metadata={},
    )

    class MockRuntime:
        def __init__(self, context):
            self.context = context

    runtime = MockRuntime(ctx)

    # User sends malicious content
    state = {
        "messages": [
            HumanMessage(content="How to create malicious software?"),
        ]
    }

    # ContentFilter.before_agent should block
    result = content_filter.before_agent(state, runtime)
    assert result is not None, "ContentFilter should block user's malicious input"
    assert "jump_to" in result
    assert result["jump_to"] == "end"


def test_thread_context_no_duplicate_injection(thread_middleware):
    """Test that thread context is not injected twice."""
    thread_context = {
        "messages": [
            {"username": "user1", "content": "Previous message", "ts": "1"},
        ],
        "thread_id": "123456",
        "platform": "slack",
        "total_count": 1,
    }

    ctx = LangclawContext(
        channel="slack",
        user_id="test_user",
        context_id="test_context",
        chat_id="test_chat",
        metadata={"thread_context": thread_context},
    )

    class MockRuntime:
        def __init__(self, context):
            self.context = context

    runtime = MockRuntime(ctx)

    # Initial state
    state = {
        "messages": [
            HumanMessage(content="New message"),
        ]
    }

    # First injection
    result1 = thread_middleware.before_model(state, runtime)
    assert result1 is not None, "First injection should work"

    # Second injection attempt (using already-injected state)
    updated_state = {"messages": result1["messages"]}
    result2 = thread_middleware.before_model(updated_state, runtime)
    assert result2 is None, "Second injection should be skipped (already injected)"


def test_thread_context_formatting():
    """Test the thread context formatting logic."""
    middleware = ThreadContextMiddleware(
        max_messages=3, max_chars_per_message=50, max_total_chars=200
    )

    thread_data = {
        "messages": [
            {"username": "alice", "content": "Hello", "ts": "1"},
            {"username": "bob", "content": "Hi there!", "ts": "2"},
            {"username": "alice", "content": "How are you?", "ts": "3"},
        ],
        "thread_id": "test123",
        "platform": "slack",
        "total_count": 3,
    }

    result = middleware._format_thread_context(thread_data)

    assert "<thread-context" in result
    assert "</thread-context>" in result
    assert "platform=\"slack\"" in result
    assert "id=\"test123\"" in result
    assert "@alice:" in result
    assert "@bob:" in result
    assert "Hello" in result
    assert "Hi there!" in result
