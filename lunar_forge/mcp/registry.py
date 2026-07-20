"""Adapt discovered MCP tools to lunar-forge's central ToolRegistry."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from lunar_forge.mcp.client import MCPClient, MCPToolDefinition
from lunar_forge.mcp.permissions import mcp_tool_permission
from lunar_forge.tools.registry import Tool, ToolRegistry


_SERVER_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_TOOL_NAME_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)*$"
)
MAX_NAMESPACED_TOOL_NAME_CHARACTERS = 128


def namespace_mcp_tool(server_name: str, tool_name: str) -> str:
    """Create a stable MCP namespace accepted by the model tool registry."""
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
    registered: list[str] = []
    for server in client.config.enabled_servers:
        for definition in client.discover_tools(server.name):
            if read_only_only and not definition.read_only:
                continue
            tool = _registry_tool(client, server.name, definition)
            registry.register(tool)
            registered.append(tool.name)
    return tuple(registered)


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
