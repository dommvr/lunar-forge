"""Bounded plugin invocation wrapper.

This is an output and exception containment boundary, not an operating-system
sandbox. It deliberately passes no project root, shell runner, or network
client to plugin handlers.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any


MAX_ARGUMENT_CHARACTERS = 50_000
MAX_OUTPUT_CHARACTERS = 50_000
MAX_OUTPUT_STRING_CHARACTERS = 20_000
MAX_OUTPUT_COLLECTION_ITEMS = 100
MAX_OUTPUT_DEPTH = 12
MAX_RESULT_PREVIEW_CHARACTERS = 10_000

PluginHandler = Callable[..., Any]


def invoke_plugin_handler(
    handler: PluginHandler,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Invoke a trusted resolver's handler and contain its returned data."""
    if not callable(handler):
        return {"ok": False, "error": "Plugin handler is not callable."}
    if not isinstance(arguments, Mapping):
        return {"ok": False, "error": "Plugin arguments must be an object."}
    try:
        normalized_arguments, arguments_truncated = _normalize_json(arguments)
        if arguments_truncated or not isinstance(normalized_arguments, dict):
            return {"ok": False, "error": "Plugin arguments are too large."}
        encoded_arguments = json.dumps(
            normalized_arguments,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(encoded_arguments) > MAX_ARGUMENT_CHARACTERS:
            return {"ok": False, "error": "Plugin arguments are too large."}
    except (TypeError, ValueError, RecursionError):
        return {
            "ok": False,
            "error": "Plugin arguments must be bounded JSON-serializable data.",
        }

    try:
        raw_result = handler(**normalized_arguments)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Plugin tool failed with {type(exc).__name__}.",
        }
    if not isinstance(raw_result, dict) or not isinstance(
        raw_result.get("ok"), bool
    ):
        return {
            "ok": False,
            "error": "Plugin tool returned an invalid result.",
        }

    try:
        normalized_result, truncated = _normalize_result(raw_result)
        if truncated:
            normalized_result["truncated"] = True
        encoded_result = json.dumps(
            normalized_result,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        return {
            "ok": False,
            "error": "Plugin tool returned a non-serializable result.",
        }
    if len(encoded_result) > MAX_OUTPUT_CHARACTERS:
        result: dict[str, Any] = {
            "ok": raw_result["ok"],
            "result_preview": encoded_result[:MAX_RESULT_PREVIEW_CHARACTERS],
            "truncated": True,
        }
        if raw_result["ok"] is False:
            result["error"] = "Plugin tool error result exceeded the output limit."
        return result
    return normalized_result


def _normalize_result(value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    normalized: dict[str, Any] = {"ok": value["ok"]}
    truncated = len(value) > MAX_OUTPUT_COLLECTION_ITEMS
    item_count = 1
    for key, item in value.items():
        if key == "ok":
            continue
        if item_count >= MAX_OUTPUT_COLLECTION_ITEMS:
            truncated = True
            break
        if not isinstance(key, str):
            raise TypeError("Plugin result keys must be strings.")
        normalized_item, item_truncated = _normalize_json(item, 1)
        normalized[key] = normalized_item
        truncated = truncated or item_truncated
        item_count += 1
    return normalized, truncated


def _normalize_json(value: Any, depth: int = 0) -> tuple[Any, bool]:
    if depth > MAX_OUTPUT_DEPTH:
        return "[truncated: maximum depth]", True
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, str):
        if len(value) <= MAX_OUTPUT_STRING_CHARACTERS:
            return value, False
        return value[:MAX_OUTPUT_STRING_CHARACTERS], True
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        truncated = len(value) > MAX_OUTPUT_COLLECTION_ITEMS
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_OUTPUT_COLLECTION_ITEMS:
                break
            if not isinstance(key, str):
                raise TypeError("Plugin object keys must be strings.")
            normalized_item, item_truncated = _normalize_json(item, depth + 1)
            normalized[key] = normalized_item
            truncated = truncated or item_truncated
        return normalized, truncated
    if isinstance(value, (list, tuple)):
        normalized_items: list[Any] = []
        truncated = len(value) > MAX_OUTPUT_COLLECTION_ITEMS
        for item in value[:MAX_OUTPUT_COLLECTION_ITEMS]:
            normalized_item, item_truncated = _normalize_json(item, depth + 1)
            normalized_items.append(normalized_item)
            truncated = truncated or item_truncated
        return normalized_items, truncated
    raise TypeError(f"Unsupported plugin value type: {type(value).__name__}")
