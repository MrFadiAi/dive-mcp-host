"""Chat conversation logger — writes human-readable markdown logs for debugging."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from dive_mcp_host.env import DIVE_CONFIG_DIR

logger = logging.getLogger(__name__)

_CHAT_LOG_DIR_NAME = "chat_logs"
_MAX_ARGS_LEN = 500
_MAX_RESULT_LEN = 2000


def _chat_log_dir() -> Path:
    """Return the chat log directory, creating it if needed."""
    d = DIVE_CONFIG_DIR / _CHAT_LOG_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ts() -> str:
    """Return a local-time timestamp string like ``14:30:25``."""
    return datetime.now(tz=UTC).astimezone().strftime("%H:%M:%S")


def _date_prefix() -> str:
    """Return today's date as ``YYYY-MM-DD``."""
    return datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d")


class ChatLogger:
    """Append-only, per-chat markdown logger.

    Each chat conversation gets its own file named
    ``{date}_{chat_id}.md`` inside ``<DIVE_CONFIG_DIR>/chat_logs/``.

    Usage::

        chat_log = ChatLogger(chat_id)
        chat_log.log_user("hello")
        chat_log.log_assistant("hi there!", model="gpt-4o")
        chat_log.log_tool_call("search_docs", {"query": "plc"})
        chat_log.log_tool_result("search_docs", "found 3 results")
        chat_log.log_error("context window exceeded")
    """

    def __init__(self, chat_id: str) -> None:
        """Initialize chat logger for a specific conversation."""
        self._chat_id = chat_id
        safe_id = chat_id.replace("/", "_").replace("\\", "_")
        self._path = _chat_log_dir() / f"{_date_prefix()}_{safe_id}.md"

    # -- internal helpers --------------------------------------------------

    def _append(self, text: str) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            logger.warning("Failed to write chat log to %s", self._path, exc_info=True)

    def _header(self) -> None:
        """Write a file header if the file is new/empty."""
        try:
            if self._path.exists() and self._path.stat().st_size > 0:
                return
        except OSError:
            pass
        now = (
            datetime.now(tz=UTC)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S %Z")
        )
        self._append(
            f"# Chat Log — `{self._chat_id}`\n\n"
            f"**Started:** {now}\n\n---\n\n"
        )

    # -- public API --------------------------------------------------------

    def log_user(self, content: str, *, files: list[str] | None = None) -> None:
        """Log a user message."""
        self._header()
        ts = _ts()
        parts = [f"## [{ts}] \U0001f464 User\n\n{content}\n\n"]
        if files:
            parts.append("**Files:**\n")
            for f in files:
                parts.append(f"- `{f}`\n")
            parts.append("\n")
        self._append("".join(parts))

    def log_assistant(
        self,
        content: str,
        *,
        model: str = "",
        tool_calls: list | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration_s: float = 0.0,
    ) -> None:
        """Log an assistant response."""
        self._header()
        ts = _ts()
        meta_parts: list[str] = []
        if model:
            meta_parts.append(f"**Model:** `{model}`")
        if tokens_in or tokens_out:
            meta_parts.append(f"**Tokens:** {tokens_in} in / {tokens_out} out")
        if duration_s > 0:
            meta_parts.append(f"**Duration:** {duration_s:.1f}s")

        parts = [f"## [{ts}] \U0001f916 Assistant\n\n"]
        if meta_parts:
            parts.append(" | ".join(meta_parts) + "\n\n")
        parts.append(f"{content}\n\n")

        if tool_calls:
            parts.append("### Tool Calls\n\n")
            for tc in tool_calls:
                name = tc.get("name", tc) if isinstance(tc, dict) else tc
                args = tc.get("args", {}) if isinstance(tc, dict) else {}
                if args:
                    args_str = json.dumps(args, default=str, ensure_ascii=False)
                    if len(args_str) > _MAX_ARGS_LEN:
                        args_str = args_str[:_MAX_ARGS_LEN] + "…"
                    parts.append(f"- **{name}** — `{args_str}`\n")
                else:
                    parts.append(f"- **{name}**\n")
            parts.append("\n")

        self._append("".join(parts))

    def log_tool_call(self, name: str, args: dict | None = None) -> None:
        """Log a tool call being dispatched."""
        ts = _ts()
        args_str = ""
        if args:
            args_str = json.dumps(args, default=str, ensure_ascii=False)
            if len(args_str) > _MAX_ARGS_LEN:
                args_str = args_str[:_MAX_ARGS_LEN] + "…"
        self._append(
            f"### [{ts}] \U0001f527 Tool Call: `{name}`\n\n"
            f"```json\n{args_str}\n```\n\n"
        )

    def log_tool_result(
        self, name: str, result: str, *, is_error: bool = False
    ) -> None:
        """Log a tool result."""
        ts = _ts()
        icon = "\U0001f6ab" if is_error else "\U0001f4cb"
        # Truncate very long results
        truncated = len(result) > _MAX_RESULT_LEN
        display = result[:_MAX_RESULT_LEN] + ("…\n" if truncated else "")
        self._append(
            f"### [{ts}] {icon} Tool Result: `{name}`\n\n"
            f"```\n{display}\n```\n\n"
        )

    def log_error(self, message: str) -> None:
        """Log an error that occurred during chat processing."""
        ts = _ts()
        self._append(f"## [{ts}] ❌ Error\n\n```\n{message}\n```\n\n")

    def log_retry(self, message_id: str) -> None:
        """Log that a retry/regenerate was triggered."""
        ts = _ts()
        self._append(
            f"## [{ts}] \U0001f504 Retry\n\n"
            f"Regenerated from message `{message_id}`\n\n"
        )

    @property
    def log_path(self) -> Path:
        """Return the path to the log file."""
        return self._path
