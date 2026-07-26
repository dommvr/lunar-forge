"""Read-only security reviewer role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "read_file_with_line_numbers",
        "read_json",
        "read_yaml",
        "read_many_files",
        "list_symbols",
        "grep",
        "glob",
        "project_health",
        "ci_summary",
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
        "security-sensitive details. Use read_json for relevant non-secret JSON "
        "configuration and read_yaml for relevant non-secret YAML configuration "
        "instead of raw read_file. Use read_many_files only for a small known set; "
        "blocked-path results are authoritative. "
        "For CI or release-security reviews, use ci_summary to inspect redacted "
        "jobs, runtime hints, and commands without reading env values. "
        "Use list_symbols to locate relevant definitions before reading a large "
        "supported source file. "
        "Do not mutate files, execute project commands, or request a commit. "
        "Existing safety rules are authoritative."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = SECURITY_ROLE

__all__ = ["ROLE", "SECURITY_ROLE"]
