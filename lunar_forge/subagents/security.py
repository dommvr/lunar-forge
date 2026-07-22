"""Read-only security reviewer role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {"read_file", "read_file_with_line_numbers", "grep", "glob"}
)

SECURITY_ROLE = SubagentRole(
    name="security",
    purpose=(
        "Review permissions, path confinement, command safety, secrets handling, "
        "Docker settings, MCP adapters, and plugin boundaries."
    ),
    system_prompt_fragment=(
        "Act as the security reviewer. Trace trust and permission boundaries, flag "
        "specific bypasses or unsafe defaults, and do not mutate files or execute "
        "commands. Existing safety rules are authoritative."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = SECURITY_ROLE

__all__ = ["ROLE", "SECURITY_ROLE"]
