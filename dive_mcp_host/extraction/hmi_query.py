"""Pure helpers to answer follow-up queries against a cached ``HmiExtraction``.

Langchain-free so it is unit-testable in isolation; the ``query_hmi_screens``
tool (``internal_tools/tools/query_hmi.py``) is a thin wrapper. Mirrors
``extraction/query.py`` for PLC — closes the gap that ``extract_hmi_screens``
caches a rich result but nothing could read it, so follow-ups re-parsed the
whole HMI project.
"""

from __future__ import annotations

from dive_mcp_host.extraction.models import HmiExtraction, ScreenResult

_VALID_DETAILS = ("summary", "navigation", "screens", "screen", "tag", "orphans")

# Output caps to avoid flooding the agent's context.
_MAX_SCREEN_ELEMENTS = 50
_MAX_SUGGESTIONS = 50
_MAX_ONLOADED_CHARS = 4000


def _find_screen(extraction: HmiExtraction, name: str) -> ScreenResult | None:
    """Find a screen by name, exact match first then case-insensitive."""
    for screen in extraction.screens:
        if screen.screen_name == name:
            return screen
    name_lower = name.lower()
    for screen in extraction.screens:
        if screen.screen_name.lower() == name_lower:
            return screen
    return None


def _available_screens(extraction: HmiExtraction) -> str:
    names = [s.screen_name for s in extraction.screens[:_MAX_SUGGESTIONS]]
    return ", ".join(names) if names else "none"


def _available_tags(extraction: HmiExtraction) -> str:
    names = sorted(extraction.plc_tag_index)[:_MAX_SUGGESTIONS]
    return ", ".join(names) if names else "none"


def _format_hmi_tag(name: str, info: object) -> str:
    """Render an HMI tag index entry.

    ``hmi_parser`` produces ``{plc_tag, data_type, connection,
    used_in_screens: [...]}``; render that cleanly instead of a list repr.
    """
    lines = [f"## HMI tag: {name}"]
    if not isinstance(info, dict):
        lines.append(str(info))
        return "\n".join(lines)

    used_in = info.get("used_in_screens") or info.get("used_in")
    if isinstance(used_in, list):
        joined = ", ".join(used_in) if used_in else "(none)"
        lines.append(f"**Used in screens ({len(used_in)}):** {joined}")

    plc_tag = info.get("plc_tag")
    if plc_tag:
        lines.append(f"**PLC tag:** {plc_tag}")
    dtype = info.get("data_type")
    if dtype:
        lines.append(f"**Data type:** {dtype}")
    connection = info.get("connection")
    if connection:
        lines.append(f"**Connection:** {connection}")

    already_rendered = {
        "used_in_screens",
        "used_in",
        "plc_tag",
        "data_type",
        "connection",
    }
    for key, value in info.items():
        if key not in already_rendered:
            lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _format_screen(screen: ScreenResult) -> str:
    """Render a single screen: elements, tag bindings, events, navigation."""
    lines = [f"## Screen: {screen.screen_name} ({screen.file})"]
    lines.append(f"**Elements:** {screen.element_count}")

    if screen.screen_navigations:
        lines.append(f"**Navigates to:** {', '.join(screen.screen_navigations)}")

    if screen.on_loaded_event:
        body = screen.on_loaded_event.strip()
        if len(body) > _MAX_ONLOADED_CHARS:
            body = body[:_MAX_ONLOADED_CHARS] + "\n... (truncated)"
        lines.append("**OnLoaded:**")
        lines.append(f"```\n{body}\n```")

    shown = 0
    for elem in screen.elements:
        if shown >= _MAX_SCREEN_ELEMENTS:
            remaining = len(screen.elements) - shown
            lines.append(f"_...and {remaining} more elements_")
            break
        shown += 1
        head = f"- **{elem.name}** ({elem.type})"
        if elem.io_role:
            head += f" — {elem.io_role}"
        lines.append(head)
        for binding in elem.tag_bindings:
            plc = binding.plc_tag or binding.plc_name or "?"
            lines.append(f"    - tag: {binding.hmi_tag} -> {plc} ({binding.data_type})")
        for event in elem.events:
            label = event.event_type or event.function or "event"
            tail = ""
            if event.navigates_to:
                tail = f" -> {', '.join(event.navigates_to)}"
            elif event.plc_tags:
                tail = f" tags: {', '.join(event.plc_tags)}"
            lines.append(f"    - event: {label}{tail}")

    if screen.plc_tags_referenced:
        lines.append(
            f"**PLC tags referenced:** {', '.join(screen.plc_tags_referenced)}"
        )
    return "\n".join(lines)


def format_hmi_query(
    extraction: HmiExtraction, detail: str, name: str | None = None
) -> str:
    """Format one follow-up query against a cached ``HmiExtraction``.

    Args:
        extraction: the cached HMI extraction result.
        detail: one of ``summary``, ``navigation``, ``screens``, ``screen``,
            ``tag``.
        name: screen name (for ``screen``) or tag name (for ``tag``).

    Returns:
        A formatted string. Never raises — unknown detail, missing name, and
        not-found all return a clean message.
    """
    normalized = (detail or "").strip().lower()
    if normalized not in _VALID_DETAILS:
        return (
            f"Error: Unknown detail '{detail}'. "
            f"Valid: {', '.join(_VALID_DETAILS)}."
        )

    if normalized == "summary":
        s = extraction.summary
        return "\n".join(
            [
                "## HMI extraction summary",
                f"Screens: {s.total_screens}",
                f"Elements: {s.total_elements} "
                f"({s.total_elements_with_events} with events, "
                f"{s.total_tag_bindings} tag bindings)",
                f"JS functions: {s.total_js_functions}",
                f"Unique PLC tags: {s.total_unique_plc_tags}",
                f"Navigation links: {s.total_navigation_links}",
            ]
        )

    if normalized == "navigation":
        if not extraction.navigation_map:
            return "No navigation links recorded in this extraction."
        lines = ["## Screen navigation (screen -> targets)"]
        for source in sorted(extraction.navigation_map):
            targets = extraction.navigation_map[source] or []
            lines.append(
                f"- {source} -> {', '.join(targets) if targets else '(none)'}"
            )
        return "\n".join(lines)

    if normalized == "screens":
        if not extraction.screens:
            return "No screens in this extraction."
        lines = [f"## Screens ({len(extraction.screens)})"]
        for screen in extraction.screens[:_MAX_SUGGESTIONS]:
            nav = f", nav: {', '.join(screen.screen_navigations)}" if screen.screen_navigations else ""
            lines.append(f"- {screen.screen_name}: {screen.element_count} elements{nav}")
        return "\n".join(lines)

    if normalized == "orphans":
        # Screens no other screen navigates to (zero inbound links) =
        # potentially unreachable / dead-end entry points. Parallel to PLC
        # dead_code. (The legitimate start screen also has no inbound link —
        # the note flags it so a human can tell it apart from a true orphan.)
        inbound: set[str] = set()
        for targets in extraction.navigation_map.values():
            inbound.update(targets)
        orphans = [s for s in extraction.screens if s.screen_name not in inbound]
        if not orphans:
            return "No orphan screens — every screen is reachable from another."
        lines = [f"## Orphan screens (no inbound navigation): {len(orphans)}"]
        for screen in orphans:
            lines.append(f"- {screen.screen_name} ({screen.element_count} elements)")
        lines.append(
            "\n(These are never the target of a navigation — one may be the "
            "start screen, the rest are genuinely unreachable.)"
        )
        return "\n".join(lines)

    # screen / tag require a name
    if not name or not name.strip():
        return (
            f"Error: detail '{normalized}' requires a screen or tag name "
            f"(pass the 'name' argument)."
        )
    target = name.strip()

    if normalized == "tag":
        info = extraction.plc_tag_index.get(target)
        if info is None:
            for key, value in extraction.plc_tag_index.items():
                if key.lower() == target.lower():
                    info, target = value, key
                    break
        if info is None:
            return (
                f"Tag '{target}' not found in plc_tag_index. "
                f"Available (first {_MAX_SUGGESTIONS}): {_available_tags(extraction)}"
            )
        return _format_hmi_tag(target, info)

    # normalized == "screen"
    screen = _find_screen(extraction, target)
    if screen is None:
        return (
            f"Screen '{target}' not found. "
            f"Available screens (first {_MAX_SUGGESTIONS}): "
            f"{_available_screens(extraction)}"
        )
    return _format_screen(screen)
