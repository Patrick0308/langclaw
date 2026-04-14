"""
Test thread context filtering: bot messages and duplicates.
"""

from __future__ import annotations

import pytest

from langclaw.config.schema import SlackChannelConfig
from langclaw.gateway.slack import SlackChannel


@pytest.fixture
def slack_config():
    """Minimal Slack channel config."""
    return SlackChannelConfig(
        enabled=True,
        token="xoxb-test-token",
        app_token="xapp-test-token",
        thread_history_enabled=True,
        thread_history_max_messages=10,
    )


@pytest.fixture
def slack_channel(slack_config):
    """SlackChannel instance with mocked app."""
    channel = SlackChannel(slack_config)
    channel._bot_user_id = "U0BOT123"  # Set bot user ID
    channel._user_cache = {
        "U0USER1": "alice",
        "U0USER2": "bob",
        "U0BOT123": "testbot",
    }
    return channel


def test_build_thread_context_filters_bot_messages(slack_channel):
    """Test that bot messages are filtered out."""
    raw_messages = [
        {"user": "U0USER1", "text": "Hello", "ts": "1"},
        {"bot_id": "B123", "text": "Bot response 1", "ts": "2"},  # Has bot_id
        {"user": "U0USER2", "text": "Hi there", "ts": "3"},
        {"user": "U0BOT123", "text": "Bot response 2", "ts": "4"},  # Bot user
        {"user": "U0USER1", "text": "Thanks", "ts": "5"},
    ]

    result = slack_channel._build_thread_context_data(raw_messages, "thread123")

    # Should only include user messages, not bot messages
    assert len(result["messages"]) == 3
    assert result["messages"][0]["username"] == "alice"
    assert result["messages"][0]["content"] == "Hello"
    assert result["messages"][1]["username"] == "bob"
    assert result["messages"][1]["content"] == "Hi there"
    assert result["messages"][2]["username"] == "alice"
    assert result["messages"][2]["content"] == "Thanks"
    assert result["total_count"] == 5  # Original count before filtering


def test_build_thread_context_filters_duplicate_content(slack_channel):
    """Test that duplicate messages are filtered out (keeps most recent)."""
    raw_messages = [
        {"user": "U0USER1", "text": "Hello", "ts": "1"},
        {"user": "U0USER2", "text": "Hi", "ts": "2"},
        {"user": "U0USER1", "text": "Hello", "ts": "3"},  # Duplicate
        {"user": "U0USER1", "text": "HELLO", "ts": "4"},  # Duplicate (case-insensitive)
        {"user": "U0USER2", "text": "How are you?", "ts": "5"},
    ]

    result = slack_channel._build_thread_context_data(raw_messages, "thread123")

    # Should filter out duplicates
    assert len(result["messages"]) == 3
    contents = [msg["content"] for msg in result["messages"]]
    assert contents == ["Hello", "Hi", "How are you?"]


def test_build_thread_context_filters_empty_messages(slack_channel):
    """Test that empty messages are filtered out."""
    raw_messages = [
        {"user": "U0USER1", "text": "Hello", "ts": "1"},
        {"user": "U0USER2", "text": "", "ts": "2"},  # Empty
        {"user": "U0USER1", "text": "   ", "ts": "3"},  # Whitespace only
        {"user": "U0USER2", "text": "Hi", "ts": "4"},
    ]

    result = slack_channel._build_thread_context_data(raw_messages, "thread123")

    assert len(result["messages"]) == 2
    assert result["messages"][0]["content"] == "Hello"
    assert result["messages"][1]["content"] == "Hi"


def test_build_thread_context_combined_filtering(slack_channel):
    """Test combined filtering: bot messages, duplicates, and empty."""
    raw_messages = [
        {"user": "U0USER1", "text": "Hello", "ts": "1"},
        {"bot_id": "B123", "text": "Bot response", "ts": "2"},  # Bot - filtered
        {"user": "U0USER2", "text": "Hi", "ts": "3"},
        {"user": "U0USER1", "text": "Hello", "ts": "4"},  # Duplicate - filtered
        {"user": "U0USER2", "text": "", "ts": "5"},  # Empty - filtered
        {"user": "U0BOT123", "text": "Another bot", "ts": "6"},  # Bot - filtered
        {"user": "U0USER1", "text": "Thanks!", "ts": "7"},
    ]

    result = slack_channel._build_thread_context_data(raw_messages, "thread123")

    # Only 3 unique, non-bot, non-empty messages
    assert len(result["messages"]) == 3
    assert result["messages"][0]["username"] == "alice"
    assert result["messages"][0]["content"] == "Hello"
    assert result["messages"][1]["username"] == "bob"
    assert result["messages"][1]["content"] == "Hi"
    assert result["messages"][2]["username"] == "alice"
    assert result["messages"][2]["content"] == "Thanks!"
    assert result["total_count"] == 7


def test_build_thread_context_with_attachments(slack_channel):
    """Test that attachment info is preserved and used in deduplication."""

    def mock_format_attachments(msg):
        if "files" in msg:
            return "[file: test.pdf]"
        return ""

    slack_channel._format_attachments = mock_format_attachments

    raw_messages = [
        {"user": "U0USER1", "text": "Check this", "files": ["file1"], "ts": "1"},
        {
            "user": "U0USER2",
            "text": "Check this",
            "ts": "2",
        },  # Same text but no attachment
        {"user": "U0USER1", "text": "Thanks", "ts": "3"},
    ]

    result = slack_channel._build_thread_context_data(raw_messages, "thread123")

    # Should keep both "Check this" messages because one has attachment
    assert len(result["messages"]) == 3
    assert "Check this [file: test.pdf]" in result["messages"][0]["content"]
    assert result["messages"][1]["content"] == "Check this"
    assert result["messages"][2]["content"] == "Thanks"
