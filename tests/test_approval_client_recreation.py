"""Test that Claude client is recreated when approval setting changes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_client_recreated_when_approval_enabled() -> None:
    """Test that enabling approval recreates the Claude client."""
    from langclaw.session.manager import SessionManager

    session_mgr = SessionManager()

    with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class, patch(
        "claude_agent_sdk.ClaudeAgentOptions"
    ) as mock_options_class:
        mock_client1 = AsyncMock()
        mock_client2 = AsyncMock()
        mock_client_class.side_effect = [mock_client1, mock_client2]

        # First: create client WITHOUT approval
        client1 = await session_mgr.get_or_create_claude_client("test", "user1", can_use_tool=None)
        assert client1 is mock_client1
        assert mock_client_class.call_count == 1

        # Verify first client was created with options=None (no callback, no workspace)
        first_client_kwargs = mock_client_class.call_args_list[0][1]
        assert first_client_kwargs.get("options") is None

        # Second: get client WITH approval - should recreate
        async def mock_approval(*args, **kwargs):
            pass

        client2 = await session_mgr.get_or_create_claude_client(
            "test", "user1", can_use_tool=mock_approval
        )

        # Old client should be disconnected
        mock_client1.disconnect.assert_called_once()

        # New client should be created
        assert client2 is mock_client2
        assert mock_client_class.call_count == 2

        # Verify second client has approval callback
        assert mock_options_class.call_count == 1  # Called once for second client
        second_call_kwargs = mock_options_class.call_args_list[0][1]
        assert "can_use_tool" in second_call_kwargs
        assert second_call_kwargs["can_use_tool"] is mock_approval


@pytest.mark.asyncio
async def test_client_recreated_when_approval_disabled() -> None:
    """Test that disabling approval recreates the Claude client."""
    from langclaw.session.manager import SessionManager

    session_mgr = SessionManager()

    async def mock_approval(*args, **kwargs):
        pass

    with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class, patch(
        "claude_agent_sdk.ClaudeAgentOptions"
    ) as mock_options_class:
        mock_client1 = AsyncMock()
        mock_client2 = AsyncMock()
        mock_client_class.side_effect = [mock_client1, mock_client2]

        # First: create client WITH approval
        client1 = await session_mgr.get_or_create_claude_client(
            "test", "user1", can_use_tool=mock_approval
        )
        assert client1 is mock_client1

        # Verify first client has approval callback
        first_call_kwargs = mock_options_class.call_args_list[0][1]
        assert "can_use_tool" in first_call_kwargs
        assert first_call_kwargs["can_use_tool"] is mock_approval

        # Second: get client WITHOUT approval - should recreate
        client2 = await session_mgr.get_or_create_claude_client("test", "user1", can_use_tool=None)

        # Old client should be disconnected
        mock_client1.disconnect.assert_called_once()

        # New client should be created
        assert client2 is mock_client2
        assert mock_client_class.call_count == 2

        # Verify second client was created with options=None (no callback after removal)
        second_client_kwargs = mock_client_class.call_args_list[1][1]
        assert second_client_kwargs.get("options") is None


@pytest.mark.asyncio
async def test_client_not_recreated_when_approval_unchanged() -> None:
    """Test that client is NOT recreated when approval setting doesn't change."""
    from langclaw.session.manager import SessionManager

    session_mgr = SessionManager()

    async def mock_approval(*args, **kwargs):
        pass

    with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class, patch(
        "claude_agent_sdk.ClaudeAgentOptions"
    ):
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        # Create client WITH approval
        client1 = await session_mgr.get_or_create_claude_client(
            "test", "user1", can_use_tool=mock_approval
        )
        assert client1 is mock_client
        assert mock_client_class.call_count == 1

        # Get client again WITH approval - should reuse
        client2 = await session_mgr.get_or_create_claude_client(
            "test", "user1", can_use_tool=mock_approval
        )
        assert client2 is mock_client
        assert mock_client_class.call_count == 1  # No new client created

        # Old client should NOT be disconnected
        mock_client.disconnect.assert_not_called()
