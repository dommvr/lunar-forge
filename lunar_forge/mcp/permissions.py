"""Permission policy for external MCP tools."""

from lunar_forge.permissions import PermissionLevel


def mcp_tool_permission(*, read_only: bool = False) -> PermissionLevel:
    """Require approval for every MCP interaction.

    Read-only external calls use ``NETWORK`` so they remain approval-gated but
    can be distinguished from mutating calls in plan mode.
    """
    return PermissionLevel.NETWORK if read_only else PermissionLevel.EXECUTE
