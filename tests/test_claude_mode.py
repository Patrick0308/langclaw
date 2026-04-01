"""Tests for /claude command and Claude Agent SDK integration."""

from __future__ import annotations

from langclaw.session.manager import SessionManager


class TestClaudeMode:
    """Test suite for Claude Agent SDK mode functionality."""

    async def test_claude_mode_toggle(self) -> None:
        """Test enabling and disabling Claude mode."""
        session_mgr = SessionManager()

        # Initially not in Claude mode
        is_enabled = await session_mgr.get_claude_mode("telegram", "user123")
        assert is_enabled is False

        # Enable Claude mode
        await session_mgr.set_claude_mode("telegram", "user123", True)
        is_enabled = await session_mgr.get_claude_mode("telegram", "user123")
        assert is_enabled is True

        # Disable Claude mode
        await session_mgr.set_claude_mode("telegram", "user123", False)
        is_enabled = await session_mgr.get_claude_mode("telegram", "user123")
        assert is_enabled is False

    async def test_claude_mode_per_user(self) -> None:
        """Test that Claude mode is tracked independently per user."""
        session_mgr = SessionManager()

        # Enable for user1
        await session_mgr.set_claude_mode("telegram", "user1", True)

        # user1 should be in Claude mode, user2 should not
        assert await session_mgr.get_claude_mode("telegram", "user1") is True
        assert await session_mgr.get_claude_mode("telegram", "user2") is False

    async def test_claude_mode_per_channel(self) -> None:
        """Test that Claude mode is tracked independently per channel."""
        session_mgr = SessionManager()

        # Enable for same user on different channels
        await session_mgr.set_claude_mode("telegram", "user1", True)
        await session_mgr.set_claude_mode("slack", "user1", False)

        # Should be independent
        assert await session_mgr.get_claude_mode("telegram", "user1") is True
        assert await session_mgr.get_claude_mode("slack", "user1") is False

    async def test_claude_session_cleanup_on_exit(self) -> None:
        """Test that Claude sessions are cleaned up when exiting mode."""
        session_mgr = SessionManager()

        # Enable mode (this would normally create a session)
        await session_mgr.set_claude_mode("telegram", "user1", True)

        # Manually add a mock session
        key = "telegram:user1"
        session_mgr._claude_sessions[key] = "mock_session"

        # Disable mode should clean up the session
        await session_mgr.set_claude_mode("telegram", "user1", False)

        # Session should be removed
        assert key not in session_mgr._claude_sessions
