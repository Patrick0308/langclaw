"""Integration test for Claude Agent SDK workspace (cwd) configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_claude_client_created_with_workspace_cwd() -> None:
    """Test that Claude client is created with cwd from workspace setting."""
    from langclaw.session.manager import SessionManager

    session_mgr = SessionManager()

    # Set workspace
    await session_mgr.set_claude_workspace("test", "user1", "~/test-project")

    # Mock Claude Agent SDK
    with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class, patch(
        "claude_agent_sdk.ClaudeAgentOptions"
    ) as mock_options_class:
        # Setup mocks
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        # Get or create client (should create new one)
        await session_mgr.get_or_create_claude_client("test", "user1")

        # Verify ClaudeAgentOptions was called with cwd
        mock_options_class.assert_called_once()
        call_kwargs = mock_options_class.call_args[1]  # Get keyword arguments

        assert "cwd" in call_kwargs
        assert "test-project" in call_kwargs["cwd"]
        assert call_kwargs["cwd"].startswith("/")  # Should be absolute path

        # Verify client was created with options
        mock_client_class.assert_called_once()
        assert mock_client_class.call_args[1]["options"] is not None


@pytest.mark.asyncio
async def test_claude_client_recreated_when_workspace_changes() -> None:
    """Test that changing workspace causes client to be recreated with new cwd."""
    from langclaw.session.manager import SessionManager

    session_mgr = SessionManager()

    with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class, patch(
        "claude_agent_sdk.ClaudeAgentOptions"
    ) as mock_options_class:
        # Setup mocks
        mock_client1 = AsyncMock()
        mock_client2 = AsyncMock()
        mock_client_class.side_effect = [mock_client1, mock_client2]

        # Set first workspace and create client
        await session_mgr.set_claude_workspace("test", "user1", "~/project1")
        await session_mgr.get_or_create_claude_client("test", "user1")

        # Verify first client created with project1
        first_call_kwargs = mock_options_class.call_args_list[0][1]
        assert "cwd" in first_call_kwargs
        assert "project1" in first_call_kwargs["cwd"]

        # Change workspace
        await session_mgr.set_claude_workspace("test", "user1", "~/project2")

        # Verify old client was disconnected
        mock_client1.disconnect.assert_called_once()

        # Create new client
        await session_mgr.get_or_create_claude_client("test", "user1")

        # Verify second client created with project2
        second_call_kwargs = mock_options_class.call_args_list[1][1]
        assert "cwd" in second_call_kwargs
        assert "project2" in second_call_kwargs["cwd"]
        assert "project1" not in second_call_kwargs["cwd"]


@pytest.mark.asyncio
async def test_claude_client_without_workspace_no_cwd() -> None:
    """Test that Claude client created without workspace has no cwd set."""
    from langclaw.session.manager import SessionManager

    session_mgr = SessionManager()

    # Don't set workspace - create client directly
    with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class, patch(
        "claude_agent_sdk.ClaudeAgentOptions"
    ) as mock_options_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        await session_mgr.get_or_create_claude_client("test", "user1")

        # If no workspace and no can_use_tool, options should be None
        call_args = mock_client_class.call_args
        if call_args[1].get("options") is not None:
            # If options were created, cwd should not be in them
            call_kwargs = mock_options_class.call_args[1]
            assert "cwd" not in call_kwargs or call_kwargs["cwd"] is None


@pytest.mark.asyncio
async def test_workspace_and_approval_both_work() -> None:
    """Test that workspace (cwd) and approval callback can both be configured."""
    from langclaw.session.manager import SessionManager

    session_mgr = SessionManager()

    # Set workspace
    await session_mgr.set_claude_workspace("test", "user1", "~/my-workspace")

    # Mock approval callback
    async def mock_approval(*args, **kwargs):
        pass

    with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class, patch(
        "claude_agent_sdk.ClaudeAgentOptions"
    ) as mock_options_class:
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        # Create client with both workspace and approval
        await session_mgr.get_or_create_claude_client("test", "user1", can_use_tool=mock_approval)

        # Verify both cwd and can_use_tool are in options
        call_kwargs = mock_options_class.call_args[1]
        assert "cwd" in call_kwargs
        assert "my-workspace" in call_kwargs["cwd"]
        assert "can_use_tool" in call_kwargs
        assert call_kwargs["can_use_tool"] is mock_approval
