"""Tests for Claude Agent SDK tool approval system."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from langclaw.bus.base import OutboundMessage
from langclaw.gateway.approval import ApprovalManager


class MockChannel:
    """Mock channel for testing."""

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self.name = "mock"

    async def send_approval_request(
        self,
        request_id: str,
        tool_name: str,
        input_data: dict,
        chat_id: str,
        user_id: str,
    ) -> None:
        """Store approval request for verification."""
        self.sent_messages.append(
            {
                "request_id": request_id,
                "tool_name": tool_name,
                "input_data": input_data,
                "chat_id": chat_id,
                "user_id": user_id,
            }
        )


@pytest.fixture
def approval_manager() -> ApprovalManager:
    """Create an approval manager for testing."""
    return ApprovalManager()


@pytest.fixture
def mock_channel() -> MockChannel:
    """Create a mock channel for testing."""
    return MockChannel()


@pytest.mark.asyncio
async def test_approval_flow_approved(
    approval_manager: ApprovalManager,
    mock_channel: MockChannel,
) -> None:
    """Test successful tool approval flow."""
    # Create a mock context
    mock_context = MagicMock()

    # Start approval request in background
    approval_task = asyncio.create_task(
        approval_manager.request_approval(
            tool_name="Bash",
            input_data={"command": "ls -la", "description": "List files"},
            _context=mock_context,
            channel=mock_channel,
            channel_name="mock",
            user_id="test_user",
            chat_id="test_chat",
            context_id="default",
        )
    )

    # Wait a bit for the approval request to be sent
    await asyncio.sleep(0.1)

    # Verify approval request was sent
    assert len(mock_channel.sent_messages) == 1
    approval_req = mock_channel.sent_messages[0]
    assert approval_req["tool_name"] == "Bash"
    assert approval_req["input_data"]["command"] == "ls -la"

    # Extract request ID
    request_id = approval_req["request_id"]

    # Approve the request
    handled = await approval_manager.handle_approval_response(
        request_id=request_id,
        approved=True,
        channel="mock",
        user_id="test_user",
    )
    assert handled is True

    # Wait for approval task to complete
    result = await approval_task

    # Verify result is approval
    assert hasattr(result, "updated_input")
    assert result.updated_input == {"command": "ls -la", "description": "List files"}


@pytest.mark.asyncio
async def test_approval_flow_denied(
    approval_manager: ApprovalManager,
    mock_channel: MockChannel,
) -> None:
    """Test tool denial flow."""
    mock_context = MagicMock()

    # Start approval request in background
    approval_task = asyncio.create_task(
        approval_manager.request_approval(
            tool_name="Bash",
            input_data={"command": "rm -rf /"},
            _context=mock_context,
            channel=mock_channel,
            channel_name="mock",
            user_id="test_user",
            chat_id="test_chat",
            context_id="default",
        )
    )

    # Wait for approval request
    await asyncio.sleep(0.1)

    # Extract request ID
    request_id = mock_channel.sent_messages[0]["request_id"]

    # Deny the request
    handled = await approval_manager.handle_approval_response(
        request_id=request_id,
        approved=False,
        channel="mock",
        user_id="test_user",
    )
    assert handled is True

    # Wait for approval task to complete
    result = await approval_task

    # Verify result is denial
    assert hasattr(result, "message")
    assert result.message == "User denied this action"


@pytest.mark.asyncio
async def test_approval_timeout(
    approval_manager: ApprovalManager,
    mock_channel: MockChannel,
) -> None:
    """Test approval request timeout."""
    # This test would need the timeout to be configurable
    # For now, skip the actual timeout test
    pytest.skip("Timeout test requires configurable timeout value")


@pytest.mark.asyncio
async def test_approval_wrong_user(
    approval_manager: ApprovalManager,
    mock_channel: MockChannel,
) -> None:
    """Test that approval from wrong user is rejected."""
    mock_context = MagicMock()

    # Start approval request
    approval_task = asyncio.create_task(
        approval_manager.request_approval(
            tool_name="Bash",
            input_data={"command": "echo hello"},
            _context=mock_context,
            channel=mock_channel,
            channel_name="mock",
            user_id="test_user",
            chat_id="test_chat",
            context_id="default",
        )
    )

    await asyncio.sleep(0.1)

    # Extract request ID
    request_id = mock_channel.sent_messages[0]["request_id"]

    # Try to approve from different user
    handled = await approval_manager.handle_approval_response(
        request_id=request_id,
        approved=True,
        channel="mock",
        user_id="different_user",  # Wrong user!
    )
    assert handled is False  # Should not be handled

    # Cancel the waiting task
    approval_task.cancel()
    try:
        await approval_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_approval_format_message() -> None:
    """Test approval message formatting in BaseChannel."""
    from langclaw.gateway.base import BaseChannel

    class TestChannel(BaseChannel):
        name = "test"
        sent_messages: list[OutboundMessage] = []

        async def start(self, bus):
            pass

        async def send_ai_message(self, msg: OutboundMessage) -> None:
            self.sent_messages.append(msg)

        async def stop(self) -> None:
            pass

    channel = TestChannel()

    # Test Bash tool formatting
    await channel.send_approval_request(
        request_id="test-123",
        tool_name="Bash",
        input_data={"command": "ls -la", "description": "List files"},
        chat_id="chat1",
        user_id="user1",
    )

    assert len(channel.sent_messages) == 1
    msg = channel.sent_messages[0]
    assert "Bash" in msg.content
    assert "ls -la" in msg.content
    assert "List files" in msg.content
    assert "/approve test-123" in msg.content
    assert "/deny test-123" in msg.content
