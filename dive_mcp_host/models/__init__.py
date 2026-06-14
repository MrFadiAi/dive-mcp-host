"""Additional models for the MCP."""

import logging
import re
from importlib import import_module
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from dive_mcp_host.models.helpers import clean_model_kwargs

logger = logging.getLogger("dive_mcp_host.models")

# Matches a trailing context-window marker like "[1m]", "[200k]", "[1M]".
# These are local UI conventions, NOT valid model identifiers for most
# providers (e.g. z.ai rejects "glm-5.1[1m]" with error 1211
# "model does not exist"). We strip them before sending to the API but
# keep them for context-window detection (see conversation_compactor).
_CONTEXT_SUFFIX_RE = re.compile(r"\s*\[\s*\d+\s*[kKmM]\s*\]\s*$")


def _strip_context_suffix(model_name: str) -> str:
    """Strip a trailing context-window marker (e.g. '[1m]') from a model name.

    The marker is used only locally to signal a larger context window; it must
    not be sent to the provider's API.
    """
    return _CONTEXT_SUFFIX_RE.sub("", model_name).strip()


def load_model(
    provider: str,
    model_name: str,
    *args: Any,
    **kwargs: Any,
) -> BaseChatModel:
    """Load a model from the models directory.

    Args:
        provider: provider name. Two special providers are supported:
            - "dive": use the model in dive_mcp_host.models
            - "__load__": load the model from the configuration
        model_name: The name of the model to load.
        args: Additional arguments to pass to the model.
        kwargs: Additional keyword arguments to pass to the model.

    Returns:
        The loaded model.

    If the provider is "dive", it should be like this:
        import dive_mcp_host.models.model_name_in_lower_case as model_module
        model = model_module.load_model(*args, **kwargs)
    If the provider is "__load__", the model_name is the class name of the model.
    For example, with model_name="package.module:ModelClass", it will be like this:
        import package.module as model_module
        model = model_module.ModelClass(*args, **kwargs)
    If the provider is neither "dive" nor "__load__", it will load model from langchain.
    """
    logger.debug(
        "Loading model %s with provider %s, kwargs: %s",
        model_name,
        provider,
        kwargs,
    )
    # Strip local context-window markers (e.g. "glm-5.2[1m]" → "glm-5.2")
    # before sending to the provider API. The marker is a local/UI convention
    # that most providers reject (z.ai returns 1211 "Unknown Model").
    # "dive"/"__load__" use model_name as a module/class path — do not strip.
    api_model_name = (
        model_name
        if provider in ("dive", "__load__")
        else _strip_context_suffix(model_name)
    )
    if provider == "dive":
        model_name_lower = model_name.replace("-", "_").replace(".", "_").lower()
        model_module = import_module(
            f"dive_mcp_host.models.{model_name_lower}",
        )
        model = model_module.load_model(*args, **kwargs)
    elif provider == "oap":
        model = init_chat_model(
            model=api_model_name,
            model_provider="openai",
            **clean_model_kwargs("openai", kwargs),
        )
    elif provider == "__load__":
        module_path, class_name = model_name.rsplit(":", 1)
        model_module = import_module(module_path)
        class_ = getattr(model_module, class_name)
        model = class_(*args, **clean_model_kwargs(class_, kwargs))
    else:
        if len(args) > 0:
            raise ValueError(
                f"Additional arguments are not supported for {provider} provider.",
            )
        model = init_chat_model(
            model=api_model_name,
            model_provider=provider,
            **clean_model_kwargs(provider, kwargs),
        )
    return model
