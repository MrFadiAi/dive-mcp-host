"""Pure helpers to answer follow-up queries against a cached ``PlcExtraction``.

Kept langchain-free so it is unit-testable in isolation; the
``query_plc_blocks`` tool (``internal_tools/tools/query_plc.py``) is a thin
wrapper that pulls the cached extraction and delegates formatting here.

This closes the dangling promise from ``extract_plc_blocks`` — it tells the AI
to "reference this cache_key for follow-up queries", but until now nothing
could read the cache, so every follow-up re-parsed the exported XML.
"""

from __future__ import annotations

from dive_mcp_host.extraction.models import BlockInterface, BlockResult, PlcExtraction

_VALID_DETAILS = (
    "block",
    "calls",
    "callers",
    "tag",
    "call_tree",
    "summary",
    "dead_code",
    "search",
)

# Cap reconstructed code shown for a single block to avoid flooding context.
_MAX_BLOCK_CODE_CHARS = 8000
# Cap the number of available-name suggestions on a miss.
_MAX_SUGGESTIONS = 50


def _find_block(extraction: PlcExtraction, name: str) -> BlockResult | None:
    """Find a block by name, exact match first then case-insensitive."""
    for block in extraction.blocks:
        if block.block_name == name:
            return block
    name_lower = name.lower()
    for block in extraction.blocks:
        if block.block_name.lower() == name_lower:
            return block
    return None


def _block_code(block: BlockResult) -> str:
    """Best reconstructed source for a block: top-level code, else joined networks."""
    if block.code and block.code.strip():
        return block.code.strip()
    parts: list[str] = []
    for idx, net in enumerate(block.networks, start=1):
        seg: list[str] = []
        if net.title:
            seg.append(f"// Network {idx}: {net.title}")
        if net.code and net.code.strip():
            seg.append(net.code.strip())
        if seg:
            parts.append("\n".join(seg))
    return "\n\n".join(parts)


def _interface_lines(interface: BlockInterface) -> list[str]:
    """Render non-empty interface sections (IN/OUT/IN_OUT/STAT/TEMP/CONST)."""
    out: list[str] = []
    for label, members in (
        ("IN", interface.inputs),
        ("OUT", interface.outputs),
        ("IN_OUT", interface.inouts),
        ("STAT", interface.statics),
        ("TEMP", interface.temps),
        ("CONST", interface.constants),
    ):
        if not members:
            continue
        out.append(f"  {label}:")
        for member in members[:50]:
            dtype = f" : {member.data_type}" if member.data_type else ""
            comment = f"  // {member.comment}" if member.comment else ""
            out.append(f"    {member.name}{dtype}{comment}")
    return out


def _available_blocks(extraction: PlcExtraction) -> str:
    names = [b.block_name for b in extraction.blocks[:_MAX_SUGGESTIONS]]
    return ", ".join(names) if names else "none"


def _available_tags(extraction: PlcExtraction) -> str:
    names = sorted(extraction.tag_xref)[:_MAX_SUGGESTIONS]
    return ", ".join(names) if names else "none"


def _format_tag_usage(name: str, info: object) -> str:
    """Render a tag's usage from its ``tag_xref`` entry.

    The PLC parser produces ``{plc_tag_address, data_type, used_in: [blocks]}``;
    render that cleanly (block list + address + data type) instead of dumping
    the metadata keys as if they were blocks with a Python list repr. Any
    extra/unknown keys are appended generically; a non-dict value falls back
    to ``str()``.
    """
    lines = [f"## Tag usage: {name}"]
    if not isinstance(info, dict):
        lines.append(str(info))
        return "\n".join(lines)

    used_in = info.get("used_in")
    if isinstance(used_in, list):
        joined = ", ".join(used_in) if used_in else "(none)"
        lines.append(f"**Used in ({len(used_in)}):** {joined}")

    address = info.get("plc_tag_address") or info.get("plc_tag") or info.get("address")
    if address:
        lines.append(f"**Address:** {address}")
    dtype = info.get("data_type")
    if dtype:
        lines.append(f"**Data type:** {dtype}")
    connection = info.get("connection")
    if connection:
        lines.append(f"**Connection:** {connection}")

    already_rendered = {
        "used_in",
        "used_in_screens",
        "plc_tag_address",
        "plc_tag",
        "address",
        "data_type",
        "connection",
    }
    for key, value in info.items():
        if key not in already_rendered:
            lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def format_plc_query(
    extraction: PlcExtraction, detail: str, name: str | None = None
) -> str:
    """Format one follow-up query against a cached ``PlcExtraction``.

    Args:
        extraction: the cached PLC extraction result.
        detail: one of ``block``, ``calls``, ``callers``, ``tag``,
            ``call_tree``, ``summary``.
        name: block name (for ``block``/``calls``/``callers``) or tag name
            (for ``tag``).

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
                "## PLC extraction summary",
                f"Blocks: {s.total_blocks} "
                f"({s.fb_count} FB, {s.fc_count} FC, {s.ob_count} OB, "
                f"{s.db_count} DB, {s.idb_count} IDB)",
                f"Languages: {s.scl_count} SCL, {s.stl_count} STL",
                f"Calls: {s.total_calls} | "
                f"Tag refs: {s.unique_tag_refs} unique "
                f"({s.total_tag_refs} total) | "
                f"PLC tags loaded: {s.plc_tags_loaded}",
            ]
        )

    if normalized == "call_tree":
        if not extraction.call_tree:
            return "No call tree recorded in this extraction."
        lines = ["## Call tree (caller -> callees)"]
        for caller in sorted(extraction.call_tree):
            callees = extraction.call_tree[caller] or []
            lines.append(
                f"- {caller} -> {', '.join(callees) if callees else '(none)'}"
            )
        return "\n".join(lines)

    if normalized == "dead_code":
        # FB/FC blocks with zero callers are dead-code candidates. OBs are
        # entry points (called by the PLC scan, not user code) and DBs/IDBs are
        # data, so both are excluded.
        dead = [
            block
            for block in extraction.blocks
            if block.block_type not in ("OB", "DB", "IDB")
            and not extraction.called_by.get(block.block_name)
        ]
        if not dead:
            return "No unused FB/FC blocks found — every FB/FC is called somewhere."
        lines = [f"## Unused blocks (dead-code candidates): {len(dead)}"]
        for block in dead:
            lines.append(
                f"- {block.block_name} ({block.block_type} {block.block_number}, "
                f"{block.programming_language})"
            )
        lines.append("\n(OBs are entry points and DBs/IDBs are data — both excluded.)")
        return "\n".join(lines)

    if normalized == "search":
        # 'name' is the free-text search term — grep reconstructed code across
        # all blocks (find a pattern an assignment/comment/magic number lives
        # in). Distinct from 'tag' (structured tag_xref index).
        if not name or not name.strip():
            return "Error: detail 'search' requires a search term (pass 'name')."
        needle = name.strip().lower()
        matches: list[tuple[BlockResult, list[str]]] = []
        for block in extraction.blocks:
            code = _block_code(block)
            if not code:
                continue
            hit_lines = [ln for ln in code.splitlines() if needle in ln.lower()]
            if hit_lines:
                matches.append((block, hit_lines))
        if not matches:
            return f"No blocks match '{name}'."
        lines = [f"## Code search '{name}': {len(matches)} block(s)"]
        for block, hit_lines in matches:
            lines.append(
                f"- **{block.block_name}** ({block.block_type} {block.block_number})"
            )
            for hit in hit_lines[:10]:
                lines.append(f"    {hit.strip()}")
        return "\n".join(lines)

    # block / calls / callers / tag all require a name
    if not name or not name.strip():
        return (
            f"Error: detail '{normalized}' requires a block or tag name "
            f"(pass the 'name' argument)."
        )
    target = name.strip()

    if normalized == "tag":
        info = extraction.tag_xref.get(target)
        if info is None:
            for key, value in extraction.tag_xref.items():
                if key.lower() == target.lower():
                    info, target = value, key
                    break
        if info is None:
            return (
                f"Tag '{target}' not found in tag_xref. "
                f"Available (first {_MAX_SUGGESTIONS}): {_available_tags(extraction)}"
            )
        return _format_tag_usage(target, info)

    # block / calls / callers all key off a block
    block = _find_block(extraction, target)
    if block is None:
        return (
            f"Block '{target}' not found. "
            f"Available blocks (first {_MAX_SUGGESTIONS}): "
            f"{_available_blocks(extraction)}"
        )

    if normalized == "calls":
        callees = extraction.call_tree.get(block.block_name) or block.calls or []
        if isinstance(callees, list):
            joined = ", ".join(callees) if callees else "(none)"
        else:
            joined = str(callees)
        return f"{block.block_name} calls: {joined}"

    if normalized == "callers":
        callers = extraction.called_by.get(block.block_name, [])
        joined = ", ".join(callers) if callers else "(nobody / top-level)"
        return f"{block.block_name} is called by: {joined}"

    # normalized == "block"
    lines = [
        f"## {block.block_name} ({block.block_type} {block.block_number}, "
        f"{block.programming_language})"
    ]
    if block.block_title:
        lines.append(f"**Title:** {block.block_title}")
    if block.comment:
        lines.append(f"**Comment:** {block.comment}")
    iface = _interface_lines(block.interface)
    if iface:
        lines.append("**Interface:**")
        lines.extend(iface)
    code = _block_code(block)
    if code:
        if len(code) > _MAX_BLOCK_CODE_CHARS:
            code = code[:_MAX_BLOCK_CODE_CHARS] + "\n... (truncated)"
        lines.append("**Code:**")
        lines.append(f"```\n{code}\n```")
    else:
        lines.append("**Code:** (none reconstructed)")
    if block.calls:
        lines.append(f"**Calls:** {', '.join(block.calls)}")
    return "\n".join(lines)
