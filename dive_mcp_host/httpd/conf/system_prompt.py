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


def guide_mode_instructions() -> str:
    """Generate guide mode instructions to append to the system prompt.

    Reads the guide-mode skill from the skills directory and wraps it
    as a system prompt section. Falls back to a minimal inline prompt
    if the skill file is not found.

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
            "Use ONLY reading/exploring tools (browse_project_tree, get_block_content, "
            "read_hardware_config, list_blocks, list_tags, etc.).\n"
            "NEVER use writing tools (write_block, create_block, modify_block, "
            "create_tag, upload, compile, deploy, etc.).\n"
            "Provide step-by-step instructions for the user to execute manually in TIA Portal."
        )

    return f"<Guide_Mode_Protocol>\n{content}\n</Guide_Mode_Protocol>"
