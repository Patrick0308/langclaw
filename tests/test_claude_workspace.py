"""Tests for Claude Agent SDK workspace management."""

from __future__ import annotations

import pytest

from langclaw.session.manager import SessionManager


@pytest.mark.asyncio
async def test_workspace_not_set_returns_none() -> None:
    """Test that get_claude_workspace returns None when not set."""
    session_mgr = SessionManager()

    workspace = await session_mgr.get_claude_workspace("test", "user1")
    assert workspace is None


@pytest.mark.asyncio
async def test_workspace_set_and_get() -> None:
    """Test setting and getting workspace."""
    session_mgr = SessionManager()

    await session_mgr.set_claude_workspace("test", "user1", "~/my-project")

    workspace = await session_mgr.get_claude_workspace("test", "user1")
    assert workspace is not None
    assert "my-project" in workspace
    assert workspace.startswith("/")  # Should be absolute path


@pytest.mark.asyncio
async def test_workspace_expands_tilde() -> None:
    """Test that workspace paths expand ~ to home directory."""
    session_mgr = SessionManager()

    await session_mgr.set_claude_workspace("test", "user1", "~/test")

    workspace = await session_mgr.get_claude_workspace("test", "user1")
    assert workspace is not None
    assert "~" not in workspace  # Tilde should be expanded
    assert workspace.startswith("/")


@pytest.mark.asyncio
async def test_workspace_per_user() -> None:
    """Test that workspace is tracked independently per user."""
    session_mgr = SessionManager()

    await session_mgr.set_claude_workspace("test", "user1", "~/project1")
    await session_mgr.set_claude_workspace("test", "user2", "~/project2")

    workspace1 = await session_mgr.get_claude_workspace("test", "user1")
    workspace2 = await session_mgr.get_claude_workspace("test", "user2")

    assert workspace1 is not None
    assert workspace2 is not None
    assert "project1" in workspace1
    assert "project2" in workspace2
    assert workspace1 != workspace2


@pytest.mark.asyncio
async def test_workspace_per_channel() -> None:
    """Test that workspace is tracked independently per channel."""
    session_mgr = SessionManager()

    await session_mgr.set_claude_workspace("telegram", "user1", "~/telegram-workspace")
    await session_mgr.set_claude_workspace("slack", "user1", "~/slack-workspace")

    workspace_telegram = await session_mgr.get_claude_workspace("telegram", "user1")
    workspace_slack = await session_mgr.get_claude_workspace("slack", "user1")

    assert workspace_telegram is not None
    assert workspace_slack is not None
    assert "telegram-workspace" in workspace_telegram
    assert "slack-workspace" in workspace_slack


@pytest.mark.asyncio
async def test_workspace_change_disconnects_client() -> None:
    """Test that changing workspace disconnects existing Claude client."""
    session_mgr = SessionManager()

    # Manually add a mock client
    key = "test:user1"
    session_mgr._claude_sessions[key] = "mock_client"

    # Change workspace should remove the client
    await session_mgr.set_claude_workspace("test", "user1", "~/new-workspace")

    # Client should be removed
    assert key not in session_mgr._claude_sessions


@pytest.mark.asyncio
async def test_workspace_with_spaces() -> None:
    """Test workspace paths with spaces."""
    session_mgr = SessionManager()

    await session_mgr.set_claude_workspace("test", "user1", "~/my test project")

    workspace = await session_mgr.get_claude_workspace("test", "user1")
    assert workspace is not None
    assert "my test project" in workspace


@pytest.mark.asyncio
async def test_workspace_relative_path_made_absolute() -> None:
    """Test that relative paths are made absolute."""
    session_mgr = SessionManager()

    await session_mgr.set_claude_workspace("test", "user1", ".")

    workspace = await session_mgr.get_claude_workspace("test", "user1")
    assert workspace is not None
    assert workspace.startswith("/")  # Should be absolute
    assert "." != workspace  # Should not be relative
