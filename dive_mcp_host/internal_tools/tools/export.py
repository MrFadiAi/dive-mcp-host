from langchain_core.tools import BaseTool

from dive_mcp_host.internal_tools.tools.bash import bash
from dive_mcp_host.internal_tools.tools.confirmation import request_confirmation
from dive_mcp_host.internal_tools.tools.doc_search import list_indexed_docs, search_docs
from dive_mcp_host.internal_tools.tools.extract_hmi import extract_hmi_screens
from dive_mcp_host.internal_tools.tools.extract_plc import extract_plc_blocks
from dive_mcp_host.internal_tools.tools.fetch import fetch
from dive_mcp_host.internal_tools.tools.file_ops import read_file, write_file
from dive_mcp_host.internal_tools.tools.list_dir import list_dir
from dive_mcp_host.internal_tools.tools.query_hmi import query_hmi_screens
from dive_mcp_host.internal_tools.tools.query_plc import query_plc_blocks
from dive_mcp_host.internal_tools.tools.mcp_server import (
    add_mcp_server,
    get_mcp_config,
    install_mcp_instructions,
    reload_mcp_server,
)
from dive_mcp_host.skills.tools import (
    dive_create_skill,
    dive_install_skill_from_path,
    dive_uninstall_skill,
)


def get_local_tools() -> list[BaseTool]:
    """Get local tools that can be exposed to external LLMs.

    These tools (fetch, bash, read_file, write_file, search_docs) can be used
    by external LLMs directly without going through the installer agent. They
    include built-in safety mechanisms like user confirmation for potentially
    dangerous operations.

    Returns:
        List of local tools.
    """
    return [
        fetch,
        bash,
        read_file,
        write_file,
        list_dir,
        get_mcp_config,
        add_mcp_server,
        reload_mcp_server,
        request_confirmation,
        install_mcp_instructions,
        search_docs,
        list_indexed_docs,
        extract_plc_blocks,
        extract_hmi_screens,
        query_plc_blocks,
        query_hmi_screens,
        dive_create_skill,
        dive_install_skill_from_path,
        dive_uninstall_skill,
    ]
