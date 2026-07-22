"""Disabled-by-default Model Context Protocol client and stdio transport."""

from lunar_forge.mcp.client import (
    MCPClient,
    MCPClientError,
    MCPToolDefinition,
    MCPTransport,
    StdioMCPTransport,
    TransportFactory,
    build_mcp_diagnostic,
)
from lunar_forge.mcp.config import (
    MCPConfig,
    MCPConfigError,
    MCPServerConfig,
    load_mcp_config,
    mcp_config_paths,
    resolve_server_environment,
)
from lunar_forge.mcp.permissions import mcp_tool_permission
from lunar_forge.mcp.registry import namespace_mcp_tool, register_mcp_tools

__all__ = [
    "MCPClient",
    "MCPClientError",
    "MCPConfig",
    "MCPConfigError",
    "MCPServerConfig",
    "StdioMCPTransport",
    "MCPToolDefinition",
    "MCPTransport",
    "TransportFactory",
    "build_mcp_diagnostic",
    "load_mcp_config",
    "mcp_config_paths",
    "mcp_tool_permission",
    "namespace_mcp_tool",
    "register_mcp_tools",
    "resolve_server_environment",
]
