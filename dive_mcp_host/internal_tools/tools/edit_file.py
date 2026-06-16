"""Surgical file edit tool — find-and-replace within an existing file.

``write_file`` overwrites (or appends) a whole file, which forces the agent to
re-emit the entire file contents for a one-line tweak (slow + token-heavy on
large files). ``edit_file`` reads the file, applies a guarded find-and-replace,
and writes the result back — so the agent only sends the old/new snippets.

The pure ``_edit_text`` helper is the tested core (no I/O); the ``@tool`` is a
thin read → edit → write wrapper that mirrors ``write_file``'s abort +
elicitation + encoding-error handling.
"""

# ruff: noqa: PLR0911
# PLR0911: Many return statements needed for complex control flow

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig  # noqa: TC002
from langchain_core.tools import InjectedToolArg, tool
from mcp import types
from pydantic import Field

from dive_mcp_host.host.agents.agent_factory import (
    ensure_config,
    get_abort_signal,
    get_dry_run,
    get_stream_writer,
)
from dive_mcp_host.host.tools.elicitation_manager import (
    ElicitationManager,
    ElicitationTimeoutError,
)
from dive_mcp_host.internal_tools.events import InstallerToolLog
from dive_mcp_host.internal_tools.tools.common import check_aborted

logger = logging.getLogger(__name__)


def _edit_text(
    content: str, old_text: str, new_text: str, replace_all: bool = False
) -> tuple[str, int]:
    """Apply a guarded find-and-replace to ``content``.

    Returns ``(new_content, n_replacements)``. Raises ``ValueError`` when:
    - ``old_text`` is empty (a no-op would silently succeed), or
    - ``old_text`` is not found, or
    - ``old_text`` matches more than once but ``replace_all`` is False
      (an ambiguous edit is dangerous — the caller must be explicit).

    ``str.replace`` is non-recursive, so a ``new_text`` that itself contains
    ``old_text`` cannot loop; the count is taken from the original content.
    """
    if not old_text:
        raise ValueError("old_text must not be empty")

    count = content.count(old_text)
    if count == 0:
        raise ValueError(f"old_text not found in the file ({len(content)} chars)")
    if count > 1 and not replace_all:
        raise ValueError(
            f"old_text found {count} times; set replace_all=True to replace all "
            f"or narrow old_text to a unique snippet"
        )

    if replace_all:
        return content.replace(old_text, new_text), count
    return content.replace(old_text, new_text, 1), 1


@tool(
    description="""
Surgically edit an existing file by find-and-replace (no full rewrite).
Prefer this over write_file for small changes to large files.
Fails safely: errors if old_text is not found, or if old_text matches more than
once without replace_all=True (forces an unambiguous edit).
Always requests user approval before writing.
"""
)
async def edit_file(
    path: Annotated[str, Field(description="Path to the existing file to edit.")],
    old_text: Annotated[
        str,
        Field(
            description=(
                "The exact text to replace. Must be a unique substring of the "
                "file (unless replace_all=True)."
            )
        ),
    ],
    new_text: Annotated[
        str, Field(description="The text to substitute in place of old_text.")
    ],
    replace_all: Annotated[
        bool,
        Field(
            default=False,
            description="Replace every occurrence of old_text instead of requiring uniqueness.",
        ),
    ] = False,
    encoding: Annotated[
        str,
        Field(default="utf-8", description="File encoding."),
    ] = "utf-8",
    config: Annotated[RunnableConfig | None, InjectedToolArg] = None,
) -> str:
    """Edit a file in place via guarded find-and-replace.

    Requests user confirmation before writing (when an elicitation manager is
    configured; tests pass ``config={}`` so this is skipped).
    """
    config = ensure_config(config)

    stream_writer = get_stream_writer(config)
    dry_run = get_dry_run(config)
    abort_signal = get_abort_signal(config)
    elicitation_manager: ElicitationManager | None = config.get(
        "configurable", {}
    ).get("elicitation_manager")

    if check_aborted(abort_signal):
        return "Error: Operation aborted."

    expanded_path = str(Path(path).expanduser())
    file_path = Path(expanded_path)

    if not file_path.is_file():
        return f"Error: File not found: {path}"

    # Read current content.
    try:
        content = file_path.read_text(encoding=encoding)
    except (OSError, UnicodeError, LookupError) as e:
        return f"Error reading file {path}: {e}"

    # Apply the guarded edit BEFORE prompting — surface not-found/ambiguous
    # early so the user is never asked to confirm a doomed write.
    try:
        new_content, count = _edit_text(content, old_text, new_text, replace_all)
    except ValueError as e:
        return f"Error: {e}"

    stream_writer(
        (
            InstallerToolLog.NAME,
            InstallerToolLog(
                tool="edit_file",
                action=f"{'[DRY RUN] ' if dry_run else ''}Editing: {path}",
                details={
                    "path": expanded_path,
                    "replacements": count,
                    "replace_all": replace_all,
                    "delta": len(new_content) - len(content),
                    "dry_run": dry_run,
                },
            ),
        )
    )

    if dry_run:
        return (
            f"[DRY RUN] Would make {count} replacement(s) in {path}\n"
            f"Simulated success."
        )

    # Request user confirmation before writing (mirrors write_file).
    if elicitation_manager is not None:
        # Cap the diff preview so a huge file can't flood the prompt.
        old_preview = old_text if len(old_text) <= 500 else old_text[:500] + "..."
        new_preview = new_text if len(new_text) <= 500 else new_text[:500] + "..."
        suffix = " (replace all)" if replace_all else ""
        confirm_message = (
            f"The edit_file tool wants to make {count} replacement{suffix} in:\n\n"
            f"**Path:** `{path}`\n\n"
            f"**Replace:**\n```\n{old_preview}\n```\n"
            f"**With:**\n```\n{new_preview}\n```"
        )
        confirm_schema: dict[str, Any] = {"type": "object", "properties": {}}
        params = types.ElicitRequestFormParams(
            message=confirm_message,
            requestedSchema=confirm_schema,
        )
        logger.info("Requesting user confirmation for edit_file: %s", path)
        try:
            result = await elicitation_manager.request(
                params=params,
                writer=stream_writer,
                abort_signal=abort_signal,
            )
            if result.action == "decline":
                return "Edit cancelled: User declined the replacement."
            if result.action != "accept":
                return "Edit cancelled: User cancelled the confirmation."
        except ElicitationTimeoutError:
            return "Error: Confirmation timed out. File not edited."
        except Exception as e:  # noqa: BLE001
            logger.exception("Error getting confirmation via elicitation")
            return f"Error getting confirmation: {e}"

    if check_aborted(abort_signal):
        return "Error: Operation aborted."

    # Write the edited content back.
    try:
        file_path.write_text(new_content, encoding=encoding)
        return (
            f"Successfully edited {path}: {count} replacement(s) "
            f"({len(content)} -> {len(new_content)} bytes)."
        )
    except (OSError, UnicodeError, LookupError) as e:
        return f"Error writing to file {path}: {e}"
