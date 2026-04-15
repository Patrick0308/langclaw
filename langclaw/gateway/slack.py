"""
SlackChannel — Gateway channel for Slack using Socket Mode.

Requires: langclaw[slack]  →  uv add "langclaw[slack]"

Features:
- Socket Mode (WebSocket) - no public URL needed
- Handles direct messages and app mentions
- Splits messages that exceed Slack's limits
- Respects allow_from user whitelist (user IDs or usernames)
- /start, /help, /reset, /cron command support
- Tool-progress / tool-result rendering with code-block formatting
- Automatic reconnect on socket disconnects
- File attachment support
- Reaction emoji UX feedback (configurable):
  * 👀 (eyes) when message is received → "I'm working on it"
  * ✅ (checkmark) when response is sent → "done"
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from langclaw.bus.base import BaseMessageBus, InboundMessage, OutboundMessage
from langclaw.config.schema import SlackChannelConfig
from langclaw.cron.utils import is_cron_context_id
from langclaw.gateway.base import BaseChannel
from langclaw.gateway.commands import CommandContext
from langclaw.gateway.utils import (
    TRUNCATION_SUFFIX,
    format_tool_progress,
    is_allowed,
    make_attachment,
    split_message,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 3000  # Slack has a 3000 char limit for text blocks
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20 MB


# ---------------------------------------------------------------------------
# Thread History Cache
# ---------------------------------------------------------------------------


@dataclass
class ThreadHistoryCache:
    """Cache entry for Slack thread conversation history."""

    messages: list[dict[str, Any]]
    cached_at: float
    ttl: float

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return time.time() - self.cached_at > self.ttl


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


class SlackChannel(BaseChannel):
    """
    Slack bot channel using Socket Mode (WebSocket).

    Uses the bolt-python framework with Socket Mode adapter for receiving
    messages. Reconnection is handled automatically by the socket mode handler.

    Args:
        config: Slack-specific section of LangclawConfig.channels.slack.
    """

    name = "slack"

    def __init__(self, config: SlackChannelConfig) -> None:
        self._config = config
        self._app: Any = None
        self._handler: Any = None
        self._bus: BaseMessageBus | None = None
        self._running = False
        self._tool_call_buffer: dict[str, dict] = {}
        # Track (channel_id, message_ts) pairs for reaction management
        self._reaction_tracking: dict[str, tuple[str, str]] = {}  # context_id -> (channel, ts)
        # In-memory cache for user_id -> username to avoid rate-limiting users_info
        self._user_cache: dict[str, str] = {}
        # Bot user ID for stripping mentions from app_mention events
        self._bot_user_id: str | None = None
        # Thread history cache: thread_ts -> ThreadHistoryCache
        self._thread_history_cache: dict[str, ThreadHistoryCache] = {}
        self._cache_lock = asyncio.Lock()

    def is_enabled(self) -> bool:
        return (
            self._config.enabled and bool(self._config.bot_token) and bool(self._config.app_token)
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, bus: BaseMessageBus) -> None:
        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.async_app import AsyncApp
            from slack_sdk.web.async_client import AsyncWebClient
        except ImportError as exc:
            raise ImportError(
                "SlackChannel requires 'langclaw[slack]'. Install with: uv add 'langclaw[slack]'"
            ) from exc

        self._bus = bus
        self._running = True

        # Find owner user ID from user_roles
        owner_user_id = None
        for user_id, role in self._config.user_roles.items():
            if role == "owner":
                owner_user_id = user_id
                break

        if not owner_user_id:
            raise ValueError(
                "No owner user found in user_roles. "
                "Set LANGCLAW__CHANNELS__SLACK__USER_ROLES=<user_id>:owner"
            )

        # Configure WebClient to use proxy mode
        # Only need to set base_url and x-slack-user-id header!
        custom_web_client = AsyncWebClient(
            token="dummy-bot-token",  # Proxy uses its own token
            base_url="https://slack-proxy-api.longbridge.xyz/api",
            headers={"x-slack-user-id": owner_user_id}
        )

        # Initialize Slack app with custom client (proxy mode)
        app = AsyncApp(client=custom_web_client)
        self._app = app

        # Fetch bot user ID for mention stripping
        try:
            auth_response = await app.client.auth_test()
            self._bot_user_id = auth_response.get("user_id")
            logger.info(
                f"Slack bot connected via proxy mode | "
                f"owner={owner_user_id} bot={self._bot_user_id}"
            )
        except Exception as exc:
            logger.warning(f"Failed to fetch bot user ID via proxy: {exc}")

        # Register event handlers
        @app.event("message")
        async def handle_message(event: dict, say: Any) -> None:
            # Only handle direct messages (channel_type == "im"); public/private channel
            # messages require an explicit @mention (handled by app_mention below).
            if event.get("channel_type") != "im":
                return
            # Ignore message subtypes except file_share
            subtype = event.get("subtype")
            if subtype and subtype not in ["file_share"]:
                return
            await self._on_message(event)

        @app.event("app_mention")
        async def handle_mention(event: dict, say: Any) -> None:
            await self._on_message(event)

        # Register approval button handlers (match any action_id starting with approval_)
        import re

        @app.action(re.compile("^approval_approve_"))
        async def handle_approve(ack: Any, action: dict, body: dict) -> None:
            await ack()
            logger.info(
                f"Approval button action triggered | action={action} | "
                f"body_user={body.get('user', {}).get('id')}"
            )

            request_id = action["value"].replace("approve_", "")
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            # Extract thread_ts from the message that contains the button
            message = body.get("message", {})
            thread_ts = message.get("thread_ts") or message.get("ts")

            logger.info(
                f"Processing approval | request_id={request_id} | "
                f"user={user_id} | channel={channel_id} | thread_ts={thread_ts}"
            )

            # Check if user is allowed to approve
            username = self._user_cache.get(user_id)
            if not username:
                try:
                    user_info = await self._app.client.users_info(user=user_id)
                    username = user_info.get("user", {}).get("name", "")
                    if username:
                        self._user_cache[user_id] = username
                except Exception as exc:
                    logger.debug(f"Failed to fetch Slack user info: {exc}")

            if not self._is_allowed(user_id, username):
                error_msg = "❌ You are not authorized to approve tool executions."
                logger.warning(
                    f"Approval denied - user not in allow_from | user={user_id} ({username})"
                )
                try:
                    await self._send_text(channel_id, error_msg, thread_ts=thread_ts)
                except Exception as exc:
                    logger.error(f"Failed to send authorization error: {exc}")
                return

            # Directly dispatch approve command (don't send to bus to avoid Claude mode routing)
            if self._command_router:
                from langclaw.gateway.commands import CommandContext

                ctx = CommandContext(
                    channel="slack",
                    user_id=user_id,
                    chat_id=channel_id,
                    context_id="approval",
                    args=[request_id],
                )
                response = await self._command_router.dispatch("approve", ctx)
                logger.info(f"Approval button clicked | request_id={request_id} | user={user_id}")

                # Send response back to Slack in the same thread
                try:
                    await self._send_text(channel_id, response, thread_ts=thread_ts)
                except Exception as exc:
                    logger.error(f"Failed to send approval response: {exc}")
            else:
                logger.error("Approval button clicked but command_router not available!")

        @app.action(re.compile("^approval_deny_"))
        async def handle_deny(ack: Any, action: dict, body: dict) -> None:
            await ack()
            logger.info(
                f"Deny button action triggered | action={action} | "
                f"body_user={body.get('user', {}).get('id')}"
            )

            request_id = action["value"].replace("deny_", "")
            user_id = body["user"]["id"]
            channel_id = body["channel"]["id"]
            # Extract thread_ts from the message that contains the button
            message = body.get("message", {})
            thread_ts = message.get("thread_ts") or message.get("ts")

            logger.info(
                f"Processing denial | request_id={request_id} | "
                f"user={user_id} | channel={channel_id} | thread_ts={thread_ts}"
            )

            # Check if user is allowed to deny
            username = self._user_cache.get(user_id)
            if not username:
                try:
                    user_info = await self._app.client.users_info(user=user_id)
                    username = user_info.get("user", {}).get("name", "")
                    if username:
                        self._user_cache[user_id] = username
                except Exception as exc:
                    logger.debug(f"Failed to fetch Slack user info: {exc}")

            if not self._is_allowed(user_id, username):
                error_msg = "❌ You are not authorized to deny tool executions."
                logger.warning(
                    f"Denial rejected - user not in allow_from | user={user_id} ({username})"
                )
                try:
                    await self._send_text(channel_id, error_msg, thread_ts=thread_ts)
                except Exception as exc:
                    logger.error(f"Failed to send authorization error: {exc}")
                return

            # Directly dispatch deny command (don't send to bus to avoid Claude mode routing)
            if self._command_router:
                from langclaw.gateway.commands import CommandContext

                ctx = CommandContext(
                    channel="slack",
                    user_id=user_id,
                    chat_id=channel_id,
                    context_id="approval",
                    args=[request_id],
                )
                response = await self._command_router.dispatch("deny", ctx)
                logger.info(f"Deny button clicked | request_id={request_id} | user={user_id}")

                # Send response back to Slack in the same thread
                try:
                    await self._send_text(channel_id, response, thread_ts=thread_ts)
                except Exception as exc:
                    logger.error(f"Failed to send deny response: {exc}")
            else:
                logger.error("Deny button clicked but command_router not available!")

        # Register slash commands if command router exists
        if self._command_router:
            for entry in self._command_router.list_commands():
                self._register_slash_command(app, entry.name, entry.description or entry.name)

        logger.info(
            f"SlackChannel starting… "
            f"(reaction_feedback="
            f"{'enabled' if self._config.reaction_feedback_enabled else 'disabled'})"
        )

        # Start socket mode handler
        handler = AsyncSocketModeHandler(app, self._config.app_token)
        self._handler = handler

        try:
            await handler.start_async()
        except Exception as exc:
            logger.error(f"Failed to start Slack Socket Mode: {exc}")
            raise

        # Keep the task alive until cancelled (mirrors TelegramChannel pattern)
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        if self._handler is not None:
            try:
                logger.info("Stopping SlackChannel…")
                await self._handler.close_async()
            except Exception:
                logger.exception("Error stopping SlackChannel")
            self._handler = None
        self._app = None

    # ------------------------------------------------------------------
    # Outbound hooks
    # ------------------------------------------------------------------

    async def send_tool_progress(self, msg: OutboundMessage) -> None:
        """Stash the tool call info; rendered together with tool_result."""
        if is_cron_context_id(msg.context_id):
            return
        tc_id = msg.metadata.get("tool_call_id", "")
        if tc_id:
            self._tool_call_buffer[tc_id] = {
                "tool": msg.metadata.get("tool", ""),
                "args": msg.metadata.get("args") or {},
            }

    async def send_tool_result(self, msg: OutboundMessage) -> None:
        """Pop the matching tool call and render both as one message."""
        if self._app is None or is_cron_context_id(msg.context_id):
            return

        tc_id = msg.metadata.get("tool_call_id", "")
        call_info = self._tool_call_buffer.pop(tc_id, {})
        header = format_tool_progress(
            call_info.get("tool", ""),
            call_info.get("args") or {},
            markup="markdown",
        )

        # Truncate result to fit within Slack's limit
        _CODE_BLOCK_OVERHEAD = len("```\n\n```") + 1
        max_content = MAX_MESSAGE_LEN - len(header) - _CODE_BLOCK_OVERHEAD - len(TRUNCATION_SUFFIX)
        result_text = msg.content or ""
        if len(result_text) > max_content:
            result_text = result_text[:max_content] + TRUNCATION_SUFFIX

        text = f"{header}\n```\n{result_text}\n```"
        thread_ts = msg.metadata.get("thread_ts")
        await self._send_text(msg.chat_id, text, thread_ts=thread_ts)

    async def send_ai_message(self, msg: OutboundMessage) -> None:
        """Deliver the final AI response."""
        if self._app is None:
            return
        if not msg.content:
            return

        try:
            from slackify_markdown import slackify_markdown
        except ImportError:
            slackify_markdown = None  # type: ignore

        thread_ts = (msg.metadata or {}).get("thread_ts")
        # Convert markdown to Slack's mrkdwn format
        text = slackify_markdown(msg.content) if slackify_markdown else msg.content
        for chunk in split_message(text, max_len=MAX_MESSAGE_LEN):
            await self._send_text(msg.chat_id, chunk, thread_ts=thread_ts)

        # Update reaction: 👀 → ✅
        if self._config.reaction_feedback_enabled:
            await self._swap_reaction(msg.context_id)

    async def send_approval_request(
        self,
        request_id: str,
        tool_name: str,
        input_data: dict,
        chat_id: str,
        user_id: str,
        metadata: dict | None = None,
    ) -> None:
        """Send a tool approval request using Slack Block Kit."""
        # Extract thread_ts from metadata for Slack threading
        thread_ts = (metadata or {}).get("thread_ts")
        logger.info(
            f"Slack approval request | request_id={request_id} | chat_id={chat_id} | "
            f"thread_ts={thread_ts} | metadata={metadata}"
        )
        if self._app is None:
            logger.error("Cannot send approval request: Slack app not initialized")
            raise RuntimeError("Slack app not initialized")

        # Build command display text
        if tool_name == "Bash":
            command = input_data.get("command", "")
            description = input_data.get("description", "")
            tool_info = f"`{command}`"
            if description:
                tool_info += f"\n_{description}_"
        else:
            # Generic tool input display
            tool_info_parts = [f"*{key}:* `{value}`" for key, value in input_data.items()]
            tool_info = "\n".join(tool_info_parts) if tool_info_parts else "_No input parameters_"

        # Fallback text for notifications
        fallback_text = f"🔐 Tool approval required: {tool_name}"

        # Slack Block Kit format
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🔐 Tool Approval Required",
                    "emoji": True,
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tool:*\n`{tool_name}`"},
                    {"type": "mrkdwn", "text": f"*Request ID:*\n`{request_id[:8]}...`"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Details:*\n{tool_info}",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Choose an action:",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "✅ Approve",
                            "emoji": True,
                        },
                        "style": "primary",
                        "value": f"approve_{request_id}",
                        "action_id": f"approval_approve_{request_id}",
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "🚫 Deny",
                            "emoji": True,
                        },
                        "style": "danger",
                        "value": f"deny_{request_id}",
                        "action_id": f"approval_deny_{request_id}",
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"💡 Tip: Use `!approve {request_id}` or `!deny {request_id}` "
                            "(all `/` commands support `!` prefix)"
                        ),
                    }
                ],
            },
        ]

        try:
            kwargs: dict[str, Any] = {
                "channel": chat_id,
                "text": fallback_text,
                "blocks": blocks,
            }
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            await self._app.client.chat_postMessage(**kwargs)
        except Exception as exc:
            logger.error(f"Failed to send approval request to Slack: {exc}")

    # ------------------------------------------------------------------
    # Sending helpers
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _send_text(
        self,
        channel_id: str,
        text: str,
        thread_ts: str | None = None,
    ) -> None:
        """Send a text message to a Slack channel."""
        if not self._app:
            return

        try:
            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "text": text,
            }
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            await self._app.client.chat_postMessage(**kwargs)
        except Exception as exc:
            logger.error(f"Failed to send Slack message: {exc}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _add_reaction(self, channel: str, timestamp: str, emoji: str) -> None:
        """Add a reaction emoji to a message."""
        if not self._app:
            return

        try:
            await self._app.client.reactions_add(
                channel=channel,
                timestamp=timestamp,
                name=emoji,
            )
        except Exception as exc:
            # Extract Slack API error code if available
            slack_error = None
            if hasattr(exc, "response") and isinstance(exc.response, dict):
                slack_error = exc.response.get("error")

            # Silently ignore common non-critical errors
            if slack_error in ["already_reacted", "no_reaction"]:
                return

            # Log actionable errors
            if slack_error == "missing_scope":
                logger.warning("Reaction failed: missing 'reactions:write' scope")
            else:
                logger.debug(f"Failed to add reaction '{emoji}': {slack_error or exc}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _remove_reaction(self, channel: str, timestamp: str, emoji: str) -> None:
        """Remove a reaction emoji from a message."""
        if not self._app:
            return

        try:
            await self._app.client.reactions_remove(
                channel=channel,
                timestamp=timestamp,
                name=emoji,
            )
        except Exception as exc:
            # Extract Slack API error code if available
            slack_error = None
            if hasattr(exc, "response") and isinstance(exc.response, dict):
                slack_error = exc.response.get("error")

            # Silently ignore common non-critical errors
            if slack_error in ["no_reaction", "already_reacted"]:
                return

            # Log actionable errors
            if slack_error == "missing_scope":
                logger.warning("Reaction failed: missing 'reactions:write' scope")
            else:
                logger.debug(f"Failed to remove reaction '{emoji}': {slack_error or exc}")

    async def _swap_reaction(self, context_id: str) -> None:
        """Swap processing reaction (👀) for complete reaction (✅)."""
        tracking = self._reaction_tracking.pop(context_id, None)
        if not tracking:
            return

        channel, timestamp = tracking
        await self._remove_reaction(channel, timestamp, self._config.reaction_processing)
        await self._add_reaction(channel, timestamp, self._config.reaction_complete)

    # ------------------------------------------------------------------
    # Inbound message handling
    # ------------------------------------------------------------------

    async def _on_message(self, event: dict) -> None:
        """Route an incoming Slack message to the command router or bus."""
        # Ignore bot messages
        if event.get("bot_id") or event.get("bot_profile"):
            return

        # Ignore message subtypes except file_share
        if event.get("subtype") and event.get("subtype") not in ("file_share",):
            return

        user_id = event.get("user", "")
        channel_id = event.get("channel", "")
        text = event.get("text", "")
        message_ts = event.get("ts", "")
        # DMs don't need thread replies; channel mentions always reply in thread
        is_dm = event.get("channel_type") == "im"
        thread_ts = None if is_dm else (event.get("thread_ts") or event.get("ts"))

        # In non-DM channels, ignore messages that don't mention the bot
        if not is_dm:
            if self._bot_user_id and f"<@{self._bot_user_id}>" not in (event.get("text") or ""):
                return

        # Strip bot mention markup from app_mention events
        if self._bot_user_id:
            text = re.sub(rf"<@{re.escape(self._bot_user_id)}>\s*", "", text).strip()

        if not user_id or not channel_id:
            logger.debug("Slack message dropped: incomplete event data")
            return

        # Add 👀 reaction immediately to signal "processing"
        if self._config.reaction_feedback_enabled and message_ts:
            self._reaction_tracking[channel_id] = (channel_id, message_ts)
            await self._add_reaction(channel_id, message_ts, self._config.reaction_processing)

        # Get user info for username (with in-memory cache to avoid rate limits)
        username = self._user_cache.get(user_id, "")
        if not username:
            try:
                if self._app:
                    user_info = await self._app.client.users_info(user=user_id)
                    username = user_info.get("user", {}).get("name", "")
                    if username:
                        self._user_cache[user_id] = username
            except Exception as exc:
                logger.debug(f"Failed to fetch Slack user info: {exc}")

        # Check allow_from whitelist
        if not self._is_allowed(user_id, username):
            logger.warning(
                f"Slack user {user_id} ({username}) not in allow_from — dropping message"
            )
            try:
                await self._send_text(
                    channel_id,
                    "Sorry, you are not authorized to use this bot.",
                    thread_ts=thread_ts,
                )
            except Exception as exc:
                logger.debug(f"Failed to send 'not authorized' reply: {exc}")
            return
        # Thread-scoped context for channels, channel-scoped for DMs
        if is_dm:
            context_id = channel_id
        elif thread_ts:
            context_id = f"{channel_id}:{thread_ts}"
        else:
            context_id = f"{channel_id}:{message_ts}"

        # -- Command handling (/start, /help, /reset, /cron) --
        stripped = text.strip()
        if stripped.startswith("/") or stripped.startswith("!"):
            parts = stripped.split()
            cmd = parts[0].lstrip("/!").lower() if parts else ""
            args = parts[1:] if len(parts) > 1 else []

            if cmd and self._command_router is not None:
                # Build metadata including thread_ts for threaded responses
                cmd_metadata = {
                    "platform": "slack",
                    "username": username,
                    "message_ts": message_ts,
                }
                if thread_ts:
                    cmd_metadata["thread_ts"] = thread_ts

                # Special handling for /claude run: add thread history to metadata
                if cmd == "claude" and args and args[0] == "run" and thread_ts:
                    thread_history = await self._get_thread_history(channel_id, thread_ts)
                    if thread_history:
                        cmd_metadata["thread_context"] = thread_history
                        logger.debug(
                            f"Added thread history to metadata for /claude run command "
                            f"(thread_ts={thread_ts})"
                        )

                ctx = CommandContext(
                    channel=self.name,
                    user_id=user_id,
                    context_id=context_id,
                    chat_id=channel_id,
                    args=args,
                    display_name=username or user_id,
                    metadata=cmd_metadata,
                )
                response = await self._command_router.dispatch(cmd, ctx)
                if response == "":
                    return
                try:
                    await self._send_text(channel_id, response, thread_ts=thread_ts)
                except Exception as exc:
                    logger.error(f"Failed to send command response: {exc}")

                # Update reaction: 👀 → ✅ for command responses
                if self._config.reaction_feedback_enabled:
                    await self._swap_reaction(channel_id)
                return

        if self._bus is None:
            return

        # -- Attachment handling --
        from langclaw.bus.base import Attachment

        try:
            import aiohttp
        except ImportError:
            aiohttp = None  # type: ignore

        content_parts = [text] if text else []
        msg_attachments: list[Attachment] = []
        media_dir = Path.home() / ".langclaw" / "media"

        files = event.get("files", [])
        for file_info in files:
            file_size = file_info.get("size", 0)
            if file_size > MAX_ATTACHMENT_BYTES:
                content_parts.append(f"[attachment: {file_info.get('name', 'file')} - too large]")
                continue

            # Download file
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                file_name = file_info.get("name", "unknown")
                file_id = file_info.get("id", "")
                file_path = media_dir / f"{file_id}_{file_name.replace('/', '_')}"

                # Get download URL (private URL requires auth)
                url_private = file_info.get("url_private")
                if url_private and self._app and aiohttp:
                    headers = {"Authorization": f"Bearer {self._config.bot_token}"}
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url_private, headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                await asyncio.to_thread(file_path.write_bytes, data)
                                msg_attachments.append(
                                    make_attachment(
                                        file_path=file_path,
                                        filename=file_name,
                                        size=file_size,
                                    )
                                )
                            else:
                                content_parts.append(f"[attachment: {file_name} - download failed]")
            except Exception as exc:
                logger.warning(f"Failed to download Slack attachment: {exc}")
                file_name = file_info.get("name", "file")
                content_parts.append(f"[attachment: {file_name} - download failed]")

        # Fetch thread history if this is a thread message
        # Store in metadata instead of content to bypass content filter
        metadata = {
            "platform": "slack",
            "username": username,
            "thread_ts": thread_ts,
            "message_ts": message_ts,
        }
        if thread_ts:
            thread_context = await self._get_thread_history(channel_id, thread_ts)
            if thread_context:
                metadata["thread_context"] = thread_context

        await self._bus.publish(
            InboundMessage(
                channel=self.name,
                user_id=user_id,
                context_id=context_id,
                chat_id=channel_id,
                content="\n".join(p for p in content_parts if p) or "[empty message]",
                origin="channel",
                attachments=msg_attachments,
                metadata=metadata,
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _register_slash_command(self, app: Any, name: str, description: str) -> None:
        """Register a slash command handler."""

        @app.command(f"/{name}")
        async def handler(ack: Any, command: dict) -> None:
            await ack()
            await self._handle_slash_command(command, name)

    async def _handle_slash_command(self, command: dict, cmd_name: str) -> None:
        """Bridge a Slack slash command to the CommandRouter."""
        if self._command_router is None:
            return

        user_id = command.get("user_id", "")
        channel_id = command.get("channel_id", "")
        text = command.get("text", "")
        args = text.split() if text else []

        # Get username
        username = command.get("user_name", "")

        # Build metadata for slash command
        cmd_metadata = {
            "platform": "slack",
            "username": username,
        }

        ctx = CommandContext(
            channel=self.name,
            user_id=user_id,
            context_id=channel_id,
            chat_id=channel_id,
            args=args,
            display_name=username or user_id,
            metadata=cmd_metadata,
        )
        response = await self._command_router.dispatch(cmd_name, ctx)

        # Send response
        try:
            if self._app:
                await self._app.client.chat_postMessage(
                    channel=channel_id,
                    text=response,
                )
        except Exception as exc:
            logger.error(f"Failed to send slash command response: {exc}")

    def _is_allowed(self, user_id: str, username: str | None) -> bool:
        """Return True if the user passes the allow_from whitelist check."""
        return is_allowed(self._config.allow_from, user_id, username)

    async def _get_thread_history(
        self,
        channel_id: str,
        thread_ts: str,
    ) -> dict[str, Any]:
        """
        Fetch Slack thread history with caching.

        Returns raw thread data for ThreadContextMiddleware to format.
        Caches results with TTL to avoid rate limiting.

        Args:
            channel_id: Slack channel ID
            thread_ts: Thread timestamp (unique thread identifier)

        Returns:
            Dict with thread context data, or empty dict if unavailable.
            Format: {
                "messages": [{"username": str, "content": str, "ts": str}, ...],
                "thread_id": str,
                "platform": "slack",
                "total_count": int,
            }
        """
        if not self._app or not self._config.thread_history_enabled:
            return {}

        # Check cache first
        async with self._cache_lock:
            cached = self._thread_history_cache.get(thread_ts)
            if cached and not cached.is_expired():
                logger.debug(f"Thread history cache hit for {thread_ts}")
                return self._build_thread_context_data(cached.messages, thread_ts)

        # Cache miss or expired - fetch from Slack API
        try:
            result = await self._app.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=self._config.thread_history_max_messages,
            )

            messages = result.get("messages", [])
            if not messages:
                return {}

            # Update cache
            async with self._cache_lock:
                self._thread_history_cache[thread_ts] = ThreadHistoryCache(
                    messages=messages,
                    cached_at=time.time(),
                    ttl=self._config.thread_history_cache_ttl,
                )
                # Clean up expired entries (opportunistic cleanup)
                expired_keys = [k for k, v in self._thread_history_cache.items() if v.is_expired()]
                for key in expired_keys:
                    del self._thread_history_cache[key]
                if expired_keys:
                    logger.debug(f"Cleaned up {len(expired_keys)} expired thread cache entries")

            logger.info(f"Fetched thread history for {thread_ts} ({len(messages)} messages)")

            # Pre-fetch usernames for all users in thread to populate cache
            await self._populate_username_cache(messages)

            return self._build_thread_context_data(messages, thread_ts)

        except Exception as exc:
            logger.warning(f"Failed to fetch thread history for {thread_ts}: {exc}")
            return {}

    async def _populate_username_cache(self, messages: list[dict[str, Any]]) -> None:
        """
        Pre-fetch usernames for all unique users in messages to populate cache.

        This ensures usernames are displayed correctly in thread context instead
        of showing user IDs like U0920QF8G15.
        """
        if not self._app:
            return

        # Get unique user IDs that aren't already cached
        user_ids_to_fetch = {
            msg.get("user")
            for msg in messages
            if msg.get("user") and not msg.get("bot_id") and msg.get("user") not in self._user_cache
        }

        # Fetch usernames in parallel (with rate limiting consideration)
        for user_id in user_ids_to_fetch:
            try:
                user_info = await self._app.client.users_info(user=user_id)
                username = user_info.get("user", {}).get("name", user_id)
                self._user_cache[user_id] = username
                logger.debug(f"Cached username for {user_id}: {username}")
            except Exception as exc:
                logger.debug(f"Failed to fetch username for {user_id}: {exc}")
                # Cache the user_id as fallback to avoid repeated failures
                self._user_cache[user_id] = user_id

    def _build_thread_context_data(
        self, messages: list[dict[str, Any]], thread_ts: str
    ) -> dict[str, Any]:
        """
        Build standardized thread context data structure.

        Filters out:
        - Bot messages (self._bot_user_id or bot_id field)
        - Duplicate content (keeps most recent)

        Args:
            messages: Raw Slack message objects
            thread_ts: Thread timestamp

        Returns:
            Standardized thread context dict for ThreadContextMiddleware
        """
        if not messages:
            return {}

        normalized_messages = []
        seen_content: set[str] = set()
        filtered_count = {"bot": 0, "empty": 0, "duplicate": 0}

        for msg in messages:
            # Skip bot messages
            if msg.get("bot_id"):
                filtered_count["bot"] += 1
                logger.debug(f"Filtered bot message (bot_id): {msg.get('text', '')[:50]}")
                continue
            if self._bot_user_id and msg.get("user") == self._bot_user_id:
                filtered_count["bot"] += 1
                logger.debug(f"Filtered bot message (user_id): {msg.get('text', '')[:50]}")
                continue

            user_id = msg.get("user", "unknown")
            username = self._user_cache.get(user_id, user_id)
            text = msg.get("text", "")

            # Handle attachments
            attachment_info = self._format_attachments(msg)
            if attachment_info:
                text = f"{text} {attachment_info}" if text else attachment_info

            # Skip empty messages
            if not text.strip():
                filtered_count["empty"] += 1
                logger.debug(f"Filtered empty message from {username}")
                continue

            # Skip duplicate content (case-insensitive comparison)
            content_normalized = text.strip().lower()
            if content_normalized in seen_content:
                filtered_count["duplicate"] += 1
                logger.debug(f"Filtered duplicate message: {text[:50]}")
                continue
            seen_content.add(content_normalized)

            normalized_messages.append(
                {
                    "username": username,
                    "content": text,
                    "ts": msg.get("ts", ""),
                }
            )

        if filtered_count["bot"] or filtered_count["empty"] or filtered_count["duplicate"]:
            logger.debug(
                f"Thread {thread_ts} filtering: "
                f"bot={filtered_count['bot']}, "
                f"empty={filtered_count['empty']}, "
                f"duplicate={filtered_count['duplicate']}, "
                f"kept={len(normalized_messages)}/{len(messages)}"
            )

        return {
            "messages": normalized_messages,
            "thread_id": thread_ts,
            "platform": "slack",
            "total_count": len(messages),  # Original count before filtering
        }

    def _format_attachments(self, msg: dict[str, Any]) -> str:
        """
        Format Slack message attachments into a concise description.

        Handles:
        - files: File attachments (images, documents, etc.)
        - attachments: Rich text attachments (link previews, cards)

        Args:
            msg: Slack message dict

        Returns:
            Formatted attachment description, or empty string if no attachments
        """
        parts = []

        # Handle file attachments
        files = msg.get("files", [])
        if files:
            file_descriptions = []
            for file_info in files:
                name = file_info.get("name", "file")
                filetype = file_info.get("filetype", "")
                # Categorize by type
                if filetype in ("png", "jpg", "jpeg", "gif", "webp"):
                    file_descriptions.append(f"[image: {name}]")
                elif filetype in ("pdf", "doc", "docx", "txt", "md"):
                    file_descriptions.append(f"[document: {name}]")
                elif filetype in ("mp4", "mov", "avi"):
                    file_descriptions.append(f"[video: {name}]")
                elif filetype in ("mp3", "wav", "ogg"):
                    file_descriptions.append(f"[audio: {name}]")
                else:
                    file_descriptions.append(f"[file: {name}]")

            if file_descriptions:
                parts.append(" ".join(file_descriptions))

        # Handle rich text attachments (link previews, etc.)
        attachments = msg.get("attachments", [])
        if attachments:
            for att in attachments:
                # Try to extract meaningful info
                title = att.get("title", "")
                title_link = att.get("title_link", "")
                text = att.get("text", "")

                if title_link:
                    parts.append(f"[link: {title or title_link}]")
                elif title:
                    parts.append(f"[attachment: {title}]")
                elif text and len(text) < 50:
                    parts.append(f"[attachment: {text}]")

        return " ".join(parts)

    def _format_thread_history(self, messages: list[dict[str, Any]], thread_ts: str) -> str:
        """
        Format Slack thread messages into a readable context string.

        Prioritizes recent messages and enforces a total character limit.
        If the limit is exceeded, older messages are truncated or omitted.

        Args:
            messages: List of Slack message dicts
            thread_ts: Thread timestamp for reference

        Returns:
            Formatted string with thread context
        """
        if not messages:
            return ""

        max_total_chars = self._config.thread_history_max_chars
        max_single_msg_chars = 200  # Per-message truncation limit

        # Build formatted messages from newest to oldest
        formatted_messages: list[str] = []
        current_char_count = 0

        # Process messages in reverse order (newest first)
        for i, msg in enumerate(reversed(messages), 1):
            user_id = msg.get("user", "unknown")
            username = self._user_cache.get(user_id, user_id)
            text = msg.get("text", "")

            # Handle attachments (files, images, etc.)
            attachment_info = self._format_attachments(msg)
            if attachment_info:
                text = f"{text} {attachment_info}" if text else attachment_info

            # Truncate single message if too long
            if len(text) > max_single_msg_chars:
                text = text[:max_single_msg_chars] + "..."

            # Format message line
            msg_line = f"[{len(messages) - i + 1}] {username}: {text}"

            # Check if adding this message would exceed total limit
            if current_char_count + len(msg_line) + 1 > max_total_chars:  # +1 for newline
                # If this is the first message and it's still too long, truncate it
                if i == 1:
                    available_chars = max_total_chars - current_char_count - 20  # Reserve for "..."
                    if available_chars > 50:  # Only include if meaningful
                        truncated_line = msg_line[:available_chars] + "..."
                        formatted_messages.insert(0, truncated_line)
                        current_char_count += len(truncated_line)
                # Stop adding older messages
                break

            # Add message to the beginning (we're iterating in reverse)
            formatted_messages.insert(0, msg_line)
            current_char_count += len(msg_line) + 1  # +1 for newline

        # Build final output with header and footer
        total_messages = len(messages)
        included_messages = len(formatted_messages)
        omitted_messages = total_messages - included_messages

        header = [
            "=== Slack Thread Context ===",
            f"Thread ID: {thread_ts}",
            f"Messages in thread: {total_messages}",
        ]

        if omitted_messages > 0:
            header.append(
                f"Showing {included_messages} most recent (omitted {omitted_messages} older)"
            )

        header.append("")

        lines = header + formatted_messages + ["=== End Thread Context ===", ""]

        result = "\n".join(lines)

        logger.debug(
            f"Formatted thread history: {included_messages}/{total_messages} messages, "
            f"{len(result)} chars (limit: {max_total_chars})"
        )

        return result
