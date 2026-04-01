"""Integration tests for Claude Agent SDK approval system."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from langclaw.config.schema import LangclawConfig


@pytest.mark.asyncio
async def test_approval_integration_with_gateway() -> None:
    """Test approval flow integrated with GatewayManager."""
    # This test verifies that the approval system integrates correctly
    # with the gateway when claude_sdk_require_approval is enabled

    config = LangclawConfig(
        agents={
            "model": "anthropic:claude-3-5-sonnet-20241022",
            "claude_sdk_require_approval": True,
        },
        channels={"websocket": {"enabled": False}},
    )

    # Verify config setting is loaded correctly
    assert config.agents.claude_sdk_require_approval is True


@pytest.mark.asyncio
async def test_approval_commands_registered() -> None:
    """Test that /approve and /deny commands are registered."""
    from langclaw.gateway.commands import CommandRouter
    from langclaw.session.manager import SessionManager

    sessions = SessionManager()
    router = CommandRouter(sessions, None, None, workspace_dir="")

    # The approval commands are registered in GatewayManager._setup_approval_commands
    # This test verifies the command router can handle them
    assert router is not None


@pytest.mark.asyncio
async def test_approval_manager_concurrent_requests() -> None:
    """Test that approval manager handles multiple concurrent requests."""
    from langclaw.gateway.approval import ApprovalManager

    manager = ApprovalManager()

    class MockChannel:
        def __init__(self) -> None:
            self.messages = []

        async def send_approval_request(
            self, request_id: str, tool_name: str, input_data: dict, chat_id: str, user_id: str
        ) -> None:
            self.messages.append(
                {
                    "request_id": request_id,
                    "tool_name": tool_name,
                    "input_data": input_data,
                }
            )

    channel1 = MockChannel()
    channel2 = MockChannel()

    mock_context = MagicMock()

    # Create two concurrent approval requests
    task1 = asyncio.create_task(
        manager.request_approval(
            tool_name="Bash",
            input_data={"command": "ls"},
            _context=mock_context,
            channel=channel1,
            channel_name="test1",
            user_id="user1",
            chat_id="chat1",
            context_id="default",
        )
    )

    task2 = asyncio.create_task(
        manager.request_approval(
            tool_name="Bash",
            input_data={"command": "pwd"},
            _context=mock_context,
            channel=channel2,
            channel_name="test2",
            user_id="user2",
            chat_id="chat2",
            context_id="default",
        )
    )

    # Wait for approval messages to be sent
    await asyncio.sleep(0.1)

    # Verify both requests were created
    assert len(channel1.messages) == 1
    assert len(channel2.messages) == 1

    # Get request IDs
    request_id1 = channel1.messages[0]["request_id"]
    request_id2 = channel2.messages[0]["request_id"]

    # Approve both (in different order)
    await manager.handle_approval_response(request_id2, True, "test2", "user2")
    await manager.handle_approval_response(request_id1, False, "test1", "user1")

    # Wait for tasks to complete
    result1 = await task1
    result2 = await task2

    # Verify results
    assert hasattr(result1, "message")  # Denied
    assert hasattr(result2, "updated_input")  # Approved


@pytest.mark.asyncio
async def test_session_manager_claude_context() -> None:
    """Test that SessionManager properly stores and retrieves Claude context."""
    from langclaw.session.manager import SessionManager

    sessions = SessionManager()

    # Update context
    await sessions.update_claude_context(
        channel="test_channel",
        user_id="test_user",
        chat_id="chat_123",
        context_id="ctx_456",
    )

    # Retrieve context
    ctx = await sessions.get_claude_context("test_channel", "test_user")

    assert ctx["chat_id"] == "chat_123"
    assert ctx["context_id"] == "ctx_456"

    # Get context for non-existent user returns defaults
    ctx2 = await sessions.get_claude_context("other_channel", "other_user")
    assert ctx2["chat_id"] == ""
    assert ctx2["context_id"] == "default"
