"""Adapt discovered MCP tools to lunar-forge's central ToolRegistry."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from lunar_forge.mcp.client import (
    MAX_DISCOVERED_TOOLS,
    MCPClient,
    MCPClientError,
    MCPToolDefinition,
)
from lunar_forge.mcp.permissions import mcp_tool_permission
from lunar_forge.tools.registry import Tool, ToolRegistry


_SERVER_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_TOOL_NAME_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)*$"
)
MAX_NAMESPACED_TOOL_NAME_CHARACTERS = 128


def namespace_mcp_tool(server_name: str, tool_name: str) -> str:
    """Create a stable internal MCP identity for routing and diagnostics."""
    if not _SERVER_NAMESPACE_PATTERN.fullmatch(server_name):
        raise ValueError("Invalid MCP server namespace.")
    if not _TOOL_NAME_PATTERN.fullmatch(tool_name):
        raise ValueError(f"Invalid MCP tool name: {tool_name}")
    namespaced = f"mcp.{server_name}.{tool_name}"
    if len(namespaced) > MAX_NAMESPACED_TOOL_NAME_CHARACTERS:
        raise ValueError("Namespaced MCP tool name is too long.")
    return namespaced


def register_mcp_tools(
    registry: ToolRegistry,
    client: MCPClient,
    *,
    read_only_only: bool = False,
) -> tuple[str, ...]:
    """Register enabled-server tools, optionally limiting them to plan-safe reads."""
    pending: list[Tool] = []
    pending_names: set[str] = set()
    existing_names = set(registry.names())
    discovered_count = 0
    for server in client.config.enabled_servers:
        definitions = client.discover_tools(server.name)
        discovered_count += len(definitions)
        if discovered_count > MAX_DISCOVERED_TOOLS:
            raise MCPClientError(
                "Enabled MCP servers expose too many tools in total."
            )
        for definition in definitions:
            if read_only_only and not definition.read_only:
                continue
            tool = _registry_tool(client, server.name, definition)
            if tool.name in existing_names or tool.name in pending_names:
                raise ValueError(f"MCP tool name is already registered: {tool.name}")
            pending.append(tool)
            pending_names.add(tool.name)
    for tool in pending:
        registry.register(tool)
    return tuple(tool.name for tool in pending)


def _registry_tool(
    client: MCPClient,
    server_name: str,
    definition: MCPToolDefinition,
) -> Tool:
    namespaced_name = namespace_mcp_tool(server_name, definition.name)

    def call_mcp_tool(**arguments: Any) -> dict[str, Any]:
        return client.call_tool(server_name, definition.name, arguments)

    parameters = _tool_parameters(definition.input_schema)
    description = definition.description or (
        f"Call {definition.name} on the {server_name} MCP server."
    )
    return Tool(
        name=namespaced_name,
        description=description,
        parameters=parameters,
        handler=call_mcp_tool,
        permission=mcp_tool_permission(read_only=definition.read_only),
        plan_safe=definition.read_only,
    )


def _tool_parameters(schema: Mapping[str, Any]) -> dict[str, Any]:
    parameters = dict(schema)
    schema_type = parameters.get("type")
    if schema_type is None:
        parameters["type"] = "object"
    elif schema_type != "object":
        raise ValueError("MCP tool input schemas must describe an object.")
    parameters.setdefault("properties", {})
    if not isinstance(parameters["properties"], Mapping):
        raise ValueError("MCP tool schema properties must be an object.")
    return parameters
