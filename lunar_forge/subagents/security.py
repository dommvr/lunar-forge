"""Read-only security reviewer role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "glob",
        "project_health",
        "dependency_summary",
        "git_status",
        "git_diff",
        "list_changed_files",
    }
)

SECURITY_ROLE = SubagentRole(
    name="security",
    purpose=(
        "Review permissions, path confinement, command safety, secrets handling, "
        "Docker settings, MCP adapters, and plugin boundaries."
    ),
    system_prompt_fragment=(
        "Act as the security reviewer. Trace trust and permission boundaries, flag "
        "specific bypasses or unsafe defaults. Use project_health and "
        "dependency_summary early for a broad repository overview. Use git_status "
        "and list_changed_files to find and scope suspicious tracked runtime, "
        "generated, or secret-looking paths, then use git_diff only for relevant "
        "security-sensitive details. Do not mutate files, execute project commands, "
        "or request a commit. Existing safety rules are authoritative."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = SECURITY_ROLE

__all__ = ["ROLE", "SECURITY_ROLE"]
