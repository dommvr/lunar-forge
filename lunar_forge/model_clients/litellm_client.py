"""LiteLLM implementation of the provider-neutral model client."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

from lunar_forge.model_clients.base import ModelResponse, ToolCall


class LiteLLMClient:
    """Synchronous adapter around ``litellm.completion``."""

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.api_base = api_base

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> ModelResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
        }
        if tools:
            request["tools"] = [dict(tool) for tool in tools]
        request.update(self._request_options())

        response = _litellm_completion(**request)
        return _normalize_response(response, fallback_model=self.model)

    def _request_options(self) -> dict[str, Any]:
        """Return shared LiteLLM connection options without retaining a raw key."""
        options: dict[str, Any] = {}
        if self.api_base:
            options["api_base"] = self.api_base
        if self.api_key_env:
            api_key = os.getenv(self.api_key_env)
            if not api_key:
                raise RuntimeError(
                    f"API key environment variable is not set: {self.api_key_env}"
                )
            options["api_key"] = api_key
        return options


def _litellm_completion(**request: Any) -> Any:
    try:
        from litellm import completion
    except ImportError as exc:
        raise RuntimeError(
            "LiteLLM is not installed. Install the project's declared dependencies."
        ) from exc
    return completion(**request)


def _normalize_response(response: Any, fallback_model: str) -> ModelResponse:
    choices = _value(response, "choices", [])
    if not choices:
        raise ValueError("LiteLLM response did not contain any choices.")

    message = _value(choices[0], "message", {})
    text = _normalize_content(_value(message, "content"))
    raw_tool_calls = _value(message, "tool_calls", []) or []
    tool_calls = tuple(_normalize_tool_call(call) for call in raw_tool_calls)
    model = _value(response, "model", fallback_model) or fallback_model

    return ModelResponse(text=text, model=str(model), tool_calls=tool_calls)


def _normalize_tool_call(raw_tool_call: Any) -> ToolCall:
    function = _value(raw_tool_call, "function") or raw_tool_call
    name = _value(function, "name")
    if not name:
        raise ValueError("LiteLLM returned a tool call without a function name.")

    return ToolCall(
        id=str(_value(raw_tool_call, "id", "") or ""),
        name=str(name),
        arguments=_normalize_arguments(_value(function, "arguments", {})),
    )


def _normalize_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if not isinstance(arguments, str):
        raise ValueError("LiteLLM returned tool arguments in an unsupported format.")

    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ValueError("LiteLLM returned invalid JSON tool arguments.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LiteLLM tool arguments must decode to a JSON object.")
    return parsed


def _normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            text = _value(block, "text")
            if text is not None:
                parts.append(str(_value(text, "value", text)))
        return "".join(parts)
    return str(content)


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)
