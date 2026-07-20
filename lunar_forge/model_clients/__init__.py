"""Model client adapters."""

from lunar_forge.model_clients.base import ModelClient, ModelResponse, ToolCall
from lunar_forge.model_clients.litellm_client import LiteLLMClient
from lunar_forge.model_clients.litellm_responses_client import LiteLLMResponsesClient


def create_litellm_client(
    *,
    api: str,
    model: str,
    api_key_env: str | None = None,
    api_base: str | None = None,
) -> ModelClient:
    """Create the configured LiteLLM transport behind the neutral protocol."""
    normalized_api = api.strip().lower()
    client_type: type[LiteLLMClient]
    if normalized_api == "chat":
        client_type = LiteLLMClient
    elif normalized_api == "responses":
        client_type = LiteLLMResponsesClient
    else:
        raise ValueError("LiteLLM API mode must be one of: chat, responses.")
    return client_type(
        model=model,
        api_key_env=api_key_env,
        api_base=api_base,
    )


__all__ = [
    "LiteLLMClient",
    "LiteLLMResponsesClient",
    "ModelClient",
    "ModelResponse",
    "ToolCall",
    "create_litellm_client",
]
