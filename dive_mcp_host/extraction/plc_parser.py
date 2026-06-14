"""PLC Program Block Extractor (Offline).

Parses TIA Portal exported Openness XML block and tag table files.

Extracts:
  - Block metadata (name, number, type, language)
  - Interface definitions (inputs, outputs, inouts, statics, temps, constants)
  - SCL/STL code (reconstructed from tokenized XML)
  - Tag cross-references (which PLC tags are used where)
  - Call structure (which blocks call which)

Adapted from ``Extract_PLC_Data_GUI/src/extract_plc_full.py`` for use as
a library module returning structured Pydantic models instead of writing
JSON files.
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from dive_mcp_host.extraction.models import (
    BlockInterface,
    BlockResult,
    InterfaceMember,
    Network,
    PlcExtraction,
    PlcSummary,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# XML namespaces used in TIA Portal exports
# ---------------------------------------------------------------------------
NS_INTERFACE = "http://www.siemens.com/automation/Openness/SW/Interface/v5"
NS_ST = "http://www.siemens.com/automation/Openness/SW/NetworkSource/StructuredText/v3"
NS_STL = "http://www.siemens.com/automation/Openness/SW/NetworkSource/StatementList/v4"

# Block type mapping from XML element names
BLOCK_TYPES: dict[str, str] = {
    "SW.Blocks.FB": "FB",
    "SW.Blocks.FC": "FC",
    "SW.Blocks.OB": "OB",
    "SW.Blocks.GlobalDB": "DB",
    "SW.Blocks.InstanceDB": "IDB",
    "SW.Types.PlcDataType": "UDT",
    "SW.Types.PlcStruct": "STRUCT",
}

# IEC address type mapping (used for address formatting)
IEC_TYPE_MAP: dict[str, str] = {"Bool": "X", "Byte": "B", "Word": "W", "DWord": "D"}
IEC_TYPE_MAP_FLAT: dict[str, str] = {
    "Bool": "",
    "Byte": "B",
    "Word": "W",
    "DWord": "D",
}  # I/Q/M areas (no X for Bool)

# Interface section names that contain variables
INTERFACE_SECTIONS: list[str] = [
    "Input",
    "Output",
    "InOut",
    "Static",
    "Temp",
    "Constant",
    "Return",
]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def strip_ns(tag: str) -> str:
    """Remove XML namespace from tag name."""
    return tag.split("}")[-1] if "}" in tag else tag


def get_text(element: ET.Element, path: str, default: str = "") -> str:
    """Get text from an element, handling namespace."""
    text = element.findtext(path, default)
    return text.strip() if text else default


def findall_ns(element: ET.Element, tag_local: str) -> list[ET.Element]:
    """Find all children matching local tag name (ignoring namespace)."""
    return [c for c in element if strip_ns(c.tag) == tag_local]


def find_ns(element: ET.Element, tag_local: str) -> ET.Element | None:
    """Find first child matching local tag name (ignoring namespace)."""
    for c in element:
        if strip_ns(c.tag) == tag_local:
            return c
    return None


# ---------------------------------------------------------------------------
# Block XML discovery
# ---------------------------------------------------------------------------
def find_block_files(base_path: str | Path) -> list[tuple[str, str]]:
    """Walk directory recursively for .xml block files.

    Returns a sorted list of ``(full_path, relative_path)`` tuples,
    excluding any XML files inside a ``PLC tags`` subdirectory.
    """
    base = Path(base_path)
    # Normalize for reliable comparison
    tags_dir_normalized = (base / "PLC tags").resolve().as_posix().lower()

    blocks: list[tuple[str, str]] = []
    for root, _dirs, files in os.walk(base):
        root_normalized = Path(root).resolve().as_posix().lower()
        # Skip PLC tags directory (exact match, not substring)
        if root_normalized == tags_dir_normalized:
            continue
        # Also skip any subdirectory of PLC tags
        if root_normalized.startswith(tags_dir_normalized + "/"):
            continue
        for f in files:
            if f.lower().endswith(".xml"):
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, base)
                blocks.append((full_path, rel_path))

    return sorted(blocks)


def detect_block_type(
    root: ET.Element,
) -> tuple[str | None, ET.Element | None]:
    """Detect block type from root element. Returns (type_str, block_elem)."""
    last_child: ET.Element | None = None
    for child in root:
        last_child = child
        tag = strip_ns(child.tag)
        if tag in BLOCK_TYPES:
            return BLOCK_TYPES[tag], child
    return None, last_child


# ---------------------------------------------------------------------------
# Interface parsing
# ---------------------------------------------------------------------------
def parse_interface(block_elem: ET.Element, lang: str = "en-US") -> dict:
    """Parse block interface sections from XML."""
    interface: dict[str, list] = {
        "inputs": [],
        "outputs": [],
        "inouts": [],
        "statics": [],
        "temps": [],
        "constants": [],
        "members": [],
        "returns": [],
    }
    section_map: dict[str, str] = {
        "Input": "inputs",
        "Output": "outputs",
        "InOut": "inouts",
        "Static": "statics",
        "Temp": "temps",
        "Constant": "constants",
        "None": "members",
        "Return": "returns",
    }

    # Find Interface element
    iface_elem: ET.Element | None = None
    for attr_list in block_elem.iter():
        if strip_ns(attr_list.tag) == "Interface":
            iface_elem = attr_list
            break
    if iface_elem is None:
        return interface

    # Find Sections
    sections_elem = find_ns(iface_elem, "Sections")
    if sections_elem is None:
        # Try direct children (interface may contain sections directly)
        for child in iface_elem:
            if strip_ns(child.tag) == "Sections":
                sections_elem = child
                break

    if sections_elem is None:
        return interface

    for section in sections_elem:
        if strip_ns(section.tag) != "Section":
            continue
        section_name = section.get("Name", "")
        target_key = section_map.get(section_name)
        if not target_key:
            # Check for nested Sections inside unmapped sections (e.g., "Base")
            for sub in section:
                if strip_ns(sub.tag) == "Sections":
                    for nested_sec in sub:
                        if strip_ns(nested_sec.tag) == "Section":
                            nested_name = nested_sec.get("Name", "")
                            nested_key = section_map.get(nested_name)
                            if nested_key:
                                for member in nested_sec:
                                    if strip_ns(member.tag) == "Member":
                                        interface[nested_key].append(
                                            parse_member(member, lang=lang)
                                        )
            continue

        for member in section:
            if strip_ns(member.tag) == "Member":
                parsed = parse_member(member, lang=lang)
                interface[target_key].append(parsed)

    return interface


def _collect_members(
    sections_elem: ET.Element,
    result_list: list[dict],
    depth: int,
    lang: str = "en-US",
) -> None:
    """Recursively collect Members from a Sections > Section > [Sections...] hierarchy."""
    for section in sections_elem:
        if strip_ns(section.tag) != "Section":
            continue
        section_name = section.get("Name", "")
        for item in section:
            tag = strip_ns(item.tag)
            if tag == "Member":
                parsed = parse_member(item, depth=depth + 1, lang=lang)
                if section_name and section_name != "None":
                    parsed["section"] = section_name
                result_list.append(parsed)
            elif tag == "Sections":
                # Nested Sections inside a Section (e.g., Section "Base" containing Sections)
                _collect_members(item, result_list, depth, lang=lang)


def parse_member(member_elem: ET.Element, depth: int = 0, lang: str = "en-US") -> dict:
    """Parse a single interface member, including nested struct members."""
    name = member_elem.get("Name", "")
    datatype = member_elem.get("Datatype", "")
    remanence = member_elem.get("Remanence", "")
    accessibility = member_elem.get("Accessibility", "")
    version = member_elem.get("Version", "")
    informative = member_elem.get("Informative", "")

    # Start value
    start_value = ""
    sv_elem = find_ns(member_elem, "StartValue")
    if sv_elem is not None and sv_elem.text:
        start_value = sv_elem.text.strip()

    # Comment
    comment = ""
    comment_elem = find_ns(member_elem, "Comment")
    if comment_elem is not None:
        for mlt in comment_elem:
            if strip_ns(mlt.tag) == "MultiLanguageText":
                if mlt.get("Lang", "") == lang and mlt.text:
                    comment = mlt.text.strip()
                    break
        if not comment:
            for mlt in comment_elem:
                if strip_ns(mlt.tag) == "MultiLanguageText" and mlt.text:
                    comment = mlt.text.strip()
                    break

    # Nested members (structs/UDTs)
    children: list[dict] = []
    for child in member_elem:
        if strip_ns(child.tag) == "Member":
            children.append(parse_member(child, depth=depth + 1, lang=lang))
        elif strip_ns(child.tag) == "Sections":
            # UDT-typed members have nested Sections > Section > Member
            _collect_members(child, children, depth, lang=lang)

    # Subelement start values (array/struct element defaults like alarm texts, parameters)
    subelement_values: list[dict] = []
    for sub in member_elem.iter():
        if strip_ns(sub.tag) == "Subelement":
            sv_elem_inner: ET.Element | None = None
            for sub_child in sub:
                if strip_ns(sub_child.tag) == "StartValue" and sub_child.text:
                    sv_elem_inner = sub_child
                    break
            if sv_elem_inner is not None:
                path = sub.get("Path", "")
                subelement_values.append(
                    {"path": path, "start_value": sv_elem_inner.text.strip()}
                )

    # Read BooleanAttribute children from AttributeList (used by PLC type structs)
    bool_attrs: dict[str, bool] = {}
    attr_list = find_ns(member_elem, "AttributeList")
    if attr_list is not None:
        for attr in attr_list:
            if strip_ns(attr.tag) == "BooleanAttribute":
                attr_name = attr.get("Name", "")
                if attr.text:
                    bool_attrs[attr_name] = attr.text.strip().lower() == "true"

    # Determine accessibility: prefer direct attribute, fall back to BooleanAttributes
    if not accessibility:
        parts: list[str] = []
        if bool_attrs.get("ExternalAccessible"):
            parts.append("Read")
        if bool_attrs.get("ExternalWritable"):
            parts.append("Write")
        if bool_attrs.get("ExternalVisible"):
            parts.append("Visible")
        if parts:
            accessibility = "/".join(parts)

    result: dict = {
        "name": name,
        "data_type": datatype,
        "comment": comment,
    }
    if start_value:
        result["start_value"] = start_value
    if remanence:
        result["remanence"] = remanence
    if accessibility:
        result["accessibility"] = accessibility
    if version:
        result["version"] = version
    if informative:
        result["informative"] = True
    if bool_attrs.get("SetPoint") is not None:
        result["setpoint"] = bool_attrs["SetPoint"]
    # Preserve raw ExternalAccess flags for OPC UA / webserver visibility
    ext_access: dict[str, bool] = {}
    for flag in ("ExternalAccessible", "ExternalVisible", "ExternalWritable"):
        if flag in bool_attrs:
            ext_access[flag] = bool_attrs[flag]
    if ext_access:
        result["external_access"] = ext_access
    if children:
        result["members"] = children
    if subelement_values:
        result["subelement_values"] = subelement_values

    return result


def count_interface_vars(interface: dict) -> int:
    """Count total variables across all interface sections."""
    total = 0
    for section in (
        "inputs",
        "outputs",
        "inouts",
        "statics",
        "temps",
        "constants",
        "members",
        "returns",
    ):
        for m in interface.get(section, []):
            total += 1
            total += count_nested(m)
    return total


def count_nested(member: dict) -> int:
    """Recursively count nested members."""
    count = 0
    for child in member.get("members", []):
        count += 1
        count += count_nested(child)
    return count


# ---------------------------------------------------------------------------
# SCL code reconstruction (StructuredText v3 tokenized XML)
# ---------------------------------------------------------------------------
def reconstruct_scl(st_elem: ET.Element) -> str:
    """Reconstruct readable SCL code from tokenized StructuredText XML."""
    children = list(st_elem)
    if not children:
        # Plain text content (V14 format)
        text = st_elem.text or ""
        return text.strip()

    parts: list[str] = []
    for child in children:
        tag = strip_ns(child.tag)
        _append_scl_part(child, tag, parts)

    return "".join(parts).strip()


def _append_scl_part(elem: ET.Element, tag: str, parts: list[str]) -> None:
    """Append one XML token's text contribution."""
    if tag == "Token":
        text = elem.get("Text", "")
        parts.append(text)
    elif tag == "Blank":
        num = elem.get("Num")
        parts.append(" " * int(num) if num else " ")
    elif tag == "Text":
        parts.append(elem.text or "")
    elif tag == "NewLine":
        num = elem.get("Num")
        parts.append("\n" * int(num) if num else "\n")
    elif tag == "LineComment":
        text_elem = find_ns(elem, "Text")
        if text_elem is not None and text_elem.text:
            parts.append("//" + text_elem.text)
        elif elem.text:
            parts.append("//" + elem.text)
    elif tag == "BlockComment":
        parts.append("(*")
        for sub in elem:
            sub_tag = strip_ns(sub.tag)
            if sub_tag == "Text":
                parts.append(sub.text or "")
            elif sub_tag == "NewLine":
                parts.append("\n")
        parts.append("*)")
    elif tag == "Access":
        _reconstruct_access(elem, parts)
    elif tag == "NamePart":
        parts.append(elem.get("Text", ""))
    elif tag == "Date":
        parts.append(elem.get("Value", ""))
    elif tag == "Time":
        parts.append(elem.get("Value", ""))


def _reconstruct_access(access_elem: ET.Element, parts: list[str]) -> None:
    """Reconstruct an Access element (variable/constant reference)."""
    scope = access_elem.get("Scope", "")

    if scope == "Call":
        # Block call: extract block name and parameters
        _reconstruct_call(access_elem, parts)
        return

    if scope == "GlobalVariable":
        # PLC tag: "DB_NAME".Member
        _reconstruct_symbol(access_elem, parts, global_var=True)
        return

    if scope == "LocalVariable":
        # Local var: #VarName
        parts.append("#")
        _reconstruct_symbol(access_elem, parts, global_var=False)
        return

    if scope == "LocalConstant":
        # Look for <Constant Name="PI" /> (named constant reference)
        for child in access_elem:
            ctag = strip_ns(child.tag)
            if ctag == "Constant":
                cname = child.get("Name", "")
                if cname:
                    parts.append("#" + cname)
                    return
        _reconstruct_symbol(access_elem, parts, global_var=False)
        return

    if scope == "Address":
        # Absolute address: <Address Area="DB" Type="DWord" BlockNumber="1002" BitOffset="64" />
        for child in access_elem:
            if strip_ns(child.tag) == "Address":
                area = child.get("Area", "")
                dtype = child.get("Type", "")
                blk = child.get("BlockNumber", "")
                offset_str = child.get("BitOffset", "0")
                try:
                    offset = int(offset_str)
                except (ValueError, TypeError):
                    offset = 0
                if area == "DB" and blk:
                    byte_off = offset // 8
                    tp = IEC_TYPE_MAP.get(dtype, "B")
                    if dtype == "Bool":
                        parts.append(f"%DB{blk}.DB{tp}{byte_off}.{offset % 8}")
                    else:
                        parts.append(f"%DB{blk}.DB{tp}{byte_off}")
                elif area in ("I", "Q", "M"):
                    byte_off = offset // 8
                    tp = IEC_TYPE_MAP_FLAT.get(dtype, "")
                    if dtype == "Bool":
                        parts.append(f"%{area}{byte_off}.{offset % 8}")
                    else:
                        parts.append(f"%{area}{tp}{byte_off}")
                else:
                    parts.append(f"[{area}:{dtype}@{offset}]")
                return

    if scope == "Label":
        for child in access_elem:
            if strip_ns(child.tag) == "Label":
                parts.append(child.get("Name", "") + ":")
        return

    if scope in ("LiteralConstant", "TypedConstant"):
        const_elem = find_ns(access_elem, "Constant")
        if const_elem is not None:
            val_elem = find_ns(const_elem, "ConstantValue")
            if val_elem is not None and val_elem.text:
                parts.append(val_elem.text)
            else:
                type_elem = find_ns(const_elem, "ConstantType")
                val = const_elem.text or ""
                if type_elem is not None and type_elem.text == "String":
                    parts.append(f"'{val}'")
                else:
                    parts.append(val)
        return

    if scope in ("Input", "Output", "InOut", "Static", "Temp"):
        parts.append("#")
        _reconstruct_symbol(access_elem, parts, global_var=False)
        return

    # Fallback: process children normally
    for child in access_elem:
        child_tag = strip_ns(child.tag)
        if child_tag not in ("Symbol", "Constant", "CallInfo", "Instance", "Instruction"):
            _append_scl_part(child, child_tag, parts)


def _reconstruct_symbol(
    parent_elem: ET.Element, parts: list[str], global_var: bool = False
) -> None:
    """Reconstruct a Symbol path from Component elements."""
    symbols: list[str] = []
    for child in parent_elem:
        tag = strip_ns(child.tag)
        if tag == "Symbol":
            _collect_symbol_path(child, symbols, global_var)
        elif tag == "Component":
            name = child.get("Name", "")
            has_quotes = False
            for attr in child:
                if (
                    strip_ns(attr.tag) == "BooleanAttribute"
                    and attr.get("Name") == "HasQuotes"
                ):
                    has_quotes = attr.text == "true"
            if global_var and has_quotes:
                symbols.append(f'"{name}"')
            else:
                symbols.append(name)
        elif tag in ("Token", "Blank", "Text", "NewLine"):
            _append_scl_part(child, tag, parts)

    if symbols:
        parts.append(".".join(symbols))


def _collect_symbol_path(
    symbol_elem: ET.Element, parts: list[str], global_var: bool
) -> None:
    """Collect dot-separated symbol path from a Symbol element."""
    segments: list[str] = []
    for child in symbol_elem:
        tag = strip_ns(child.tag)
        if tag == "Component":
            name = child.get("Name", "")
            has_quotes = False
            # Check for array index children: Token "[" + Access + Token "]"
            array_index = ""
            for attr in child:
                atag = strip_ns(attr.tag)
                if (
                    atag == "BooleanAttribute"
                    and attr.get("Name") == "HasQuotes"
                ):
                    has_quotes = attr.text == "true"
                elif atag == "Token" and attr.get("Text") == "[":
                    # Start of array index — collect until "]"
                    array_index = _collect_array_index(child)
            if global_var and has_quotes:
                segments.append(f'"{name}"')
            else:
                seg = name
                if array_index:
                    seg += "[" + array_index + "]"
                segments.append(seg)
        elif tag == "Token":
            tok = child.get("Text", "")
            if tok == ".":
                pass  # dot separator handled by join
            elif tok in ("[", "]"):
                pass  # handled by Component children
            else:
                segments.append(tok)
        elif tag == "Access" and child.get("AccessModifier") == "Array":
            # Array access: [index] via AccessModifier (older format)
            inner_parts: list[str] = []
            for sub in child:
                sub_tag = strip_ns(sub.tag)
                if sub_tag == "Symbol":
                    _collect_symbol_path(sub, inner_parts, False)
                elif sub_tag == "Access":
                    _reconstruct_access(sub, inner_parts)
            if segments:
                segments[-1] += "[" + "".join(inner_parts) + "]"
    parts.append(".".join(segments))


def _collect_array_index(component_elem: ET.Element) -> str:
    """Extract array index from Component children: Token '[' + Access + Token ']'."""
    index_parts: list[str] = []
    in_bracket = False
    for child in component_elem:
        tag = strip_ns(child.tag)
        if tag == "Token":
            tok = child.get("Text", "")
            if tok == "[":
                in_bracket = True
                continue
            elif tok == "]":
                break
            elif in_bracket:
                index_parts.append(tok)
        elif tag == "Access" and in_bracket:
            _reconstruct_access(child, index_parts)
    return "".join(index_parts)


def _reconstruct_call(call_elem: ET.Element, parts: list[str]) -> None:
    """Reconstruct a block call from Access Scope=Call."""
    for child in call_elem:
        tag = strip_ns(child.tag)
        if tag in ("CallInfo", "Instruction"):
            # CallInfo: standard block calls
            # Instruction: library/system block calls (e.g., Program_Alarm)
            block_name = child.get("Name", "")
            if block_name:
                parts.append(f'"{block_name}"')
            # Instance DB / local instance
            for sub in child:
                sub_tag = strip_ns(sub.tag)
                if sub_tag == "Instance":
                    inst_parts: list[str] = []
                    for inst_child in sub:
                        if strip_ns(inst_child.tag) == "Component":
                            inst_parts.append(inst_child.get("Name", ""))
                    if inst_parts:
                        inst_name = ".".join(inst_parts)
                        inst_scope = sub.get("Scope", "")
                        if inst_scope == "LocalVariable":
                            inst_name = "#" + inst_name
                        parts.append(f", {inst_name}")
                elif sub_tag == "Token":
                    parts.append(sub.get("Text", ""))
                elif sub_tag == "Parameter":
                    pname = sub.get("Name", "")
                    parts.append(pname)
                    has_children = False
                    for param_child in sub:
                        has_children = True
                        ptag = strip_ns(param_child.tag)
                        if ptag == "Access":
                            _reconstruct_access(param_child, parts)
                        elif ptag == "Token":
                            parts.append(param_child.get("Text", ""))
                        elif ptag == "Blank":
                            num = param_child.get("Num")
                            parts.append(" " * int(num) if num else " ")
                        elif ptag == "NewLine":
                            num = param_child.get("Num")
                            parts.append("\n" * int(num) if num else "\n")
                        elif ptag == "LineComment":
                            for lc in param_child:
                                if strip_ns(lc.tag) == "Text" and lc.text:
                                    parts.append(f" // {lc.text}")
                    if not has_children:
                        parts.append(" := ")
                    # Add Section/Type annotation for call parameters
                    psection = sub.get("Section", "")
                    ptype = sub.get("Type", "")
                    if psection or ptype:
                        label = f"{psection}" if psection else ""
                        if ptype:
                            label = f"{label}/{ptype}" if label else ptype
                        parts.append(f"  [{label}]")
                elif sub_tag == "NamelessParameter":
                    # Nested function call arguments (no name prefix)
                    for param_child in sub:
                        ptag = strip_ns(param_child.tag)
                        if ptag == "Access":
                            _reconstruct_access(param_child, parts)
                        elif ptag == "Token":
                            parts.append(param_child.get("Text", ""))
                        elif ptag == "Blank":
                            num = param_child.get("Num")
                            parts.append(" " * int(num) if num else " ")
                        elif ptag == "NewLine":
                            num = param_child.get("Num")
                            parts.append("\n" * int(num) if num else "\n")
                elif sub_tag == "NewLine":
                    num = sub.get("Num")
                    parts.append("\n" * int(num) if num else "\n")
                elif sub_tag == "Blank":
                    num = sub.get("Num")
                    parts.append(" " * int(num) if num else " ")
        elif tag == "Token":
            parts.append(child.get("Text", ""))


# ---------------------------------------------------------------------------
# STL code reconstruction (StatementList v4)
# ---------------------------------------------------------------------------
def reconstruct_stl(stl_elem: ET.Element) -> str:
    """Reconstruct readable STL code from tokenized StatementList XML."""
    parts: list[str] = []
    for stmt in stl_elem:
        tag = strip_ns(stmt.tag)
        if tag != "StlStatement":
            continue

        # STL token
        token_elem = find_ns(stmt, "StlToken")
        if token_elem is not None:
            token_text = token_elem.get("Text", "")
            if token_text == "EMPTY_LINE":
                parts.append("\n")
                continue
            if token_text == "COMMENT":
                # Line comment: text is in LineComment > Text grandchild
                for child in stmt:
                    child_tag = strip_ns(child.tag)
                    if child_tag == "LineComment":
                        for txt in child:
                            if strip_ns(txt.tag) == "Text" and txt.text:
                                parts.append(f"      //{txt.text}\n")
                        break
                continue
            # Map XML token names to real STL mnemonics
            token_map: dict[str, str] = {
                "Assign": "=",
                "A_BRACK": "A(",
                "AN_BRACK": "AN(",
                "O_BRACK": "O(",
                "ON_BRACK": "ON(",
                "BRACKET": ")",
                "NOP_0": "NOP 0",
                "ADD_R": "+R",
                "SUB_R": "-R",
                "MUL_R": "*R",
                "DIV_R": "/R",
                "Rise": "FP",
                "Fall": "FN",
                "OnDelay": "SD",
                "OffDelay": "SF",
            }
            token_text = token_map.get(token_text, token_text)
            # Closing bracket has no operand after it
            if token_text == ")":
                parts.append("      )\n")
                continue
            parts.append(f"      {token_text}     ")

        # Operand (Access elements)
        for child in stmt:
            child_tag = strip_ns(child.tag)
            if child_tag == "StlToken":
                continue
            if child_tag == "Access":
                scope = child.get("Scope", "")
                access_comment = ""
                if scope == "GlobalVariable":
                    sym_parts: list[str] = []
                    for sym in child:
                        if strip_ns(sym.tag) == "Symbol":
                            _collect_stl_symbol(sym, sym_parts)
                        elif strip_ns(sym.tag) == "LineComment":
                            for txt in sym:
                                if strip_ns(txt.tag) == "Text" and txt.text:
                                    access_comment = txt.text.strip()
                    parts.append("".join(sym_parts))
                elif scope == "LocalVariable":
                    sym_parts = ["#"]
                    for sym in child:
                        if strip_ns(sym.tag) == "Symbol":
                            _collect_stl_symbol(sym, sym_parts)
                        elif strip_ns(sym.tag) == "LineComment":
                            for txt in sym:
                                if strip_ns(txt.tag) == "Text" and txt.text:
                                    access_comment = txt.text.strip()
                    parts.append("".join(sym_parts))
                elif scope == "LiteralConstant":
                    for const in child:
                        if strip_ns(const.tag) == "Constant":
                            val = find_ns(const, "ConstantValue")
                            if val is not None and val.text:
                                parts.append(val.text)
                        elif strip_ns(const.tag) == "LineComment":
                            for txt in const:
                                if strip_ns(txt.tag) == "Text" and txt.text:
                                    access_comment = txt.text.strip()
                elif scope == "TypedConstant":
                    for const in child:
                        if strip_ns(const.tag) == "Constant":
                            val = find_ns(const, "ConstantValue")
                            if val is not None and val.text:
                                parts.append(val.text)
                        elif strip_ns(const.tag) == "LineComment":
                            for txt in const:
                                if strip_ns(txt.tag) == "Text" and txt.text:
                                    access_comment = txt.text.strip()
                elif scope == "Call":
                    # CALL instruction - find CallInfo with block name
                    for sub in child:
                        sub_tag = strip_ns(sub.tag)
                        if sub_tag == "CallInfo":
                            call_name = sub.get("Name", "")
                            if call_name:
                                parts.append(f'"{call_name}"')
                            # Instance DB (for FB calls)
                            for inst in sub:
                                if strip_ns(inst.tag) == "Instance":
                                    inst_scope = inst.get("Scope", "")
                                    if inst_scope == "GlobalVariable":
                                        # Instance has Component children directly
                                        inst_name = ""
                                        for ic in inst:
                                            if strip_ns(ic.tag) == "Component":
                                                inst_name = ic.get("Name", "")
                                            elif strip_ns(ic.tag) == "Symbol":
                                                isegs: list[str] = []
                                                _collect_stl_symbol(ic, isegs)
                                                inst_name = "".join(isegs)
                                        if inst_name:
                                            parts.append(f', "{inst_name}"')
                            # Parameters
                            for param in sub:
                                ptag = strip_ns(param.tag)
                                if ptag == "Parameter":
                                    pname = param.get("Name", "")
                                    parts.append(f"\n        {pname} := ")
                                    for pc in param:
                                        pc_tag = strip_ns(pc.tag)
                                        if pc_tag == "Access":
                                            pc_scope = pc.get("Scope", "")
                                            if pc_scope == "GlobalVariable":
                                                sp: list[str] = []
                                                for sym in pc:
                                                    if strip_ns(sym.tag) == "Symbol":
                                                        _collect_stl_symbol(sym, sp)
                                                parts.append("".join(sp))
                                            elif pc_scope == "LocalVariable":
                                                parts.append("#")
                                                for sym in pc:
                                                    if strip_ns(sym.tag) == "Symbol":
                                                        _collect_stl_symbol(sym, parts)
                                            elif pc_scope in (
                                                "LiteralConstant",
                                                "TypedConstant",
                                            ):
                                                for const in pc:
                                                    if (
                                                        strip_ns(const.tag)
                                                        == "Constant"
                                                    ):
                                                        val = find_ns(
                                                            const, "ConstantValue"
                                                        )
                                                        if (
                                                            val is not None
                                                            and val.text
                                                        ):
                                                            parts.append(val.text)
                                            elif pc_scope == "Address":
                                                for addr in pc:
                                                    if strip_ns(addr.tag) == "Address":
                                                        parts.append(
                                                            _format_address(addr)
                                                        )
                                        elif pc_tag == "LineComment":
                                            for txt in pc:
                                                if (
                                                    strip_ns(txt.tag) == "Text"
                                                    and txt.text
                                                ):
                                                    parts.append(f" // {txt.text}")
                                    # Add Section/Type annotation for call parameters
                                    psection = param.get("Section", "")
                                    ptype = param.get("Type", "")
                                    if psection or ptype:
                                        label = (
                                            f"{psection}" if psection else ""
                                        )
                                        if ptype:
                                            label = (
                                                f"{label}/{ptype}"
                                                if label
                                                else ptype
                                            )
                                        parts.append(f"  [{label}]")
                        elif sub_tag == "Instruction":
                            instr_name = sub.get("Name", "")
                            if instr_name:
                                parts.append(instr_name)
                            # Instance (local variable like #S_1)
                            for inst in sub:
                                if strip_ns(inst.tag) == "Instance":
                                    inst_scope = inst.get("Scope", "")
                                    inst_name = ""
                                    for ic in inst:
                                        if strip_ns(ic.tag) == "Component":
                                            inst_name = ic.get("Name", "")
                                    if inst_name:
                                        if inst_scope == "LocalVariable":
                                            parts.append(f", #{inst_name}")
                                        else:
                                            parts.append(f', "{inst_name}"')
                            # Parameters
                            for param in sub:
                                ptag = strip_ns(param.tag)
                                if ptag == "Parameter":
                                    pname = param.get("Name", "")
                                    parts.append(f"\n        {pname} := ")
                                    for pc in param:
                                        pc_tag = strip_ns(pc.tag)
                                        if pc_tag == "Access":
                                            pc_scope = pc.get("Scope", "")
                                            if pc_scope == "GlobalVariable":
                                                sp = []
                                                for sym in pc:
                                                    if (
                                                        strip_ns(sym.tag)
                                                        == "Symbol"
                                                    ):
                                                        _collect_stl_symbol(sym, sp)
                                                parts.append("".join(sp))
                                            elif pc_scope == "LocalVariable":
                                                sp = ["#"]
                                                for sym in pc:
                                                    if (
                                                        strip_ns(sym.tag)
                                                        == "Symbol"
                                                    ):
                                                        _collect_stl_symbol(sym, sp)
                                                parts.append("".join(sp))
                                            elif pc_scope in (
                                                "LiteralConstant",
                                                "TypedConstant",
                                            ):
                                                for const in pc:
                                                    if (
                                                        strip_ns(const.tag)
                                                        == "Constant"
                                                    ):
                                                        val = find_ns(
                                                            const, "ConstantValue"
                                                        )
                                                        if (
                                                            val is not None
                                                            and val.text
                                                        ):
                                                            parts.append(val.text)
                if access_comment:
                    parts.append(f"  //{access_comment}")
            if child_tag == "Comment":
                for txt in child:
                    if strip_ns(txt.tag) == "Text" and txt.text:
                        parts.append(f"  //{txt.text}")
            if child_tag == "LineComment":
                for txt in child:
                    if strip_ns(txt.tag) == "Text" and txt.text:
                        parts.append(f"  //{txt.text.strip()}")

        parts.append("\n")

    return "".join(parts).rstrip()


def _collect_stl_symbol(symbol_elem: ET.Element, parts: list[str]) -> None:
    """Collect symbol path for STL operand."""
    segments: list[str] = []
    for child in symbol_elem:
        tag = strip_ns(child.tag)
        if tag == "Component":
            name = child.get("Name", "")
            is_array = child.get("AccessModifier") == "Array"
            # First component (DB/FC/FB name) is always quoted in STL
            # Subsequent components check HasQuotes attribute
            if not segments:
                seg = f'"{name}"'
            else:
                has_quotes = False
                for attr in child:
                    if (
                        strip_ns(attr.tag) == "BooleanAttribute"
                        and attr.get("Name") == "HasQuotes"
                    ):
                        has_quotes = attr.text == "true"
                seg = f'"{name}"' if has_quotes else name
            # Handle array index on Component
            if is_array:
                idx = _extract_array_index(child)
                seg += f"[{idx}]"
            segments.append(seg)
        elif tag == "Access" and child.get("AccessModifier") == "Array":
            idx = _extract_access_value(child)
            if segments:
                segments[-1] += f"[{idx}]"
    parts.append(".".join(segments))


def _extract_array_index(elem: ET.Element) -> str:
    """Extract array index from child Access elements (LiteralConstant/TypedConstant/Symbol)."""
    for sub in elem:
        if strip_ns(sub.tag) == "Access":
            return _extract_access_value(sub)
    return ""


def _extract_access_value(access_elem: ET.Element) -> str:
    """Extract value from an Access element (constant or symbol)."""
    scope = access_elem.get("Scope", "")
    if scope in ("LiteralConstant", "TypedConstant"):
        for const in access_elem:
            if strip_ns(const.tag) == "Constant":
                val = find_ns(const, "ConstantValue")
                if val is not None and val.text:
                    return val.text
    elif scope == "GlobalVariable":
        inner: list[str] = []
        for sym in access_elem:
            if strip_ns(sym.tag) == "Symbol":
                _collect_stl_symbol(sym, inner)
        return "".join(inner)
    elif scope == "LocalVariable":
        inner = ["#"]
        for sym in access_elem:
            if strip_ns(sym.tag) == "Symbol":
                _collect_stl_symbol(sym, inner)
        return "".join(inner)
    return ""


def _format_address(addr_elem: ET.Element) -> str:
    """Format an <Address Area="DB" Type="DWord" BlockNumber="1002" BitOffset="64" /> element."""
    area = addr_elem.get("Area", "")
    dtype = addr_elem.get("Type", "")
    blk = addr_elem.get("BlockNumber", "")
    offset_str = addr_elem.get("BitOffset", "0")
    try:
        offset = int(offset_str)
    except (ValueError, TypeError):
        offset = 0
    if area == "DB" and blk:
        byte_off = offset // 8
        tp = IEC_TYPE_MAP.get(dtype, "B")
        if dtype == "Bool":
            return f"%DB{blk}.DB{tp}{byte_off}.{offset % 8}"
        else:
            return f"%DB{blk}.DB{tp}{byte_off}"
    elif area in ("I", "Q", "M"):
        byte_off = offset // 8
        tp = IEC_TYPE_MAP_FLAT.get(dtype, "")
        if dtype == "Bool":
            return f"%{area}{byte_off}.{offset % 8}"
        else:
            return f"%{area}{tp}{byte_off}"
    else:
        return f"[{area}:{dtype}@{offset}]"


# ---------------------------------------------------------------------------
# Code extraction from CompileUnit
# ---------------------------------------------------------------------------
def _extract_network_title(cu_elem: ET.Element) -> str:
    """Extract network title from CompileUnit's ObjectList."""
    for child in cu_elem:
        if strip_ns(child.tag) == "ObjectList":
            for obj in child:
                if (
                    strip_ns(obj.tag) == "MultilingualText"
                    and obj.get("CompositionName") == "Title"
                ):
                    for item in obj:
                        if strip_ns(item.tag) == "ObjectList":
                            for mti in item:
                                if strip_ns(mti.tag) == "MultilingualTextItem":
                                    for al in mti:
                                        if strip_ns(al.tag) == "AttributeList":
                                            text = None
                                            for attr in al:
                                                if (
                                                    strip_ns(attr.tag) == "Text"
                                                    and attr.text
                                                ):
                                                    text = attr.text
                                            if text:
                                                return text
    return ""


def _extract_network_comment(cu_elem: ET.Element) -> str:
    """Extract network comment from CompileUnit's ObjectList."""
    for child in cu_elem:
        if strip_ns(child.tag) == "ObjectList":
            for obj in child:
                if (
                    strip_ns(obj.tag) == "MultilingualText"
                    and obj.get("CompositionName") == "Comment"
                ):
                    for item in obj:
                        if strip_ns(item.tag) == "ObjectList":
                            for mti in item:
                                if strip_ns(mti.tag) == "MultilingualTextItem":
                                    for al in mti:
                                        if strip_ns(al.tag) == "AttributeList":
                                            text = None
                                            for attr in al:
                                                if (
                                                    strip_ns(attr.tag) == "Text"
                                                    and attr.text
                                                ):
                                                    text = attr.text
                                            if text:
                                                return text
    return ""


def extract_code_from_block(block_elem: ET.Element) -> list[dict]:
    """Extract all code networks from block's CompileUnits."""
    networks: list[dict] = []

    for cu in block_elem.iter():
        cu_tag = strip_ns(cu.tag)
        if cu_tag not in ("CompileUnit", "SW.Blocks.CompileUnit"):
            continue

        # Extract network metadata
        title = _extract_network_title(cu)
        net_comment = _extract_network_comment(cu)

        # Find NetworkSource and ProgrammingLanguage
        ns_elem: ET.Element | None = None
        net_lang = ""
        for child in cu:
            if strip_ns(child.tag) == "AttributeList":
                for attr in child:
                    if strip_ns(attr.tag) == "NetworkSource":
                        ns_elem = attr
                    elif strip_ns(attr.tag) == "ProgrammingLanguage":
                        net_lang = (attr.text or "").strip()

        if ns_elem is None:
            continue

        # Check for StructuredText or StatementList
        for lang_elem in ns_elem:
            lang_tag = strip_ns(lang_elem.tag)

            if lang_tag == "StructuredText":
                code = reconstruct_scl(lang_elem)
                if code:
                    networks.append(
                        {
                            "language": "SCL",
                            "code": code,
                            "title": title,
                            "comment": net_comment,
                            "net_lang": net_lang,
                        }
                    )

            elif lang_tag == "StatementList":
                code = reconstruct_stl(lang_elem)
                if code:
                    networks.append(
                        {
                            "language": "STL",
                            "code": code,
                            "title": title,
                            "comment": net_comment,
                            "net_lang": net_lang,
                        }
                    )

    return networks


# ---------------------------------------------------------------------------
# Tag and call reference extraction from code
# ---------------------------------------------------------------------------
def extract_global_vars_from_xml(block_elem: ET.Element) -> set[str]:
    """Extract all GlobalVariable references directly from XML for reliable tag xref."""
    refs: set[str] = set()
    for access in block_elem.iter():
        if strip_ns(access.tag) != "Access":
            continue
        if access.get("Scope") == "GlobalVariable":
            path_parts: list[str] = []
            for child in access:
                if strip_ns(child.tag) == "Symbol":
                    _collect_var_path(child, path_parts)
            if path_parts:
                refs.add(".".join(path_parts))
        elif access.get("Scope") == "Call":
            # Extract called block name
            for child in access:
                ctag = strip_ns(child.tag)
                if ctag == "CallInfo":
                    # STL format: <CallInfo Name="FC_LIJN" BlockType="FC" />
                    call_name = child.get("Name", "")
                    if call_name:
                        refs.add("CALL:" + call_name)
                    # SCL format: <Instance Scope="GlobalVariable"><Symbol>...
                    for sub in child:
                        if strip_ns(sub.tag) == "Instance":
                            for inst in sub:
                                if strip_ns(inst.tag) == "Symbol":
                                    name_parts: list[str] = []
                                    _collect_var_path(inst, name_parts)
                                    if name_parts:
                                        refs.add("CALL:" + ".".join(name_parts))
    return refs


def _collect_var_path(symbol_elem: ET.Element, parts: list[str]) -> None:
    """Collect variable path from Symbol element."""
    segments: list[str] = []
    for child in symbol_elem:
        tag = strip_ns(child.tag)
        if tag == "Component":
            segments.append(child.get("Name", ""))
        elif tag == "Access" and child.get("AccessModifier") == "Array":
            # Skip array index, just note it's array access
            pass
    if segments:
        parts.append(".".join(segments))


def classify_references(
    global_refs: set[str],
) -> tuple[list[str], list[str]]:
    """Split global refs into tag references and block calls."""
    tag_refs: list[str] = []
    calls: list[str] = []

    for ref in global_refs:
        if ref.startswith("CALL:"):
            calls.append(ref[5:])
        else:
            tag_refs.append(ref)

    return sorted(set(tag_refs)), sorted(set(calls))


# ---------------------------------------------------------------------------
# PLC tag table parsing
# ---------------------------------------------------------------------------
def parse_tag_tables(
    tags_dir: str | Path, lang: str = "en-US"
) -> tuple[dict[str, dict], list[str]]:
    """Parse all PLC tag table XML files in directory."""
    all_tags: dict[str, dict] = {}
    tag_tables: list[str] = []  # List of all table names (including empty ones)

    tags_path = Path(tags_dir)
    if not tags_path.is_dir():
        return all_tags, tag_tables

    for f in sorted(tags_path.iterdir()):
        if not f.name.lower().endswith(".xml"):
            continue

        filepath = str(f)
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
        except ET.ParseError:
            continue

        # Find table name
        table_name = ""
        for child in root:
            ctag = strip_ns(child.tag)
            if ctag == "SW.Tags.PlcTagTable":
                for attr_list in child:
                    if strip_ns(attr_list.tag) == "AttributeList":
                        name_elem = find_ns(attr_list, "Name")
                        if name_elem is not None and name_elem.text:
                            table_name = name_elem.text.strip()
                break

        if not table_name:
            table_name = f.stem

        tag_tables.append(table_name)

        # Parse tags — elements are named SW.Tags.PlcTag (not namespaced)
        for tag_elem in root.iter():
            tag_local = strip_ns(tag_elem.tag)
            if tag_local != "SW.Tags.PlcTag":
                continue

            attrs: ET.Element | None = None
            for child in tag_elem:
                if strip_ns(child.tag) == "AttributeList":
                    attrs = child
                    break

            if attrs is None:
                continue

            name = ""
            data_type = ""
            address = ""
            comment = ""

            for field in attrs:
                field_tag = strip_ns(field.tag)
                if field_tag == "Name":
                    name = (field.text or "").strip()
                elif field_tag == "DataTypeName":
                    data_type = (field.text or "").strip()
                elif field_tag == "LogicalAddress":
                    address = (field.text or "").strip()

            # Comment
            for child in tag_elem:
                if strip_ns(child.tag) == "ObjectList":
                    for obj in child:
                        if strip_ns(obj.tag) == "MultilingualText":
                            for sub in obj:
                                if strip_ns(sub.tag) == "AttributeList":
                                    pass
                                elif strip_ns(sub.tag) == "ObjectList":
                                    for item in sub:
                                        if (
                                            strip_ns(item.tag)
                                            == "MultilingualTextItem"
                                        ):
                                            culture = ""
                                            text = ""
                                            for ia in item:
                                                ia_tag = strip_ns(ia.tag)
                                                if ia_tag == "AttributeList":
                                                    for ia_field in ia:
                                                        if (
                                                            strip_ns(ia_field.tag)
                                                            == "Culture"
                                                        ):
                                                            culture = (
                                                                ia_field.text or ""
                                                            ).strip()
                                                elif ia_tag == "Text":
                                                    text = (ia.text or "").strip()
                                            if culture == lang and text:
                                                comment = text
                                                break
                                    break

            if name:
                all_tags[name] = {
                    "name": name,
                    "table": table_name,
                    "data_type": data_type,
                    "address": address,
                    "comment": comment,
                }

    return all_tags, tag_tables


# ---------------------------------------------------------------------------
# Single block file parser
# ---------------------------------------------------------------------------
def parse_block_file(
    filepath: str | Path,
    rel_path: str,
    lang: str = "en-US",
) -> dict | None:
    """Parse a single exported block XML file.

    Returns a dict of block data, ``None`` if the file is not a recognised
    block type, or a dict with an ``"error"`` key on parse failure.
    """
    try:
        tree = ET.parse(str(filepath))
    except ET.ParseError as e:
        return {"error": f"XML parse error: {e}", "file": rel_path}

    root = tree.getroot()

    # Detect block type
    block_type, block_elem = detect_block_type(root)
    if block_type is None:
        return None  # Not a block file

    # Metadata from AttributeList
    block_name = ""
    block_number = ""
    prog_language = ""
    memory_layout = ""
    header_author = ""
    header_family = ""
    header_name = ""
    header_version = ""
    db_opc_ua = ""
    db_webserver = ""
    instance_of_name = ""
    instance_of_type = ""
    comment = ""
    auto_number = ""
    iec_check_enabled = ""
    set_eno_auto = ""
    uda_enable_tag_readback = ""
    block_namespace = ""
    engineering_version = ""
    memory_reserve = ""
    secondary_type = ""
    is_failsafe_compliant = ""
    is_only_load_memory = ""
    is_retain_mem_res = ""
    is_write_protected = ""
    block_text = ""
    cyclic_time = ""
    phase_offset = ""
    system_lib_version = ""

    for child in block_elem:
        if strip_ns(child.tag) == "AttributeList":
            for attr in child:
                attr_tag = strip_ns(attr.tag)
                if attr_tag == "Name":
                    block_name = (attr.text or "").strip()
                elif attr_tag == "Number":
                    block_number = (attr.text or "").strip()
                elif attr_tag == "ProgrammingLanguage":
                    prog_language = (attr.text or "").strip()
                elif attr_tag == "MemoryLayout":
                    memory_layout = (attr.text or "").strip()
                elif attr_tag == "MemoryReserve":
                    memory_reserve = (attr.text or "").strip()
                elif attr_tag == "HeaderAuthor":
                    header_author = (attr.text or "").strip()
                elif attr_tag == "HeaderFamily":
                    header_family = (attr.text or "").strip()
                elif attr_tag == "HeaderName":
                    header_name = (attr.text or "").strip()
                elif attr_tag == "HeaderVersion":
                    header_version = (attr.text or "").strip()
                elif attr_tag == "SecondaryType":
                    secondary_type = (attr.text or "").strip()
                elif attr_tag == "IsFailsafeCompliant":
                    is_failsafe_compliant = (attr.text or "").strip()
                elif attr_tag == "DBAccessibleFromOPCUA":
                    db_opc_ua = (attr.text or "").strip()
                elif attr_tag == "DBAccessibleFromWebserver":
                    db_webserver = (attr.text or "").strip()
                elif attr_tag == "InstanceOfName":
                    instance_of_name = (attr.text or "").strip()
                elif attr_tag == "InstanceOfType":
                    instance_of_type = (attr.text or "").strip()
                elif attr_tag == "AutoNumber":
                    auto_number = (attr.text or "").strip()
                elif attr_tag == "IsIECCheckEnabled":
                    iec_check_enabled = (attr.text or "").strip()
                elif attr_tag == "SetENOAutomatically":
                    set_eno_auto = (attr.text or "").strip()
                elif attr_tag == "UDAEnableTagReadback":
                    uda_enable_tag_readback = (attr.text or "").strip()
                elif attr_tag == "IsOnlyStoredInLoadMemory":
                    is_only_load_memory = (attr.text or "").strip()
                elif attr_tag == "IsRetainMemResEnabled":
                    is_retain_mem_res = (attr.text or "").strip()
                elif attr_tag == "IsWriteProtectedInAS":
                    is_write_protected = (attr.text or "").strip()
                elif attr_tag == "Namespace":
                    block_namespace = (attr.text or "").strip()
                elif attr_tag == "Text":
                    block_text = (attr.text or "").strip()
                elif attr_tag == "CyclicTime":
                    cyclic_time = (attr.text or "").strip()
                elif attr_tag == "PhaseOffset":
                    phase_offset = (attr.text or "").strip()
                elif attr_tag == "OfSystemLibVersion":
                    system_lib_version = (attr.text or "").strip()

    # Block ID attribute (from block element, e.g. SW.Blocks.FC ID="0")
    block_id = block_elem.get("ID", "")

    # Extract DocumentInfo from root level
    export_setting = ""
    created_timestamp = ""
    installed_products: list[dict] = []
    for child in root:
        tag = strip_ns(child.tag)
        if tag == "Engineering":
            engineering_version = (child.get("version", "") or "").strip()
        elif tag == "DocumentInfo":
            for di in child:
                di_tag = strip_ns(di.tag)
                if di_tag == "Created":
                    created_timestamp = (di.text or "").strip()
                elif di_tag == "ExportSetting":
                    export_setting = (di.text or "").strip()
                elif di_tag == "InstalledProducts":
                    for prod in di:
                        ptag = strip_ns(prod.tag)
                        if ptag in ("Product", "OptionPackage"):
                            pname = ""
                            pver = ""
                            for pf in prod:
                                pftag = strip_ns(pf.tag)
                                if pftag == "DisplayName":
                                    pname = (pf.text or "").strip()
                                elif pftag == "DisplayVersion":
                                    pver = (pf.text or "").strip()
                            if pname:
                                installed_products.append(
                                    {
                                        "name": pname,
                                        "version": pver,
                                        "type": (
                                            "option"
                                            if ptag == "OptionPackage"
                                            else "product"
                                        ),
                                    }
                                )

    # Check UDABlockProperties (empty element vs has children)
    has_uda_properties = False
    for child in block_elem:
        if strip_ns(child.tag) == "AttributeList":
            for attr in child:
                if strip_ns(attr.tag) == "UDABlockProperties":
                    has_uda_properties = len(list(attr)) > 0
                    break

    # Comment, Title, and Culture info from MultilingualText
    block_title = ""
    cultures: set[str] = set()
    for child in block_elem:
        if strip_ns(child.tag) == "ObjectList":
            for obj in child:
                if strip_ns(obj.tag) == "MultilingualText":
                    comp_name = obj.get("CompositionName", "")
                    for sub in obj:
                        if strip_ns(sub.tag) == "ObjectList":
                            for item in sub:
                                if strip_ns(item.tag) == "MultilingualTextItem":
                                    culture = ""
                                    text = ""
                                    for field in item:
                                        ftag = strip_ns(field.tag)
                                        if ftag == "AttributeList":
                                            for af in field:
                                                if strip_ns(af.tag) == "Culture":
                                                    culture = (
                                                        af.text or ""
                                                    ).strip()
                                                elif strip_ns(af.tag) == "Text":
                                                    text = (af.text or "").strip()
                                        elif ftag == "Text":
                                            text = (field.text or "").strip()
                                    if culture:
                                        cultures.add(culture)
                                    if culture == lang and text:
                                        if comp_name == "Title":
                                            block_title = text
                                        else:
                                            comment = text

    # Fallback: use filename as block name (for STRUCT/UDT types)
    if not block_name:
        block_name = os.path.splitext(os.path.basename(rel_path))[0]

    # Fallback language for types without code
    if not prog_language and block_type in ("STRUCT", "UDT"):
        prog_language = block_type

    # Folder path (parent directory of the file)
    folder = os.path.dirname(rel_path).replace("\\", "/")
    if folder == ".":
        folder = ""

    # Interface
    interface = parse_interface(block_elem, lang=lang)

    # Code
    networks = extract_code_from_block(block_elem)
    code_parts: list[str] = []
    for i, n in enumerate(networks, 1):
        title = n.get("title", "")
        net_comment = n.get("comment", "")
        header = f"Network {i}"
        if title:
            header += f": {title}"
        code_parts.append(f"// {header}")
        if net_comment:
            code_parts.append(f"// Comment: {net_comment}")
        code_parts.append(n["code"])
    full_code = "\n".join(code_parts)

    # Global variable references from XML (most reliable)
    global_refs = extract_global_vars_from_xml(block_elem)
    tag_refs, calls = classify_references(global_refs)

    return {
        "block_name": block_name,
        "block_number": int(block_number) if block_number.isdigit() else block_number,
        "block_type": block_type,
        "programming_language": prog_language,
        "memory_layout": memory_layout,
        "header_author": header_author,
        "header_family": header_family,
        "header_name": header_name,
        "header_version": header_version,
        "db_opc_ua": db_opc_ua,
        "db_webserver": db_webserver,
        "instance_of_name": instance_of_name,
        "instance_of_type": instance_of_type,
        "auto_number": auto_number,
        "iec_check_enabled": iec_check_enabled,
        "set_eno_automatically": set_eno_auto,
        "uda_enable_tag_readback": uda_enable_tag_readback,
        "has_uda_properties": has_uda_properties,
        "namespace": block_namespace,
        "block_id": block_id,
        "memory_reserve": memory_reserve,
        "secondary_type": secondary_type,
        "is_failsafe_compliant": is_failsafe_compliant,
        "is_only_load_memory": is_only_load_memory,
        "is_retain_mem_res": is_retain_mem_res,
        "is_write_protected": is_write_protected,
        "block_text": block_text,
        "cyclic_time": cyclic_time,
        "phase_offset": phase_offset,
        "system_lib_version": system_lib_version,
        "engineering_version": engineering_version,
        "export_setting": export_setting,
        "created": created_timestamp,
        "installed_products": installed_products,
        "cultures": sorted(cultures),
        "comment": comment,
        "block_title": block_title,
        "folder": folder,
        "source_file": rel_path.replace("\\", "/"),
        "interface": interface,
        "interface_count": count_interface_vars(interface),
        "networks": networks,
        "code": full_code,
        "tag_references": tag_refs,
        "calls": calls,
    }


# ---------------------------------------------------------------------------
# Analysis: tag xref and call tree
# ---------------------------------------------------------------------------
def build_tag_xref(blocks: list[dict]) -> dict[str, list[dict]]:
    """Build tag cross-reference: tag_name -> list of blocks using it."""
    xref: dict[str, list[dict]] = defaultdict(list)
    for block in blocks:
        for tag in block.get("tag_references", []):
            xref[tag].append(
                {
                    "block": block["block_name"],
                    "block_type": block["block_type"],
                }
            )
    return dict(sorted(xref.items()))


def build_call_tree(
    blocks: list[dict],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build call tree: forward (block -> calls) and reverse (block -> called_by)."""
    forward: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = defaultdict(list)

    for block in blocks:
        name = block["block_name"]
        calls = block.get("calls", [])
        forward[name] = calls
        for called in calls:
            reverse[called].append(name)

    return forward, {k: sorted(set(v)) for k, v in reverse.items()}


# ---------------------------------------------------------------------------
# Helper: convert raw interface dict to BlockInterface Pydantic model
# ---------------------------------------------------------------------------
def _dict_to_interface_member(data: dict) -> InterfaceMember:
    """Convert a raw member dict (from parse_member) to an InterfaceMember model."""
    children = [
        _dict_to_interface_member(child) for child in data.get("members", [])
    ]
    return InterfaceMember(
        name=data.get("name", ""),
        data_type=data.get("data_type", ""),
        comment=data.get("comment", ""),
        start_value=data.get("start_value", ""),
        remanence=data.get("remanence", ""),
        accessibility=data.get("accessibility", ""),
        members=children,
        subelement_values=data.get("subelement_values", []),
    )


def _raw_interface_to_model(raw: dict) -> BlockInterface:
    """Convert a raw interface dict to a BlockInterface Pydantic model."""
    return BlockInterface(
        inputs=[_dict_to_interface_member(m) for m in raw.get("inputs", [])],
        outputs=[_dict_to_interface_member(m) for m in raw.get("outputs", [])],
        inouts=[_dict_to_interface_member(m) for m in raw.get("inouts", [])],
        statics=[_dict_to_interface_member(m) for m in raw.get("statics", [])],
        temps=[_dict_to_interface_member(m) for m in raw.get("temps", [])],
        constants=[_dict_to_interface_member(m) for m in raw.get("constants", [])],
        returns=[_dict_to_interface_member(m) for m in raw.get("returns", [])],
    )


def _raw_block_to_model(raw: dict) -> BlockResult:
    """Convert a raw block dict (from parse_block_file) to a BlockResult model."""
    interface_model = _raw_interface_to_model(raw.get("interface", {}))
    networks = [
        Network(
            language=n.get("language", ""),
            code=n.get("code", ""),
            title=n.get("title", ""),
            comment=n.get("comment", ""),
        )
        for n in raw.get("networks", [])
    ]
    return BlockResult(
        block_name=raw.get("block_name", ""),
        block_number=raw.get("block_number", ""),
        block_type=raw.get("block_type", ""),
        programming_language=raw.get("programming_language", ""),
        comment=raw.get("comment", ""),
        block_title=raw.get("block_title", ""),
        folder=raw.get("folder", ""),
        source_file=raw.get("source_file", ""),
        interface=interface_model,
        interface_count=raw.get("interface_count", 0),
        networks=networks,
        code=raw.get("code", ""),
        tag_references=raw.get("tag_references", []),
        calls=raw.get("calls", []),
        header_author=raw.get("header_author", ""),
        header_family=raw.get("header_family", ""),
        header_name=raw.get("header_name", ""),
        header_version=raw.get("header_version", ""),
        instance_of_name=raw.get("instance_of_name", ""),
        instance_of_type=raw.get("instance_of_type", ""),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def parse_plc_directory(
    blocks_path: str,
    tags_dir: str | None = None,
    lang: str = "en-US",
) -> PlcExtraction:
    """Parse all PLC blocks and tag tables under *blocks_path*.

    Parameters
    ----------
    blocks_path:
        Path to the ``DATA_Program blocks`` directory containing exported
        Openness XML files.
    tags_dir:
        Optional path to the PLC tags directory.  When ``None``, defaults
        to ``<blocks_path>/PLC tags``.
    lang:
        Preferred culture for multi-language comment extraction
        (default ``"en-US"``).

    Returns
    -------
    PlcExtraction
        A fully-populated Pydantic model containing parsed blocks, tag
        cross-references, call tree, and summary statistics.
    """
    blocks_path_resolved = Path(blocks_path).resolve()
    if not blocks_path_resolved.exists():
        logger.error("Path does not exist: %s", blocks_path_resolved)
        return PlcExtraction(
            source_path=str(blocks_path_resolved),
            errors=[{"error": f"Path does not exist: {blocks_path_resolved}"}],
        )

    resolved_tags_dir = (
        Path(tags_dir) if tags_dir else blocks_path_resolved / "PLC tags"
    )

    # --- Discover block files ---
    block_files = find_block_files(str(blocks_path_resolved))
    logger.info("Found %d XML files in %s", len(block_files), blocks_path_resolved)

    # --- Parse tag tables ---
    plc_tags: dict[str, dict] = {}
    tag_table_names: list[str] = []
    if resolved_tags_dir.is_dir():
        plc_tags, tag_table_names = parse_tag_tables(
            str(resolved_tags_dir), lang=lang
        )
        logger.info("Loaded %d PLC tags from %s", len(plc_tags), resolved_tags_dir)

    # Also parse tag tables found in the blocks directory (AI, DI, etc.)
    inline_tags, inline_table_names = parse_tag_tables(
        str(blocks_path_resolved), lang=lang
    )
    for tn in inline_table_names:
        if tn not in tag_table_names:
            tag_table_names.append(tn)
    merged = 0
    for name, detail in inline_tags.items():
        if name not in plc_tags:
            plc_tags[name] = detail
            merged += 1
    if merged:
        logger.info("Added %d additional tags from block directory", merged)

    # --- Parse blocks ---
    blocks_raw: list[dict] = []
    errors: list[dict] = []

    for full_path, rel_path in block_files:
        result = parse_block_file(full_path, rel_path, lang=lang)
        if result is None:
            continue  # Not a block file
        if "error" in result:
            errors.append(result)
            logger.warning("Error parsing %s: %s", rel_path, result["error"])
            continue
        blocks_raw.append(result)

    logger.info("Parsed %d blocks (%d errors)", len(blocks_raw), len(errors))

    # --- Build indexes ---
    tag_xref = build_tag_xref(blocks_raw)
    call_forward, call_reverse = build_call_tree(blocks_raw)

    # Resolve tag references against tag tables
    resolved_tags: dict[str, dict] = {}
    for tag_ref, usage in tag_xref.items():
        # Try exact match first, then base name (before first dot)
        base_name = tag_ref.split(".")[0] if "." in tag_ref else tag_ref
        tag_detail = plc_tags.get(tag_ref) or plc_tags.get(base_name)
        resolved_tags[tag_ref] = {
            "plc_tag_address": (
                tag_detail["address"] if tag_detail else "(not in tag table)"
            ),
            "data_type": tag_detail["data_type"] if tag_detail else "?",
            "used_in": [u["block"] for u in usage],
        }

    # --- Summary ---
    type_counts: dict[str, int] = defaultdict(int)
    lang_counts: dict[str, int] = defaultdict(int)
    total_iface = 0
    total_calls = 0
    total_tag_refs = 0

    for b in blocks_raw:
        type_counts[b["block_type"]] += 1
        lang_counts[b["programming_language"]] += 1
        total_iface += b["interface_count"]
        total_calls += len(b["calls"])
        total_tag_refs += len(b["tag_references"])

    summary = PlcSummary(
        total_blocks=len(blocks_raw),
        fb_count=type_counts.get("FB", 0),
        fc_count=type_counts.get("FC", 0),
        ob_count=type_counts.get("OB", 0),
        db_count=type_counts.get("DB", 0),
        idb_count=type_counts.get("IDB", 0),
        scl_count=lang_counts.get("SCL", 0),
        stl_count=lang_counts.get("STL", 0),
        total_interfaces=total_iface,
        total_calls=total_calls,
        total_tag_refs=total_tag_refs,
        unique_tag_refs=len(tag_xref),
        plc_tags_loaded=len(plc_tags),
    )

    # --- Convert raw dicts to BlockResult models ---
    block_models = [_raw_block_to_model(b) for b in blocks_raw]

    # --- Build final extraction ---
    extraction = PlcExtraction(
        source_path=blocks_path_resolved.as_posix(),
        summary=summary,
        blocks=block_models,
        call_tree=call_forward,
        called_by=call_reverse,
        tag_xref=resolved_tags,
        plc_tags={k: v for k, v in sorted(plc_tags.items())},
        errors=errors,
    )

    logger.info(
        "Extraction complete: %d blocks, %d tags, %d errors",
        summary.total_blocks,
        summary.plc_tags_loaded,
        len(errors),
    )

    return extraction
