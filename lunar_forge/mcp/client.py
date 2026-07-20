"""Transport-agnostic MCP discovery and invocation.

This module intentionally contains no process launcher. A later integration can
provide a concrete transport factory after applying command and network policy.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from lunar_forge.mcp.config import MCPConfig, MCPServerConfig


MAX_TOOL_DESCRIPTION_CHARACTERS = 2_000
MAX_TOOL_SCHEMA_CHARACTERS = 50_000
MAX_CALL_ARGUMENT_CHARACTERS = 50_000
MAX_OUTPUT_CHARACTERS = 50_000
MAX_OUTPUT_STRING_CHARACTERS = 20_000
MAX_OUTPUT_COLLECTION_ITEMS = 100
MAX_OUTPUT_DEPTH = 12


class MCPClientError(RuntimeError):
    """Raised for invalid discovery data or unavailable MCP transports."""


class MCPTransport(Protocol):
    """Small synchronous seam implemented by future SDK transports and tests."""

    def list_tools(self) -> Sequence[Mapping[str, Any]]:
        """Return raw MCP tool definitions."""

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Invoke one remote tool and return its raw result."""


TransportFactory = Callable[[MCPServerConfig], MCPTransport]


@dataclass(frozen=True)
class MCPToolDefinition:
    """Validated model-facing parts of one discovered MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False


class MCPClient:
    """Discover and invoke tools through explicitly supplied transports."""

    def __init__(
        self,
        config: MCPConfig,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self.config = config
        self._transport_factory = transport_factory
        self._transports: dict[str, MCPTransport] = {}

    def discover_tools(self, server_name: str) -> tuple[MCPToolDefinition, ...]:
        """Validate tool schemas exposed by an explicitly enabled server."""
        transport = self._transport_for(server_name)
        try:
            raw_tools = transport.list_tools()
        except Exception as exc:
            raise MCPClientError(
                f"MCP tool discovery failed with {type(exc).__name__}."
            ) from exc
        if isinstance(raw_tools, (str, bytes)) or not isinstance(raw_tools, Sequence):
            raise MCPClientError("MCP tool discovery must return a sequence.")

        definitions: list[MCPToolDefinition] = []
        seen_names: set[str] = set()
        for raw_tool in raw_tools:
            definition = _parse_tool_definition(raw_tool)
            if definition.name in seen_names:
                raise MCPClientError(
                    f"MCP server returned duplicate tool name: {definition.name}"
                )
            seen_names.add(definition.name)
            definitions.append(definition)
        return tuple(definitions)

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Call an enabled MCP tool and return a bounded serializable envelope."""
        try:
            normalized_arguments, argument_truncated = _normalize_json(arguments)
            if argument_truncated or not isinstance(normalized_arguments, dict):
                return {"ok": False, "error": "MCP tool arguments are too large."}
            encoded_arguments = json.dumps(
                normalized_arguments,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            if len(encoded_arguments) > MAX_CALL_ARGUMENT_CHARACTERS:
                return {"ok": False, "error": "MCP tool arguments are too large."}
            transport = self._transport_for(server_name)
            raw_result = transport.call_tool(tool_name, normalized_arguments)
            return _bounded_result(raw_result)
        except (MCPClientError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)[:500]}
        except Exception as exc:
            return {
                "ok": False,
                "error": f"MCP tool call failed with {type(exc).__name__}.",
            }

    def _transport_for(self, server_name: str) -> MCPTransport:
        server = self.config.servers.get(server_name)
        if server is None:
            raise MCPClientError(f"Unknown MCP server: {server_name}")
        if not server.enabled:
            raise MCPClientError(f"MCP server is disabled: {server_name}")
        if self._transport_factory is None:
            raise MCPClientError(
                "No MCP transport is configured; external servers were not launched."
            )
        transport = self._transports.get(server_name)
        if transport is None:
            transport = self._transport_factory(server)
            self._transports[server_name] = transport
        return transport


def _parse_tool_definition(raw_tool: Mapping[str, Any]) -> MCPToolDefinition:
    if not isinstance(raw_tool, Mapping):
        raise MCPClientError("MCP tool definitions must be mappings.")
    name = raw_tool.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MCPClientError("MCP tool definitions require a name.")
    name = name.strip()

    description = raw_tool.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise MCPClientError(f"MCP tool '{name}' description must be a string.")
    description = description[:MAX_TOOL_DESCRIPTION_CHARACTERS]

    input_schema = raw_tool.get("inputSchema", raw_tool.get("input_schema"))
    if input_schema is None:
        input_schema = {"type": "object", "properties": {}}
    if not isinstance(input_schema, Mapping):
        raise MCPClientError(f"MCP tool '{name}' input schema must be an object.")
    schema = dict(input_schema)
    try:
        encoded_schema = json.dumps(schema, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MCPClientError(
            f"MCP tool '{name}' input schema must be JSON-serializable."
        ) from exc
    if len(encoded_schema) > MAX_TOOL_SCHEMA_CHARACTERS:
        raise MCPClientError(f"MCP tool '{name}' input schema is too large.")

    annotations = raw_tool.get("annotations", {})
    if annotations is None:
        annotations = {}
    if not isinstance(annotations, Mapping):
        raise MCPClientError(f"MCP tool '{name}' annotations must be an object.")
    read_only = annotations.get("readOnlyHint", False)
    if not isinstance(read_only, bool):
        raise MCPClientError(
            f"MCP tool '{name}' annotations.readOnlyHint must be a boolean."
        )
    return MCPToolDefinition(
        name=name,
        description=description,
        input_schema=schema,
        read_only=read_only,
    )


def _bounded_result(raw_result: Any) -> dict[str, Any]:
    try:
        result, truncated = _normalize_json(raw_result)
    except (TypeError, ValueError):
        return {"ok": False, "error": "MCP tool returned a non-serializable result."}

    envelope: dict[str, Any] = {
        "ok": True,
        "result": result,
        "truncated": truncated,
    }
    encoded = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) <= MAX_OUTPUT_CHARACTERS:
        return envelope
    return {
        "ok": True,
        "result": {"preview": encoded[: MAX_OUTPUT_CHARACTERS - 200]},
        "truncated": True,
    }


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
                raise TypeError("JSON object keys must be strings.")
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
    raise TypeError(f"Unsupported MCP result type: {type(value).__name__}")
