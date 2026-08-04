"""Model client adapters."""

from lunar_forge.config import ModelConfig
from lunar_forge.model_clients.base import (
    ModelClient,
    ModelClientFactory,
    ModelResponse,
    ModelUsage,
    RedactingModelClient,
    ToolCall,
)
from lunar_forge.model_clients.litellm_client import LiteLLMClient
from lunar_forge.model_clients.litellm_responses_client import LiteLLMResponsesClient


def create_litellm_client(
    *,
    api: str,
    model: str,
    api_key_env: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    reasoning_effort: str | None = None,
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
        api_key=api_key,
        api_base=api_base,
        reasoning_effort=reasoning_effort,
    )


def create_model_client(config: ModelConfig) -> ModelClient:
    """Select a provider adapter without leaking provider logic into the agent."""
    provider = config.provider.strip().lower()
    if provider != "litellm":
        raise ValueError(
            f"Unsupported model provider: {config.provider}. "
            "This milestone supports LiteLLM only."
        )
    return create_litellm_client(
        api=config.api,
        model=config.model,
        api_key_env=config.api_key_env,
        api_base=config.api_base,
        reasoning_effort=config.reasoning.effort,
    )


def create_ephemeral_model_client(
    *,
    model: str,
    api_key: str,
    api: str = "chat",
    api_base: str | None = None,
    reasoning_effort: str | None = None,
) -> ModelClient:
    """Create an in-memory OpenAI or Anthropic LiteLLM client.

    The credential is retained only by the returned client, never copied into
    configuration, requests, events, logs, representations, or environment
    variables. Callers should discard the client after the turn.
    """

    normalized_model = model.strip().lower()
    if not normalized_model.startswith(("openai/", "anthropic/")):
        raise ValueError(
            "Ephemeral model clients support OpenAI or Anthropic model IDs."
        )
    return create_litellm_client(
        api=api,
        model=model,
        api_key=api_key,
        api_base=api_base,
        reasoning_effort=reasoning_effort,
    )


def model_client_sensitive_values(client: ModelClient) -> tuple[str, ...]:
    """Return registered in-memory credentials for per-run redaction."""

    provider = getattr(client, "sensitive_values_for_redaction", None)
    if not callable(provider):
        return ()
    try:
        values = provider()
    except Exception:
        return ()
    return tuple(
        value
        for value in values
        if isinstance(value, str) and value
    )


__all__ = [
    "LiteLLMClient",
    "LiteLLMResponsesClient",
    "ModelClient",
    "ModelClientFactory",
    "ModelResponse",
    "ModelUsage",
    "RedactingModelClient",
    "ToolCall",
    "create_model_client",
    "create_litellm_client",
    "create_ephemeral_model_client",
    "model_client_sensitive_values",
]
