"""Tests for /claude run oneshot query command."""

from __future__ import annotations

import pytest

from langclaw.gateway.commands import CommandContext, CommandRouter
from langclaw.session.manager import SessionManager


@pytest.mark.asyncio
async def test_claude_run_command() -> None:
    """Test /claude run requires gateway manager."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["run", "what", "is", "the", "weather"],
    )

    result = await router.dispatch("claude", ctx)

    # Should return error when gateway not initialized
    assert "not available" in result


@pytest.mark.asyncio
async def test_claude_run_no_query() -> None:
    """Test /claude run without query returns usage."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["run"],
    )

    result = await router.dispatch("claude", ctx)

    # Should return usage message
    assert "Usage: /claude run <query>" in result


@pytest.mark.asyncio
async def test_claude_help_includes_run() -> None:
    """Test /claude help includes run command."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=[],
    )

    result = await router.dispatch("claude", ctx)

    # Should include run command in help
    assert "/claude run" in result
    assert "one-shot" in result.lower() or "auto-exit" in result.lower()
