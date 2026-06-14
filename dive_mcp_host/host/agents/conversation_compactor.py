"""Auto-compacting for long conversations.

When the conversation approaches the model's context window limit,
this module summarizes older messages into a compact summary while
keeping recent messages intact. This is similar to how Claude Code
handles long conversations.

The compaction flow:
1. Check if total tokens exceed the budget threshold
2. Split messages into "old" (to summarize) and "recent" (to keep)
3. Call the LLM to produce a concise summary of old messages
4. Return [SystemMessage(summary)] + recent_messages
"""

import logging
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately

logger = logging.getLogger(__name__)

# Default context window sizes for known models (input tokens)
DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_000,
    "gpt-3.5-turbo": 16_000,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
}

# Fraction of context window to use as threshold before compacting
COMPACTION_THRESHOLD = 0.80

# Fraction of context window to reserve for recent messages (not summarized)
RECENT_WINDOW_RATIO = 0.30

# Maximum tokens for the summary itself
MAX_SUMMARY_TOKENS = 2_000

# System prompt for the summarization LLM call
SUMMARIZE_PROMPT = """\
You are a conversation compactor. Your job is to produce a concise summary of \
the conversation so far that preserves ALL important context for the AI assistant \
to continue the conversation seamlessly.

Rules:
- Preserve key facts, decisions, user preferences, and any explicitly stated requirements.
- Keep specific code references (file paths, function names, variable names, error messages).
- Keep tag names, PLC block names, HMI screen names, and other domain-specific identifiers.
- Note any tools that were called and their key results.
- Do NOT include pleasantries or conversational filler.
- Be concise but complete — this summary replaces the original messages.
- Write in third person, factual style.
- If there were errors or problems encountered, note them and their resolution status.
"""

MAX_SUMMARY_PROMPT = """\
Summarize the following conversation. Focus on facts, decisions, code references, \
tag names, and technical details. Be concise (under 500 words).
"""


def get_context_window(model_name: str, configured_window: int | None = None) -> int:
    """Get the context window size for a model.

    Args:
        model_name: The model name (e.g. "gpt-4o", "claude-3-5-sonnet").
        configured_window: User-configured context window override.

    Returns:
        Context window size in tokens.
    """
    if configured_window and configured_window > 0:
        return configured_window

    # Try exact match
    if model_name in DEFAULT_CONTEXT_WINDOWS:
        return DEFAULT_CONTEXT_WINDOWS[model_name]

    # Explicit context-window marker, e.g. "glm-5.2[1m]" → 1,000,000 tokens.
    # The marker is stripped before the API call (see models/__init__.py) but
    # is kept here so the compactor knows the real window size.
    import re

    marker = re.search(r"\[(\d+)\s*([kKmM])\]", model_name)
    if marker:
        num = int(marker.group(1))
        unit = marker.group(2).lower()
        return num * (1_000 if unit == "k" else 1_000_000)

    # Try prefix match for model families
    model_lower = model_name.lower()
    if "gpt-4o" in model_lower:
        return 128_000
    if "gpt-4" in model_lower:
        return 128_000
    if "gpt-3.5" in model_lower:
        return 16_000
    if "claude-3-5" in model_lower or "claude-3.5" in model_lower:
        return 200_000
    if "claude-3" in model_lower:
        return 200_000
    if "claude-opus" in model_lower or "claude-sonnet" in model_lower:
        return 200_000
    if "glm-4" in model_lower or "glm-5" in model_lower:
        return 128_000
    if "deepseek" in model_lower:
        return 128_000
    if "o1-" in model_lower or "o3-" in model_lower:
        return 200_000
    if "gemini" in model_lower:
        return 1_000_000
    if "llama" in model_lower:
        return 128_000
    if "mistral" in model_lower or "codestral" in model_lower:
        return 128_000
    if "qwen" in model_lower:
        return 128_000

    # Safe default
    return 128_000


class CompactionResult:
    """Result of a compaction operation."""

    def __init__(
        self,
        messages: list[BaseMessage],
        compacted: bool,
        summary: str | None = None,
        messages_removed: int = 0,
        tokens_before: int = 0,
        tokens_after: int = 0,
    ) -> None:
        self.messages = messages
        self.compacted = compacted
        self.summary = summary
        self.messages_removed = messages_removed
        self.tokens_before = tokens_before
        self.tokens_after = tokens_after


def find_safe_cut_point(
    messages: list[BaseMessage],
    target_index: int,
) -> int:
    """Find a safe point to split the message list for summarization.

    We must not split in the middle of a tool call / tool response pair.
    We also must not split in the middle of a multi-part AI message with
    tool calls.

    Args:
        messages: The full message list.
        target_index: Desired split point (messages before this are summarized).

    Returns:
        A safe index to split at (all messages before this index form complete pairs).
    """
    # Walk backwards from target_index to find a safe HumanMessage boundary
    for i in range(target_index, 0, -1):
        msg = messages[i]
        # Safe to cut before a HumanMessage — it starts a new turn
        if isinstance(msg, HumanMessage):
            return i

    # Fallback: if we can't find a safe cut, use target_index
    # (this shouldn't happen in normal conversations)
    return target_index


async def compact_conversation(
    messages: list[BaseMessage],
    model: BaseChatModel,
    *,
    context_window: int = 128_000,
    threshold: float = COMPACTION_THRESHOLD,
    configured_window: int | None = None,
) -> CompactionResult:
    """Check if conversation needs compacting and compact if needed.

    Args:
        messages: Current conversation messages.
        model: The LLM to use for summarization.
        context_window: Context window size for the model.
        threshold: Fraction of context window that triggers compaction.
        configured_window: User-configured override for context window.

    Returns:
        CompactionResult with compacted messages and metadata.
    """
    if configured_window and configured_window > 0:
        context_window = configured_window

    total_tokens = count_tokens_approximately(messages)
    budget = int(context_window * threshold)

    logger.debug(
        "Compaction check: %d tokens used, budget is %d (window=%d, threshold=%.0f%%)",
        total_tokens,
        budget,
        context_window,
        threshold * 100,
    )

    # No compaction needed
    if total_tokens <= budget:
        return CompactionResult(
            messages=messages,
            compacted=False,
            tokens_before=total_tokens,
            tokens_after=total_tokens,
        )

    logger.info(
        "Compacting conversation: %d tokens exceeds budget of %d",
        total_tokens,
        budget,
    )

    # Calculate how many tokens to keep for recent messages
    recent_budget = int(context_window * RECENT_WINDOW_RATIO)
    target_total = budget - MAX_SUMMARY_TOKENS  # Leave room for the summary

    # Find the split point: walk backwards to find where recent messages start
    recent_tokens = 0
    recent_start = len(messages)

    for i in range(len(messages) - 1, -1, -1):
        msg_tokens = count_tokens_approximately([messages[i]])
        if recent_tokens + msg_tokens > recent_budget:
            break
        recent_tokens += msg_tokens
        recent_start = i

    # Find a safe cut point (don't split tool call/response pairs)
    cut_point = find_safe_cut_point(messages, recent_start)

    old_messages = messages[:cut_point]
    recent_messages = messages[cut_point:]

    if not old_messages:
        logger.warning("Compaction needed but no messages to summarize")
        return CompactionResult(
            messages=messages,
            compacted=False,
            tokens_before=total_tokens,
            tokens_after=total_tokens,
        )

    logger.info(
        "Summarizing %d old messages, keeping %d recent messages",
        len(old_messages),
        len(recent_messages),
    )

    # Build summarization prompt
    summarize_messages: list[BaseMessage] = [
        SystemMessage(content=SUMMARIZE_PROMPT),
        HumanMessage(content=_build_summarize_input(old_messages)),
    ]

    # Call LLM to summarize
    try:
        response = await model.ainvoke(summarize_messages)
        summary_text = response.content if isinstance(response.content, str) else str(response.content)
    except Exception:
        logger.exception("Failed to summarize conversation, falling back to trim")
        # Fallback: just use the recent messages without a summary
        result_tokens = count_tokens_approximately(recent_messages)
        return CompactionResult(
            messages=recent_messages,
            compacted=True,
            summary="(Context was trimmed due to summarization failure)",
            messages_removed=len(old_messages),
            tokens_before=total_tokens,
            tokens_after=result_tokens,
        )

    # Build the compacted message list
    summary_msg = SystemMessage(
        content=f"<conversation_summary>\n{summary_text}\n</conversation_summary>",
        name="compaction_summary",
    )
    compacted = [summary_msg, *recent_messages]
    result_tokens = count_tokens_approximately(compacted)

    logger.info(
        "Compaction complete: %d → %d tokens (removed %d messages)",
        total_tokens,
        result_tokens,
        len(old_messages),
    )

    return CompactionResult(
        messages=compacted,
        compacted=True,
        summary=summary_text,
        messages_removed=len(old_messages),
        tokens_before=total_tokens,
        tokens_after=result_tokens,
    )


def _build_summarize_input(messages: list[BaseMessage]) -> str:
    """Build a text representation of messages for the summarization LLM.

    Converts structured messages into a readable format while preserving
    key information like tool calls and their results.
    """
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            # Skip compaction summaries from previous compactions
            if msg.name == "compaction_summary":
                parts.append(f"[Previous summary: {msg.content}]")
            else:
                parts.append(f"[System: {_truncate(msg.content, 200)}]")
        elif isinstance(msg, HumanMessage):
            text = _extract_text(msg.content)
            parts.append(f"User: {_truncate(text, 2000)}")
        elif isinstance(msg, AIMessage):
            # Include tool calls
            if msg.tool_calls:
                for tc in msg.tool_calls[:5]:  # Limit tool calls shown
                    name = tc.get("name", "unknown")
                    args = str(tc.get("args", {}))[:300]
                    parts.append(f"Assistant called {name}({args})")
            if isinstance(msg.content, str) and msg.content.strip():
                parts.append(f"Assistant: {_truncate(msg.content, 1000)}")
        elif isinstance(msg, ToolMessage):
            result = _truncate(msg.content if isinstance(msg.content, str) else str(msg.content), 500)
            parts.append(f"Tool result ({msg.name}): {result}")

    conversation = "\n\n".join(parts)
    return f"{MAX_SUMMARY_PROMPT}\n\n---\nConversation:\n{conversation}"


def _extract_text(content: str | list) -> str:
    """Extract text from message content (may be string or list of parts)."""
    if isinstance(content, str):
        return content
    texts = []
    for item in content:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            texts.append(item.get("text", ""))
    return "".join(texts)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len with ellipsis indicator."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [{len(text):,} chars total]"
