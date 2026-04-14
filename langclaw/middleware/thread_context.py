"""
ThreadContextMiddleware — injects thread history from metadata into messages.

Runs AFTER ContentFilterMiddleware to ensure thread history bypasses content filtering
while still being provided to the LLM for context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langgraph.runtime import Runtime
from loguru import logger

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


class ThreadContextMiddleware(AgentMiddleware):
    """
    Injects thread context from metadata into the conversation.

    This middleware uses before_model hook to run AFTER ContentFilterMiddleware.before_model,
    ensuring:
    1. User input is validated by content filter (in before_agent and before_model)
    2. Thread history (stored in metadata) is injected AFTER all content checks
    3. LLM receives complete context (user input + thread history)

    The thread_context is extracted from runtime.context.metadata["thread_context"]
    and prepended to the latest human message.

    Args:
        max_messages: Maximum number of messages to include (default: 10)
        max_chars_per_message: Max characters per individual message (default: 300)
        max_total_chars: Max total characters for entire context (default: 2000)
    """

    def __init__(
        self,
        max_messages: int = 10,
        max_chars_per_message: int = 500,
        max_total_chars: int = 3000,
    ) -> None:
        super().__init__()
        self.max_messages = max_messages
        self.max_chars_per_message = max_chars_per_message
        self.max_total_chars = max_total_chars

    def _format_thread_context(self, thread_data: dict[str, Any]) -> str:
        """
        Format thread context data into LLM-friendly text.

        Format:
        ```
        <thread-context platform="slack" id="123">
        Recent messages (showing N of M):

        @alice: Hello world
        @bob: Hi there! How can I help?
        @alice: I need help with X
        </thread-context>
        ```

        Args:
            thread_data: Dict with keys: messages, thread_id, platform, total_count

        Returns:
            Formatted context string
        """
        messages = thread_data.get("messages", [])
        thread_id = thread_data.get("thread_id", "unknown")
        platform = thread_data.get("platform", "unknown")
        total_count = thread_data.get("total_count", len(messages))

        if not messages:
            return ""

        # Limit messages to max_messages (newest first)
        limited_messages = messages[-self.max_messages :]
        included_count = len(limited_messages)
        omitted_count = total_count - included_count

        # Format each message
        formatted_lines = []
        total_chars = 0

        for msg in limited_messages:
            username = msg.get("username", "unknown")
            content = msg.get("content", "")

            # Truncate individual message if too long
            if len(content) > self.max_chars_per_message:
                content = content[: self.max_chars_per_message] + "..."

            line = f"@{username}: {content}"

            # Check if adding this line would exceed total limit
            if total_chars + len(line) + 1 > self.max_total_chars:
                break

            formatted_lines.append(line)
            total_chars += len(line) + 1  # +1 for newline

        # Build final output
        header = f'<thread-context platform="{platform}" id="{thread_id}">'
        if omitted_count > 0:
            summary = (
                f"Recent messages (showing {included_count} of {total_count}, "
                f"{omitted_count} omitted):"
            )
        else:
            summary = f"Recent messages ({included_count} total):"

        footer = "</thread-context>"

        parts = [header, summary, ""] + formatted_lines + ["", footer]
        return "\n".join(parts)

    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Inject thread context into the latest human message before model call."""
        ctx = getattr(runtime, "context", None)
        if ctx is None:
            return None

        # Check if thread_context exists in metadata
        thread_data = ctx.metadata.get("thread_context")
        if not thread_data:
            return None

        # Check if we've already injected (avoid re-injection on retries)
        if ctx.metadata.get("_thread_context_injected"):
            return None

        messages = state.get("messages", [])
        if not messages:
            return None

        # Find the latest human message
        latest_msg: BaseMessage | None = None
        latest_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if getattr(msg, "type", None) == "human":
                latest_msg = msg
                latest_idx = idx
                break

        if latest_msg is None:
            return None

        # Format thread context
        formatted_context = self._format_thread_context(thread_data)
        if not formatted_context:
            return None

        # Prepend thread_context to the message content
        original_content = latest_msg.content

        # Check if thread context already injected (by checking for marker)
        if isinstance(original_content, str) and "<thread-context" in original_content:
            return None  # Already injected, skip

        if isinstance(original_content, str):
            # Simple string content
            new_content = f"{formatted_context}\n\n{original_content}"
        elif isinstance(original_content, list):
            # Multi-modal content (list of content blocks)
            # Check if first block is thread context
            if (
                original_content
                and isinstance(original_content[0], dict)
                and "<thread-context" in str(original_content[0].get("text", ""))
            ):
                return None  # Already injected, skip
            new_content = [{"type": "text", "text": formatted_context}, *original_content]
        else:
            logger.warning(
                f"Unexpected message content type: {type(original_content)}, "
                "skipping thread context injection"
            )
            return None

        # Create updated message with thread context
        updated_msg = latest_msg.model_copy()
        updated_msg.content = new_content

        # Replace the message in the list
        updated_messages = list(messages)
        updated_messages[latest_idx] = updated_msg

        # Mark as injected in metadata to prevent re-injection
        ctx.metadata["_thread_context_injected"] = True

        logger.debug(
            f"Injected thread context | platform={thread_data.get('platform')} | "
            f"messages={len(thread_data.get('messages', []))} | "
            f"formatted_length={len(formatted_context)}"
        )

        return {"messages": updated_messages}


__all__ = ["ThreadContextMiddleware"]
