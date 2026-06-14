"""Pydantic models for PLC block and HMI screen extraction results.

These models define the structured output of the extraction parsers,
serving as the contract between parsing code, internal tools, and
the future HTTP router.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# PLC Block Extraction Models
# ---------------------------------------------------------------------------

class InterfaceMember(BaseModel):
    """A single variable in a block interface section."""

    model_config = {"extra": "ignore"}

    name: str = ""
    data_type: str = ""
    comment: str = ""
    start_value: str = ""
    remanence: str = ""
    accessibility: str = ""
    members: list[InterfaceMember] = Field(default_factory=list)
    subelement_values: list[dict] = Field(default_factory=list)


class BlockInterface(BaseModel):
    """Block interface sections (inputs, outputs, etc.)."""

    model_config = {"extra": "ignore"}

    inputs: list[InterfaceMember] = Field(default_factory=list)
    outputs: list[InterfaceMember] = Field(default_factory=list)
    inouts: list[InterfaceMember] = Field(default_factory=list)
    statics: list[InterfaceMember] = Field(default_factory=list)
    temps: list[InterfaceMember] = Field(default_factory=list)
    constants: list[InterfaceMember] = Field(default_factory=list)
    returns: list[InterfaceMember] = Field(default_factory=list)


class Network(BaseModel):
    """A single code network within a block."""

    model_config = {"extra": "ignore"}

    language: str = ""
    code: str = ""
    title: str = ""
    comment: str = ""


class BlockResult(BaseModel):
    """A single parsed PLC block (FB, FC, OB, DB, etc.)."""

    model_config = {"extra": "ignore"}

    block_name: str = ""
    block_number: int | str = ""
    block_type: str = ""
    programming_language: str = ""
    comment: str = ""
    block_title: str = ""
    folder: str = ""
    source_file: str = ""
    interface: BlockInterface = Field(default_factory=BlockInterface)
    interface_count: int = 0
    networks: list[Network] = Field(default_factory=list)
    code: str = ""
    tag_references: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    header_author: str = ""
    header_family: str = ""
    header_name: str = ""
    header_version: str = ""
    instance_of_name: str = ""
    instance_of_type: str = ""


class PlcSummary(BaseModel):
    """Summary statistics of a PLC extraction."""

    model_config = {"extra": "ignore"}

    total_blocks: int = 0
    fb_count: int = 0
    fc_count: int = 0
    ob_count: int = 0
    db_count: int = 0
    idb_count: int = 0
    scl_count: int = 0
    stl_count: int = 0
    total_interfaces: int = 0
    total_calls: int = 0
    total_tag_refs: int = 0
    unique_tag_refs: int = 0
    plc_tags_loaded: int = 0


class PlcExtraction(BaseModel):
    """Complete PLC block extraction result."""

    model_config = {"extra": "ignore"}

    source_path: str = ""
    summary: PlcSummary = Field(default_factory=PlcSummary)
    blocks: list[BlockResult] = Field(default_factory=list)
    call_tree: dict[str, list[str]] = Field(default_factory=dict)
    called_by: dict[str, list[str]] = Field(default_factory=dict)
    tag_xref: dict[str, dict] = Field(default_factory=dict)
    plc_tags: dict[str, dict] = Field(default_factory=dict)
    errors: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# HMI Screen Extraction Models
# ---------------------------------------------------------------------------

class TagBinding(BaseModel):
    """A PLC tag binding for an HMI element."""

    model_config = {"extra": "ignore"}

    property: str = ""
    hmi_tag: str = ""
    plc_tag: str = ""
    plc_name: str = ""
    data_type: str = ""
    connection: str = ""


class ElementEvent(BaseModel):
    """A JavaScript event on an HMI element."""

    model_config = {"extra": "ignore"}

    function: str = ""
    event_type: str = ""
    plc_tags: list[str] = Field(default_factory=list)
    navigates_to: list[str] = Field(default_factory=list)
    code: str = ""


class ScreenElement(BaseModel):
    """A single UI element on an HMI screen."""

    model_config = {"extra": "ignore"}

    name: str = ""
    type: str = ""
    io_role: str = ""
    tag_bindings: list[TagBinding] = Field(default_factory=list)
    events: list[ElementEvent] = Field(default_factory=list)


class ScreenResult(BaseModel):
    """A single parsed HMI screen."""

    model_config = {"extra": "ignore"}

    screen_name: str = ""
    file: str = ""
    element_count: int = 0
    elements: list[ScreenElement] = Field(default_factory=list)
    element_summary: dict[str, int] = Field(default_factory=dict)
    javascript_functions: list[dict] = Field(default_factory=list)
    screen_navigations: list[str] = Field(default_factory=list)
    on_loaded_event: str | None = None
    plc_tags_referenced: list[str] = Field(default_factory=list)


class HmiSummary(BaseModel):
    """Summary statistics of an HMI extraction."""

    model_config = {"extra": "ignore"}

    total_screens: int = 0
    total_elements: int = 0
    total_elements_with_events: int = 0
    total_tag_bindings: int = 0
    total_js_functions: int = 0
    total_unique_plc_tags: int = 0
    total_navigation_links: int = 0


class HmiExtraction(BaseModel):
    """Complete HMI screen extraction result."""

    model_config = {"extra": "ignore"}

    source_project: str = ""
    summary: HmiSummary = Field(default_factory=HmiSummary)
    screens: list[ScreenResult] = Field(default_factory=list)
    navigation_map: dict[str, list[str]] = Field(default_factory=dict)
    plc_tag_index: dict[str, dict] = Field(default_factory=dict)
    hmi_device: dict | None = None
    errors: list[dict] = Field(default_factory=list)
