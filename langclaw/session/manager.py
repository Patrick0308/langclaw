"""
SessionManager — maps (channel, user_id, context_id) → LangGraph thread_id.

``context_id`` is a session discriminator, **not** a delivery address.
Different values create separate LangGraph threads for the same user
(e.g. ``"cron:task:<uuid>"`` isolates a scheduled task from the main
conversation).

Conversation state lives entirely inside the LangGraph checkpointer.
This manager only maintains the ID mapping so the same thread is resumed
across messages from the same user in the same context.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    pass


class SessionManager:
    """
    Thread-safe mapping of channel conversation keys to LangGraph thread IDs.

    Key format: ``"<channel>:<user_id>:<context_id>"``

    The mapping is held in-process by default. For multi-process / multi-instance
    deployments extend this class to back the store with Redis or a shared DB —
    the interface stays identical.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._active_agent_store: dict[str, str] = {}
        self._claude_mode_store: dict[str, bool] = {}
        self._claude_sessions: dict[str, Any] = {}  # Store Agent SDK sessions
        self._claude_context: dict[str, dict[str, str]] = {}  # Store (chat_id, context_id) per user
        self._claude_workspace: dict[str, str] = {}  # Store workspace path per user
        self._claude_has_approval: dict[str, bool] = {}  # Track if client has approval callback
        self._lock = asyncio.Lock()

    async def get_or_create_thread(
        self,
        channel: str,
        user_id: str,
        context_id: str = "default",
    ) -> str:
        """
        Return the existing thread_id for this (channel, user, context) triple,
        or create and store a new UUID thread_id.
        """
        key = self._make_key(channel, user_id, context_id)
        async with self._lock:
            if key not in self._store:
                self._store[key] = str(uuid.uuid4())
                logger.info(f"Created new thread {self._store[key]} for {key}")
            return self._store[key]

    async def delete_thread(
        self,
        channel: str,
        user_id: str,
        context_id: str = "default",
    ) -> bool:
        """
        Remove the thread mapping (e.g. on /reset). Returns True if it existed.
        Note: this does NOT delete the checkpoint from LangGraph storage.
        """
        key = self._make_key(channel, user_id, context_id)
        async with self._lock:
            return self._store.pop(key, None) is not None

    def make_runnable_config(
        self,
        thread_id: str,
        channel_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Build a LangGraph ``RunnableConfig`` dict for the given thread.

        The optional ``channel_context`` dict is forwarded into
        ``configurable["channel_context"]`` where ``ChannelContextMiddleware``
        picks it up.
        """
        configurable: dict[str, Any] = {"thread_id": thread_id}
        if channel_context:
            configurable["channel_context"] = channel_context
        return {"configurable": configurable}

    async def get_config(
        self,
        channel: str,
        user_id: str,
        context_id: str = "default",
        channel_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Convenience: get or create thread then return a ready RunnableConfig.
        """
        thread_id = await self.get_or_create_thread(channel, user_id, context_id)
        logger.info(f"Current thread_id: {thread_id} for {channel}:{user_id}:{context_id}")
        return self.make_runnable_config(thread_id, channel_context)

    async def get_active_agent(self, channel: str, user_id: str) -> str:
        """Return the active agent name for this (channel, user_id) pair.

        Returns:
            The stored agent name, or ``"default"`` if none has been set.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            return self._active_agent_store.get(key, "default")

    async def set_active_agent(self, channel: str, user_id: str, agent_name: str) -> None:
        """Persist the active agent name for this (channel, user_id) pair.

        Passing ``"default"`` removes the entry, keeping the store clean.

        Args:
            channel:    Channel name (e.g. ``"telegram"``).
            user_id:    Platform-specific user identifier.
            agent_name: Agent name to activate. Pass ``"default"`` to reset.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            if agent_name == "default":
                self._active_agent_store.pop(key, None)
            else:
                self._active_agent_store[key] = agent_name

    async def get_claude_mode(self, channel: str, user_id: str) -> bool:
        """Check if user is in Claude direct conversation mode.

        Returns:
            ``True`` if the user is in Claude mode, ``False`` otherwise.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            return self._claude_mode_store.get(key, False)

    async def set_claude_mode(self, channel: str, user_id: str, enabled: bool) -> None:
        """Enable or disable Claude direct conversation mode for a user.

        Args:
            channel: Channel name (e.g. ``"telegram"``).
            user_id: Platform-specific user identifier.
            enabled: ``True`` to enable Claude mode, ``False`` to disable.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            if enabled:
                self._claude_mode_store[key] = True
            else:
                self._claude_mode_store.pop(key, None)
                # Disconnect and cleanup the client when exiting
                client = self._claude_sessions.pop(key, None)
                self._claude_has_approval.pop(key, None)
                if client:
                    try:
                        await client.disconnect()
                        logger.info(f"Disconnected Claude SDK client for {key}")
                    except Exception as e:
                        logger.warning(f"Failed to disconnect Claude SDK client: {e}")

    async def update_claude_context(
        self,
        channel: str,
        user_id: str,
        chat_id: str,
        context_id: str,
    ) -> None:
        """Update the context for a Claude SDK session.

        This stores the current chat_id and context_id for use in approval callbacks.

        Args:
            channel:    Channel name.
            user_id:    User identifier.
            chat_id:    Chat/conversation identifier.
            context_id: Context identifier.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            self._claude_context[key] = {
                "chat_id": chat_id,
                "context_id": context_id,
            }

    async def get_claude_context(
        self,
        channel: str,
        user_id: str,
    ) -> dict[str, str]:
        """Get the current context for a Claude SDK session.

        Args:
            channel: Channel name.
            user_id: User identifier.

        Returns:
            Dict with chat_id and context_id, or defaults if not set.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            return self._claude_context.get(key, {"chat_id": "", "context_id": "default"})

    async def get_claude_workspace(
        self,
        channel: str,
        user_id: str,
    ) -> str | None:
        """Get the current workspace path for a user's Claude session.

        Args:
            channel: Channel name.
            user_id: User identifier.

        Returns:
            Workspace path, or None if not set.
        """
        key = f"{channel}:{user_id}"
        async with self._lock:
            return self._claude_workspace.get(key)

    async def set_claude_workspace(
        self,
        channel: str,
        user_id: str,
        workspace: str,
    ) -> None:
        """Set the workspace path for a user's Claude session.

        This will disconnect the existing Claude client so a new one
        will be created with the updated workspace on the next interaction.

        Args:
            channel:   Channel name.
            user_id:   User identifier.
            workspace: Workspace directory path.
        """
        from pathlib import Path

        # Expand ~ and resolve to absolute path
        workspace_path = Path(workspace).expanduser().resolve()

        key = f"{channel}:{user_id}"
        async with self._lock:
            self._claude_workspace[key] = str(workspace_path)

            # Disconnect existing client so a new one will be created with new workspace
            client = self._claude_sessions.pop(key, None)
            if client:
                try:
                    await client.disconnect()
                    logger.info(f"Disconnected Claude SDK client for {key} due to workspace change")
                except Exception as e:
                    logger.warning(f"Failed to disconnect Claude SDK client: {e}")

    async def get_or_create_claude_client(
        self,
        channel: str,
        user_id: str,
        can_use_tool: Any | None = None,
    ) -> Any:
        """Get or create a ClaudeSDKClient for a user.

        If the approval callback setting changes (from None to callback or vice versa),
        the existing client will be disconnected and a new one created with the
        updated configuration.

        Args:
            channel:      Channel name.
            user_id:      User identifier.
            can_use_tool: Optional callback for tool approval.

        Returns:
            ClaudeSDKClient instance.
        """
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        key = f"{channel}:{user_id}"
        async with self._lock:
            # Check if approval setting changed
            has_approval = can_use_tool is not None
            existing_has_approval = self._claude_has_approval.get(key, False)

            # If client exists and approval setting changed, recreate it
            if key in self._claude_sessions and has_approval != existing_has_approval:
                client = self._claude_sessions.pop(key)
                self._claude_has_approval.pop(key, None)
                try:
                    await client.disconnect()
                    logger.info(
                        f"Disconnected Claude SDK client for {key} due to approval setting change"
                    )
                except Exception as e:
                    logger.warning(f"Failed to disconnect Claude SDK client: {e}")

            if key not in self._claude_sessions:
                # Get workspace if set
                workspace = self._claude_workspace.get(key)

                # Build options
                options_kwargs: dict[str, Any] = {}
                if can_use_tool:
                    options_kwargs["can_use_tool"] = can_use_tool
                if workspace:
                    options_kwargs["cwd"] = workspace

                options = ClaudeAgentOptions(**options_kwargs) if options_kwargs else None

                # Create client with options
                client = ClaudeSDKClient(options=options)

                # Connect the client
                await client.connect()
                self._claude_sessions[key] = client
                self._claude_has_approval[key] = has_approval

                approval_msg = " with approval callback" if has_approval else ""
                workspace_msg = f" workspace={workspace}" if workspace else ""
                logger.info(
                    f"Created and connected Claude SDK client for {key}"
                    + approval_msg
                    + workspace_msg
                )
            return self._claude_sessions[key]

    def all_threads(self) -> dict[str, str]:
        """Return a snapshot of all key→thread_id mappings (for diagnostics)."""
        return dict(self._store)

    @staticmethod
    def _make_key(channel: str, user_id: str, context_id: str) -> str:
        return f"{channel}:{user_id}:{context_id}"
