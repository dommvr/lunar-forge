"""Read-only security reviewer role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "glob",
        "project_health",
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
        "specific bypasses or unsafe defaults. Use project_health and changed-file "
        "Git metadata when runtime, generated, secret-looking, or tracked paths are "
        "relevant. Do not mutate files or execute commands. Existing safety rules "
        "are authoritative."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = SECURITY_ROLE

__all__ = ["ROLE", "SECURITY_ROLE"]
