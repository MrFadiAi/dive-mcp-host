"""Pure helpers to answer follow-up queries against a cached ``PlcExtraction``.

Kept langchain-free so it is unit-testable in isolation; the
``query_plc_blocks`` tool (``internal_tools/tools/query_plc.py``) is a thin
wrapper that pulls the cached extraction and delegates formatting here.

This closes the dangling promise from ``extract_plc_blocks`` — it tells the AI
to "reference this cache_key for follow-up queries", but until now nothing
could read the cache, so every follow-up re-parsed the exported XML.
"""

from __future__ import annotations

from collections import deque
from typing import Any

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
    "path",
    "mermaid",
    "cycles",
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


def _mermaid_call_graph(
    extraction: PlcExtraction, root: str | None = None, max_nodes: int = 60
) -> str | None:
    """Render the call graph (or a focused sub-tree from ``root``) as a Mermaid
    flowchart. BFS over ``call_tree``, node-capped, with sanitized node IDs
    (block names can contain chars that are invalid as Mermaid IDs). Returns
    ``None`` when ``root`` is given but not found (caller emits a message)."""
    if root:
        block = _find_block(extraction, root)
        if block is None:
            return None
        starts = [block.block_name]
    else:
        starts = [b.block_name for b in extraction.blocks if b.block_type == "OB"]
        if not starts:
            starts = [b.block_name for b in extraction.blocks[:5]]

    nodes: set[str] = set(starts)
    edges: list[tuple[str, str]] = []
    queue: deque[str] = deque(starts)
    while queue and len(nodes) < max_nodes:
        current = queue.popleft()
        for callee in extraction.call_tree.get(current, []):
            edges.append((current, callee))
            if callee not in nodes and len(nodes) < max_nodes:
                nodes.add(callee)
                queue.append(callee)

    id_for = {name: f"n{i}" for i, name in enumerate(sorted(nodes))}
    lines = ["```mermaid", "graph TD"]
    for name in sorted(nodes):
        lines.append(f'    {id_for[name]}["{name}"]')
    for src, dst in edges:
        if src in id_for and dst in id_for:
            lines.append(f"    {id_for[src]} --> {id_for[dst]}")
    lines.append("```")
    return "\n".join(lines)


def _tarjan_scc(graph: dict[str, list[str]]) -> list[list[str]]:
    """Return strongly-connected components of a directed graph (Tarjan, iterative).

    ``graph`` maps each node to its successor list. Iterative (no recursion
    limit) so a deep call graph can't blow the stack. Each SCC is returned as a
    list of node names (order within a component is traversal order, not
    sorted). Used to find mutually-recursive call-cycle groups (SCCs of size
    >= 2).
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    counter = [0]
    sccs: list[list[str]] = []

    for root in graph:
        if root in index:
            continue
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack[root] = True
        # work stack holds (node, successor-iterator) so a node's successors
        # resume exactly where they left off after a child subtree completes.
        work: list[tuple[str, Any]] = [(root, iter(graph[root]))]
        while work:
            node, succ_iter = work[-1]
            descended = False
            for w in succ_iter:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, iter(graph[w])))
                    descended = True
                    break
                if on_stack.get(w):
                    low[node] = min(low[node], index[w])
            if descended:
                continue
            # All successors processed — pop this node's SCC if it is a root.
            if low[node] == index[node]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == node:
                        break
                sccs.append(comp)
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sccs


def _find_call_cycles(
    extraction: PlcExtraction,
) -> tuple[list[list[str]], list[str]]:
    """Detect recursion in the call graph.

    Returns ``(groups, self_loops)``:
      - ``groups``: mutually-recursive block groups — SCCs of size >= 2, each a
        sorted name list (the whole group calls back into itself).
      - ``self_loops``: blocks that call themselves directly (sorted).
    Calls to names not in ``extraction.blocks`` (external/library blocks) are
    ignored so they can't form phantom cycles.
    """
    known = {b.block_name for b in extraction.blocks}
    graph: dict[str, list[str]] = {}
    for block in extraction.blocks:
        callees = extraction.call_tree.get(block.block_name) or block.calls or []
        graph[block.block_name] = [c for c in callees if c in known]

    sccs = _tarjan_scc(graph)
    groups = sorted(
        (sorted(s) for s in sccs if len(s) >= 2),
        key=lambda g: g,
    )
    self_loops = sorted(n for n, callees in graph.items() if n in callees)
    return groups, self_loops


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

    if normalized == "mermaid":
        # Optional name = root block; render the call sub-tree from it (or the
        # whole graph from OBs when omitted) as a Mermaid flowchart.
        root = name.strip() if name else None
        rendered = _mermaid_call_graph(extraction, root=root)
        if rendered is None:
            return (
                f"Block '{name}' not found. "
                f"Available blocks (first {_MAX_SUGGESTIONS}): "
                f"{_available_blocks(extraction)}"
            )
        return rendered

    if normalized == "cycles":
        # Mutually-recursive call-cycle groups (SCCs >= 2) + self-recursive
        # blocks. Cyclic FB/FC calls are a PLC anti-pattern (scan-time issues /
        # compiler-blocked), so this is commissioning-relevant.
        groups, self_loops = _find_call_cycles(extraction)
        if not groups and not self_loops:
            return "No call cycles found — the call graph is acyclic (no mutual recursion)."
        lines: list[str] = []
        if groups:
            lines.append(
                f"## Call cycles (mutually-recursive block groups): {len(groups)}"
            )
            for group in groups:
                lines.append(f"- {' <-> '.join(group)}")
        if self_loops:
            lines.append(f"## Self-recursive blocks (call themselves): {len(self_loops)}")
            lines.append(", ".join(self_loops))
        lines.append(
            "\n(Cyclic FB/FC calls are a PLC anti-pattern — they can cause "
            "scan-time issues or are rejected by the compiler. Review these.)"
        )
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

    if normalized == "path":
        # Trace a call chain from a no-caller entry point (an OB) down to the
        # target by walking called_by upward (BFS, shortest path). Multi-hop
        # complement to 'callers' (1-hop). Cycle-safe via a visited set.
        block = _find_block(extraction, target)
        if block is None:
            return (
                f"Block '{target}' not found. "
                f"Available blocks (first {_MAX_SUGGESTIONS}): "
                f"{_available_blocks(extraction)}"
            )
        start = block.block_name
        queue: deque[list[str]] = deque([[start]])
        visited = {start}
        found: list[str] | None = None
        while queue:
            path = queue.popleft()
            callers = extraction.called_by.get(path[-1], [])
            if not callers:
                found = path
                break
            for caller in callers:
                if caller not in visited:
                    visited.add(caller)
                    queue.append(path + [caller])
        if found is None:
            return f"No entry-point path to {start} found (cycle or no OB root)."
        chain = " -> ".join(reversed(found))
        return f"## Call path to {start} ({len(found)} block(s))\n{chain}"

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
