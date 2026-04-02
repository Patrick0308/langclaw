"""
Approval handler for Claude Agent SDK tool execution.

Manages user approval requests before tools are executed, integrating
with the channel system to surface approval prompts to users.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from loguru import logger

if TYPE_CHECKING:
    from langclaw.gateway.base import BaseChannel


@dataclass
class ApprovalRequest:
    """Represents a pending tool approval request."""

    request_id: str
    tool_name: str
    input_data: dict[str, Any]
    channel: str
    user_id: str
    chat_id: str
    context_id: str
    event: asyncio.Event
    result: PermissionResultAllow | PermissionResultDeny | None = None


class ApprovalManager:
    """
    Manages tool approval requests for Claude Agent SDK.

    Handles the flow of approval requests from the SDK to users via channels,
    and routes user responses back to the waiting approval callback.
    """

    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._lock = asyncio.Lock()

    async def request_approval(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        _context: ToolPermissionContext,
        channel: BaseChannel,
        channel_name: str,
        user_id: str,
        chat_id: str,
        context_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """
        Request user approval for a tool execution.

        Sends an approval request to the user via the channel and waits
        for their response. Returns the approval or denial result.

        Args:
            tool_name:    Name of the tool requesting approval.
            input_data:   Tool input parameters.
            context:      Permission context from SDK.
            channel:      Channel instance to send approval request through.
            channel_name: Channel identifier.
            user_id:      User identifier.
            chat_id:      Chat/conversation identifier.
            context_id:   Context identifier.
            metadata:     Channel-specific metadata (e.g., thread_ts for Slack).

        Returns:
            PermissionResultAllow if approved, PermissionResultDeny if denied.
        """
        from claude_agent_sdk.types import PermissionResultDeny

        logger.info(
            f"ApprovalManager.request_approval called | tool={tool_name} | "
            f"channel={channel_name} | user={user_id} | chat={chat_id} | metadata={metadata}"
        )

        # Create unique request ID
        request_id = str(uuid.uuid4())

        # Create approval request
        request = ApprovalRequest(
            request_id=request_id,
            tool_name=tool_name,
            input_data=input_data,
            channel=channel_name,
            user_id=user_id,
            chat_id=chat_id,
            context_id=context_id,
            event=asyncio.Event(),
        )

        # Store pending request
        async with self._lock:
            self._pending[request_id] = request

        try:
            # Send approval request via channel
            logger.info(
                f"Sending approval request | request_id={request_id} | tool={tool_name} | "
                f"channel={channel_name} | user={user_id} | chat={chat_id}"
            )

            try:
                # Build kwargs for send_approval_request
                request_kwargs: dict[str, Any] = {
                    "request_id": request_id,
                    "tool_name": tool_name,
                    "input_data": input_data,
                    "chat_id": chat_id,
                    "user_id": user_id,
                }
                if metadata:
                    request_kwargs["metadata"] = metadata

                await channel.send_approval_request(**request_kwargs)
                logger.info(f"Approval request sent | request_id={request_id} | tool={tool_name}")
            except Exception as e:
                logger.error(
                    f"Failed to send approval request | request_id={request_id} | "
                    f"tool={tool_name} | error={e!r}",
                    exc_info=True,
                )
                return PermissionResultDeny(message=f"Failed to send approval request: {e}")

            # Wait for user response (with timeout)
            try:
                await asyncio.wait_for(request.event.wait(), timeout=300.0)  # 5 min
            except TimeoutError:
                logger.warning(f"Approval request {request_id} timed out")
                return PermissionResultDeny(message="Approval request timed out")

            # Return the result
            if request.result:
                return request.result

            # Fallback to deny if no result set
            return PermissionResultDeny(message="No approval result received")

        finally:
            # Cleanup pending request
            async with self._lock:
                self._pending.pop(request_id, None)

    async def handle_approval_response(
        self,
        request_id: str,
        approved: bool,
        channel: str,
        user_id: str,
    ) -> bool:
        """
        Handle a user's approval response.

        Args:
            request_id: ID of the approval request.
            approved:   True if approved, False if denied.
            channel:    Channel the response came from.
            user_id:    User who sent the response.

        Returns:
            True if the request was found and handled, False otherwise.
        """
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        async with self._lock:
            request = self._pending.get(request_id)

        if not request:
            logger.warning(f"No pending approval request found: {request_id}")
            return False

        # Verify the response is from the correct user
        if request.channel != channel or request.user_id != user_id:
            logger.warning(
                f"Approval response from wrong user | "
                f"expected={request.channel}:{request.user_id} "
                f"got={channel}:{user_id}"
            )
            return False

        # Set result
        if approved:
            request.result = PermissionResultAllow(updated_input=request.input_data)
            logger.info(f"Approved tool execution | request={request_id} tool={request.tool_name}")
        else:
            request.result = PermissionResultDeny(message="User denied this action")
            logger.info(f"Denied tool execution | request={request_id} tool={request.tool_name}")

        # Signal completion
        request.event.set()
        return True
