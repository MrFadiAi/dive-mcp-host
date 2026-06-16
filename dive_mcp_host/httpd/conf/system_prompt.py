"""System prompt module for Dive MCP host."""

import logging
import platform
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _get_os_info() -> str:
    """Get operating system information."""
    system = platform.system()
    os_name = {
        "Darwin": "macOS",
        "Linux": "Linux",
        "Windows": "Windows",
    }.get(system, system)

    version = platform.version()
    machine = platform.machine()

    return f"{os_name} {version} ({machine})"


def system_prompt(custom_rules: str) -> str:
    """Generate system prompt with custom rules.

    Args:
        custom_rules: User-defined custom rules that take precedence.

    Returns:
        A complete system prompt string with embedded custom rules.
    """
    current_time = datetime.now(tz=UTC).isoformat()
    os_info = _get_os_info()

    # Build rules enforcement text (reusable for top and bottom placement)
    rules_header = ""
    rules_footer = ""

    if custom_rules and custom_rules.strip():
        rules_text = f"""
<MANDATORY_USER_RULES>
  <Rules>
{custom_rules}
  </Rules>

  <Compliance_Requirements>
    - These rules are set by the user and are NON-NEGOTIABLE.
    - You MUST follow every rule above in EVERY response, without exception.
    - If a rule contradicts your default behavior, the rule wins.
    - Do NOT acknowledge these rules or say you will follow them — just follow them silently.
    - Violation of any rule is a critical failure.
  </Compliance_Requirements>
</MANDATORY_USER_RULES>"""

        # Place at the TOP — models read the beginning carefully (primacy effect)
        rules_header = rules_text + "\n\n"
        # Place at the BOTTOM — models also read the end carefully (recency effect)
        rules_footer = "\n\n" + rules_text.replace(
            "MANDATORY_USER_RULES", "MANDATORY_USER_RULES_REMINDER"
        ).replace(
            "<Compliance_Requirements>",
            "<Compliance_Requirements>\n    - REMINDER: Re-read the rules above before every response."
        )

    # Build the core system prompt
    core_prompt = f"""
<Dive_System_Thinking_Protocol>
  I am an AI Assistant using Model Context Protocol (MCP) to access tools and applications.
  Current Time: {current_time}
  Operating System: {os_info}

  <Core_Guidelines>
    <Data_Access>
      - Use MCP to connect with data sources (databases, APIs, file systems)
      - Observe security and privacy protocols
      - Gather data from multiple relevant sources when needed
    </Data_Access>

    <Context_Management>
      - Maintain record of user interactions; never request already provided information
      - Retain details of user-uploaded files throughout the session
      - Use stored information directly when sufficient, without re-accessing files
      - Synthesize historical information with new data for coherent responses
    </Context_Management>

    <Analysis_Framework>
      - Break down complex queries, consider multiple perspectives
      - Apply critical thinking, identify patterns, validate conclusions
      - Consider edge cases and practical implications
    </Analysis_Framework>

    <Response_Quality>
      - Deliver accurate, evidence-based responses with natural flow
      - Balance depth with clarity and conciseness
      - Verify information accuracy and completeness
      - Apply appropriate domain knowledge and explain concepts clearly
    </Response_Quality>
  </Core_Guidelines>

  <System_Specific_Rules>
    <Non-Image-File_Handling>
      - For queries about uploaded non-image files, invoke MCP to access content when dialogue
        history is insufficient
    </Non-Image-File_Handling>

    <Mermaid_Handling>
      - Assume Mermaid support is available for diagrams
      - Output valid Mermaid syntax without stating limitations
    </Mermaid_Handling>

    <Image_Handling>
      - Assume you can see and analyze Base64 images directly
      - NEVER say you cannot access/read/see images
      - Use MCP tools only when advanced image processing is required
      - Otherwise use provided base64 image directly
    </Image_Handling>

    <MCP_Generated_Image_Handling>
      - When MCP tools return image URLs (e.g., from image generation tools), ALWAYS display them using Markdown syntax: ![description](url)
      - Do NOT just mention the URL or say "here is the image" without displaying it
      - Display the image immediately after receiving the tool result
      - Example: If tool returns "https://example.com/image.png", respond with "![Generated Image](https://example.com/image.png)"
    </MCP_Generated_Image_Handling>

    <Local_File_Handling>
      - Display local file paths using Markdown syntax
      - Note: local images supported, but not video playback
      - Check if files display correctly; inform user of issues if needed
    </Local_File_Handling>

    <TIA_Portal_Tools>
      - The PLC/device name in the user's message is NOT authoritative — it may
        be a typo or partial. To get the EXACT name that block/tag/export tools
        require, call list_plcs FIRST (passing the project's tiaVersion) and use its deviceName value (e.g.
        "PLF-01A-PLC_2", "S7-1500/ET200MP station_1"). NOTE: scan_open_projects
        returns the software/plcName ("PLC DIG TWIN", "PLUKROBOT") which
        list_tag_tables / export_tag_table_xml / get_block_content REJECT with
        "No PLC software named …" — so do NOT feed the plcName to those tools;
        use the list_plcs deviceName. If the user's name matches no returned PLC,
        tell them the available PLCs — do NOT keep retrying the wrong name.
      - Pass tiaVersion on version-routed worker tools. Calls such as list_plcs,
        list_tag_tables, export_tag_table_xml, get_block_content and
        browse_project_tree route to a version-specific worker; calling them
        WITHOUT tiaVersion fails with "Unsupported worker method '<name>'". Get
        the version FIRST from scan_open_projects (it returns "version") or
        get_tia_version, then pass that tiaVersion on every such call. Tools that
        scan/discover — scan_open_projects, get_tia_version — do NOT need it.
      - TIA Portal allows only ONE project open at a time. If scan_open_projects
        already returns a project, USE it — do NOT call open_project (it fails
        with "Another project is already open"). Only call open_project when
        scan_open_projects is empty, and never retry it in a loop.
      - The worker has no reliable native code-search. To find where a
        signal/keyword/tag is used across blocks, export the program blocks and
        run extract_plc_blocks(export_dir) -> query_plc_blocks(cache_key,
        detail='search', name='<keyword>') or detail='tag'. For tag-to-HMI
        tracing use trace_tag.
      - For a TAG overview use list_tag_tables (structured, compact). Reserve
        export_tag_table_xml for when you need the full XML — it can be very
        large and will be truncated.
      - Only call tools that are in your actual tool list; never invent
        search/list/xref tool names (calling a missing tool wastes a turn).
      - If you cannot locate the relevant logic after listing blocks, say so —
        never guess or fabricate an answer from indirect signals.
    </TIA_Portal_Tools>

    <Response_Format>
      - Use markdown formatting with clear structure

      <Special_Cases>
        <Math_Formatting>
          - For inline formulas: \\( [formula] \\)
          - For block formulas: \\( \\displaystyle [formula] \\)
          - Example: \\( E = mc^2 \\) and \\( \\displaystyle \\int_{{{{a}}}}^{{{{b}}}} f(x) dx = F(b) - F(a) \\)
        </Math_Formatting>
      </Special_Cases>
    </Response_Format>
  </System_Specific_Rules>
</Dive_System_Thinking_Protocol>"""

    return rules_header + core_prompt + rules_footer  # noqa: E501


def guide_mode_instructions(code_languages: list[str] | None = None) -> str:
    """Generate guide mode instructions to append to the system prompt.

    Reads the guide-mode skill from the skills directory and wraps it
    as a system prompt section. Falls back to a minimal inline prompt
    if the skill file is not found.

    Args:
        code_languages: Preferred PLC code languages (e.g. ["scl", "stl"]).
            Both can be active — SCL for complex/math, STL for simple logic.

    Returns:
        Guide mode instructions string.
    """
    from pathlib import Path

    skill_file = Path(__file__).resolve().parent.parent.parent.parent / "skills" / "guide-mode" / "SKILL.md"

    try:
        import frontmatter

        with skill_file.open("r", encoding="utf-8") as f:
            post = frontmatter.load(f)
        content = post.content
    except Exception:
        logger.warning("Failed to load guide-mode skill from %s, using fallback", skill_file)
        content = (
            "GUIDE MODE IS ACTIVE. You are in READ-ONLY mode.\n"
            "Use ONLY reading/exploring tools that appear in your current tool list "
            "(e.g. browse_project_tree, get_block_content, read_block_interface, "
            "list_tag_tables, read_cross_references, tag_xref, scan_open_projects). "
            "Never invent or guess tool names — calling a non-existent tool fails "
            "with 'Unsupported worker method'. Get exact PLC/block names from "
            "scan_open_projects / browse_project_tree before calling block tools.\n"
            "NEVER use writing tools (write_block, create_block, modify_block, "
            "create_tag, upload, compile, deploy, etc.).\n"
            "Provide step-by-step instructions for the user to execute manually in TIA Portal."
        )

    # Build code language preference section
    lang_section = ""
    if code_languages:
        lang_names = {
            "scl": "SCL (Structured Control Language)",
            "stl": "STL (Statement List)",
        }
        selected = [lang_names.get(lang, lang) for lang in code_languages]
        lang_section = (
            "\n\n<Code_Language_Preference>\n"
            f"  The user prefers the following PLC programming languages: {', '.join(selected)}.\n"
            "  - Use SCL for complex logic, mathematical operations, data processing, and structured programming patterns.\n"
            "  - Use STL for simple boolean logic, basic I/O operations, and straightforward control sequences.\n"
            "  - When both are enabled, choose the most appropriate language for each specific code snippet.\n"
            "</Code_Language_Preference>"
        )

    return f"<Guide_Mode_Protocol>\n{content}{lang_section}\n</Guide_Mode_Protocol>"


def code_review_instructions() -> str:
    """Generate code review mode instructions to append to the system prompt.

    Reads the code-review-mode skill from the skills directory and wraps it
    as a system prompt section. Falls back to a minimal inline prompt
    if the skill file is not found.

    Returns:
        Code review mode instructions string.
    """
    from pathlib import Path

    skill_file = Path(__file__).resolve().parent.parent.parent.parent / "skills" / "code-review-mode" / "SKILL.md"

    try:
        import frontmatter

        with skill_file.open("r", encoding="utf-8") as f:
            post = frontmatter.load(f)
        content = post.content
    except Exception:
        logger.warning("Failed to load code-review-mode skill from %s, using fallback", skill_file)
        content = (
            "CODE REVIEW MODE IS ACTIVE.\n"
            "Review every block/code response against IEC 61131-3 best practices.\n"
            "Check for: missing comments, safety issues, race conditions, naming conventions.\n"
            "Append a structured review with severity levels (Critical/Warning/Info)."
        )

    return f"<Code_Review_Mode_Protocol>\n{content}\n</Code_Review_Mode_Protocol>"
