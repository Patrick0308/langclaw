"""Tests for /claude command behavior."""

from __future__ import annotations

import pytest

from langclaw.gateway.commands import CommandContext, CommandRouter
from langclaw.session.manager import SessionManager


@pytest.mark.asyncio
async def test_claude_command_usage() -> None:
    """Test /claude command shows usage when called without arguments."""
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
    assert "Usage:" in result
    assert "/claude start" in result
    assert "/claude quit" in result


@pytest.mark.asyncio
async def test_claude_command_start() -> None:
    """Test /claude start enters Claude mode."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["start"],
    )

    result = await router.dispatch("claude", ctx)
    assert "Entered Claude direct conversation mode" in result

    # Verify mode is enabled
    is_enabled = await session_mgr.get_claude_mode("test", "user1")
    assert is_enabled is True


@pytest.mark.asyncio
async def test_claude_command_quit() -> None:
    """Test /claude quit exits Claude mode."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    # First enable mode
    await session_mgr.set_claude_mode("test", "user1", True)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["quit"],
    )

    result = await router.dispatch("claude", ctx)
    assert "Exited Claude direct conversation mode" in result

    # Verify mode is disabled
    is_enabled = await session_mgr.get_claude_mode("test", "user1")
    assert is_enabled is False


@pytest.mark.asyncio
async def test_claude_command_status_when_active() -> None:
    """Test /claude shows status when mode is already active."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    # Enable mode first
    await session_mgr.set_claude_mode("test", "user1", True)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=[],
    )

    result = await router.dispatch("claude", ctx)
    assert "Claude mode is currently active" in result
    assert "/claude quit" in result


@pytest.mark.asyncio
async def test_claude_command_exit_alias() -> None:
    """Test /claude exit works as alias for quit."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    # Enable mode
    await session_mgr.set_claude_mode("test", "user1", True)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["exit"],
    )

    result = await router.dispatch("claude", ctx)
    assert "Exited Claude direct conversation mode" in result

    # Verify mode is disabled
    is_enabled = await session_mgr.get_claude_mode("test", "user1")
    assert is_enabled is False


@pytest.mark.asyncio
async def test_claude_workspace_show_not_set() -> None:
    """Test /claude workspace show when no workspace is set."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["workspace", "show"],
    )

    result = await router.dispatch("claude", ctx)
    assert "No workspace set" in result


@pytest.mark.asyncio
async def test_claude_workspace_set() -> None:
    """Test /claude workspace set <path>."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["workspace", "set", "~/test-workspace"],
    )

    result = await router.dispatch("claude", ctx)
    assert "Workspace set to:" in result

    # Verify workspace was set
    workspace = await session_mgr.get_claude_workspace("test", "user1")
    assert workspace is not None
    assert "test-workspace" in workspace


@pytest.mark.asyncio
async def test_claude_workspace_show_after_set() -> None:
    """Test /claude workspace show after setting a workspace."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    # Set workspace
    await session_mgr.set_claude_workspace("test", "user1", "~/my-project")

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["workspace", "show"],
    )

    result = await router.dispatch("claude", ctx)
    assert "Current workspace:" in result
    assert "my-project" in result


@pytest.mark.asyncio
async def test_claude_workspace_with_spaces() -> None:
    """Test /claude workspace set with paths containing spaces."""
    session_mgr = SessionManager()
    router = CommandRouter(session_mgr, None, None, None)

    ctx = CommandContext(
        channel="test",
        user_id="user1",
        chat_id="chat1",
        context_id="default",
        args=["workspace", "set", "~/my", "test", "project"],
    )

    result = await router.dispatch("claude", ctx)
    assert "Workspace set to:" in result

    # Verify workspace includes spaces
    workspace = await session_mgr.get_claude_workspace("test", "user1")
    assert workspace is not None
    assert "my test project" in workspace
