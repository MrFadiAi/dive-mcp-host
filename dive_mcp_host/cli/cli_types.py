"""Dive MCP Host CLI types."""

from dataclasses import dataclass, field


@dataclass
class CLIArgs:
    """CLI arguments.

    Args:
        chat_id: The thread id to continue from.
        query: The input query.
        config_path: The path to the configuration file.
        config_dir: The directory containing configuration files.
        mcp_config_path: The path to the MCP servers configuration file.
        model_config_path: The path to the model configuration file.
        prompt_file: The path to the system prompt file.
        index_docs: Directory path to index documents for RAG.
        reindex: Force re-index already indexed documents.
        list_docs: List indexed documents.
    """

    chat_id: str | None = None
    query: list = field(default_factory=list)
    config_path: str | None = None
    config_dir: str | None = None
    mcp_config_path: str | None = None
    model_config_path: str | None = None
    prompt_file: str | None = None
    index_docs: str | None = None
    reindex: bool = False
    list_docs: bool = False
