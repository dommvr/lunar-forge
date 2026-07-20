"""LiteLLM Responses API adapter with provider-specific translation isolated here."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from lunar_forge.model_clients.base import ModelResponse, ToolCall
from lunar_forge.model_clients.litellm_client import (
    LiteLLMClient,
    _normalize_arguments,
    _normalize_content,
    _normalize_response,
    _value,
)


@dataclass(frozen=True)
class _OutputGroup:
    key: str
    items: tuple[Any, ...]


class LiteLLMResponsesClient(LiteLLMClient):
    """Synchronous adapter around ``litellm.responses``.

    The agent continues to use its provider-neutral message and tool contracts.
    This adapter translates them to Responses input items and retains returned
    output groups so reasoning and function-call items can be replayed on the
    next tool-loop step without exposing their provider shape to the agent.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str | None = None,
        api_base: str | None = None,
    ) -> None:
        super().__init__(
            model,
            api_key_env=api_key_env,
            api_base=api_base,
        )
        self._output_groups_by_call_id: dict[str, _OutputGroup] = {}

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> ModelResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "input": self._responses_input(messages),
        }
        if tools:
            request["tools"] = _responses_tools(tools)
        request.update(self._request_options())

        response = _litellm_responses(**request)
        normalized = _normalize_responses_response(
            response,
            fallback_model=self.model,
        )
        self._remember_output_group(response, normalized.tool_calls)
        return normalized

    def _responses_input(
        self,
        messages: Sequence[Mapping[str, Any]],
    ) -> list[Any]:
        input_items: list[Any] = []
        replayed_groups: set[str] = set()

        for raw_message in messages:
            message = dict(raw_message)
            role = str(message.get("role", ""))
            if role == "tool":
                input_items.append(_tool_result_input(message))
                continue

            raw_tool_calls = message.get("tool_calls")
            if role == "assistant" and _is_sequence(raw_tool_calls):
                groups = self._stored_groups(raw_tool_calls)
                if groups:
                    for group in groups:
                        if group.key in replayed_groups:
                            continue
                        input_items.extend(group.items)
                        replayed_groups.add(group.key)
                    continue

                content = message.get("content")
                if content not in (None, ""):
                    input_items.append({"role": role, "content": content})
                input_items.extend(
                    _tool_call_input(raw_tool_call)
                    for raw_tool_call in raw_tool_calls
                )
                continue

            content = message.get("content")
            if role and content is not None:
                input_items.append({"role": role, "content": content})

        return input_items

    def _stored_groups(self, raw_tool_calls: Sequence[Any]) -> list[_OutputGroup]:
        groups: list[_OutputGroup] = []
        seen: set[str] = set()
        for raw_tool_call in raw_tool_calls:
            call_id = str(_value(raw_tool_call, "id", "") or "")
            group = self._output_groups_by_call_id.get(call_id)
            if group is None or group.key in seen:
                continue
            groups.append(group)
            seen.add(group.key)
        return groups

    def _remember_output_group(
        self,
        response: Any,
        tool_calls: tuple[ToolCall, ...],
    ) -> None:
        if not tool_calls:
            return
        output = _output_items(response)
        if not output:
            return
        group_key = str(_value(response, "id", "") or tool_calls[0].id)
        group = _OutputGroup(
            key=group_key,
            items=tuple(_snapshot_output_item(item) for item in output),
        )
        for tool_call in tool_calls:
            if tool_call.id:
                self._output_groups_by_call_id[tool_call.id] = group


def _litellm_responses(**request: Any) -> Any:
    try:
        litellm = import_module("litellm")
    except ImportError as exc:
        raise RuntimeError(
            "LiteLLM is not installed. Install the project's declared dependencies."
        ) from exc

    responses = getattr(litellm, "responses", None)
    if not callable(responses):
        raise RuntimeError(
            "Installed LiteLLM does not provide Responses API support. "
            "Upgrade to LiteLLM 1.63.8 or newer, or set model.api to 'chat'."
        )
    return responses(**request)


def _normalize_responses_response(
    response: Any,
    fallback_model: str,
) -> ModelResponse:
    output = _output_items(response)
    if not output and _value(response, "choices"):
        return _normalize_response(response, fallback_model)

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in output:
        item_type = str(_value(item, "type", "") or "").lower()
        if item_type in {"function_call", "tool_call"}:
            tool_calls.append(_normalize_responses_tool_call(item))
        elif item_type == "message":
            text_parts.append(_normalize_content(_value(item, "content")))
        elif item_type in {"output_text", "text"}:
            text_parts.append(_normalize_content(_value(item, "text", item)))

    text = "".join(text_parts)
    if not text:
        text = _normalize_content(_value(response, "output_text"))
    model = _value(response, "model", fallback_model) or fallback_model
    return ModelResponse(
        text=text,
        model=str(model),
        tool_calls=tuple(tool_calls),
    )


def _normalize_responses_tool_call(raw_tool_call: Any) -> ToolCall:
    function = _value(raw_tool_call, "function") or raw_tool_call
    name = _value(function, "name")
    if not name:
        raise ValueError("LiteLLM Responses returned a tool call without a name.")
    call_id = (
        _value(raw_tool_call, "call_id")
        or _value(raw_tool_call, "id")
        or ""
    )
    return ToolCall(
        id=str(call_id),
        name=str(name),
        arguments=_normalize_arguments(_value(function, "arguments", {})),
    )


def _responses_tools(
    tools: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw_tool in tools:
        tool = dict(raw_tool)
        function = tool.get("function")
        if tool.get("type") == "function" and isinstance(function, Mapping):
            response_tool = {"type": "function", **dict(function)}
            if not response_tool.get("name"):
                raise ValueError("Function tool schema is missing a name.")
            normalized.append(response_tool)
        else:
            normalized.append(tool)
    return normalized


def _tool_result_input(message: Mapping[str, Any]) -> dict[str, Any]:
    call_id = message.get("tool_call_id") or message.get("call_id")
    if not call_id:
        raise ValueError("Responses API tool result is missing tool_call_id.")
    return {
        "type": "function_call_output",
        "call_id": str(call_id),
        "output": _normalize_content(message.get("content")),
    }


def _tool_call_input(raw_tool_call: Any) -> dict[str, Any]:
    function = _value(raw_tool_call, "function") or raw_tool_call
    name = _value(function, "name")
    if not name:
        raise ValueError("Responses API tool call is missing a function name.")
    arguments = _value(function, "arguments", {})
    if isinstance(arguments, Mapping):
        arguments = json.dumps(dict(arguments), ensure_ascii=False)
    elif arguments in (None, ""):
        arguments = "{}"
    return {
        "type": "function_call",
        "call_id": str(_value(raw_tool_call, "id", "") or ""),
        "name": str(name),
        "arguments": str(arguments),
    }


def _output_items(response: Any) -> list[Any]:
    output = _value(response, "output", []) or []
    return list(output) if _is_sequence(output) else []


def _snapshot_output_item(item: Any) -> Any:
    if isinstance(item, Mapping):
        return {
            str(key): _snapshot_output_item(value)
            for key, value in item.items()
        }
    if _is_sequence(item):
        return [_snapshot_output_item(value) for value in item]
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        return _snapshot_output_item(model_dump(exclude_none=True))
    if item is None or isinstance(item, (str, bool, int, float)):
        return item

    snapshot: dict[str, Any] = {}
    for key in (
        "type",
        "id",
        "call_id",
        "name",
        "arguments",
        "status",
        "role",
        "content",
        "summary",
        "encrypted_content",
        "text",
        "refusal",
    ):
        value = _value(item, key)
        if value is not None:
            snapshot[key] = _snapshot_output_item(value)
    return snapshot or item


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


__all__ = ["LiteLLMResponsesClient"]
