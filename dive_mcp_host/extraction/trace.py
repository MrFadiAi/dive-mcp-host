"""Pure formatter for the combined PLC↔HMI tag trace.

Connects the HMI UI to PLC logic: given a tag, report where it's used in the
PLC (``tag_xref``: blocks / address / type) AND the HMI (``plc_tag_index``:
screens / connection). Langchain-free so it unit-tests in isolation; the
``trace_tag`` tool is a thin wrapper over this.
"""

from __future__ import annotations

from typing import Any

from dive_mcp_host.extraction.models import HmiExtraction, PlcExtraction


def _find_case_insensitive(mapping: dict[str, Any], name: str) -> Any | None:
    """Exact match first, then case-insensitive. Returns the value or None."""
    if name in mapping:
        return mapping[name]
    lowered = name.lower()
    for key, value in mapping.items():
        if key.lower() == lowered:
            return value
    return None


def format_tag_trace(plc: PlcExtraction, hmi: HmiExtraction, tag: str) -> str:
    """Format combined PLC + HMI usage for a tag.

    Renders each side from its real parser shape:
      - PLC ``tag_xref[tag]`` = ``{plc_tag_address, data_type, used_in: [blocks]}``
      - HMI ``plc_tag_index[tag]`` = ``{plc_tag, data_type, connection,
        used_in_screens: [...]}``
    A side that doesn't reference the tag reports "not found" rather than
    raising, so a name that exists in only one domain still traces cleanly.
    """
    name = (tag or "").strip()
    lines = [f"## Tag trace: {name}"]

    plc_info = _find_case_insensitive(plc.tag_xref, name)
    lines.append("**PLC usage:**")
    if plc_info is None:
        lines.append(f"  Tag '{name}' not found in PLC tag_xref.")
    elif isinstance(plc_info, dict):
        used_in = plc_info.get("used_in")
        if isinstance(used_in, list) and used_in:
            lines.append(f"  Used in blocks ({len(used_in)}): {', '.join(used_in)}")
        address = plc_info.get("plc_tag_address") or plc_info.get("plc_tag")
        if address:
            lines.append(f"  Address: {address}")
        dtype = plc_info.get("data_type")
        if dtype:
            lines.append(f"  Data type: {dtype}")
    else:
        lines.append(f"  {plc_info}")

    hmi_info = _find_case_insensitive(hmi.plc_tag_index, name)
    lines.append("**HMI usage:**")
    if hmi_info is None:
        lines.append(f"  Tag '{name}' not found in HMI plc_tag_index.")
    elif isinstance(hmi_info, dict):
        used_in = hmi_info.get("used_in_screens")
        if isinstance(used_in, list) and used_in:
            lines.append(f"  Used in screens ({len(used_in)}): {', '.join(used_in)}")
        plc_tag = hmi_info.get("plc_tag")
        if plc_tag:
            lines.append(f"  PLC tag: {plc_tag}")
        dtype = hmi_info.get("data_type")
        if dtype:
            lines.append(f"  Data type: {dtype}")
        connection = hmi_info.get("connection")
        if connection:
            lines.append(f"  Connection: {connection}")
    else:
        lines.append(f"  {hmi_info}")

    return "\n".join(lines)


def format_coverage_gap(plc: PlcExtraction, hmi: HmiExtraction) -> str:
    """Commissioning gap analysis between PLC tags and HMI bindings.

    - **Bound** — tags present in both (PLC ``tag_xref`` ∩ HMI ``plc_tag_index``).
    - **PLC-only** — PLC tags with NO HMI binding → not surfaced to the operator.
    - **HMI-only** — HMI-referenced tags absent from the PLC extraction → likely
      stale/orphan HMI references.
    """
    plc_tags = set(plc.tag_xref.keys())
    hmi_tags = set(hmi.plc_tag_index.keys())
    bound = sorted(plc_tags & hmi_tags)
    plc_only = sorted(plc_tags - hmi_tags)
    hmi_only = sorted(hmi_tags - plc_tags)

    lines = ["## PLC↔HMI tag coverage"]
    lines.append(f"Bound (in both): {len(bound)}")
    if bound:
        lines.append("  " + ", ".join(bound[:50]) + _ellipsis(bound))

    lines.append(
        f"PLC-only — no HMI binding (not surfaced to operator): {len(plc_only)}"
    )
    if plc_only:
        lines.append("  " + ", ".join(plc_only[:50]) + _ellipsis(plc_only))

    lines.append(
        f"HMI-only — no PLC usage (possible stale HMI reference): {len(hmi_only)}"
    )
    if hmi_only:
        lines.append("  " + ", ".join(hmi_only[:50]) + _ellipsis(hmi_only))
    return "\n".join(lines)


def _ellipsis(items: list) -> str:
    return f"  … (+{len(items) - 50} more)" if len(items) > 50 else ""
