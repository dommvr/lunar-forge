"""Read-only planner role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "list_dir",
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "glob",
        "detect_project",
        "project_health",
        "dependency_summary",
        "git_status",
    }
)

PLANNER_ROLE = SubagentRole(
    name="planner",
    purpose=(
        "Inspect project context and instructions, identify relevant files, and "
        "produce a concrete implementation plan without changing project state."
    ),
    system_prompt_fragment=(
        "Act as the planner. Inspect only what is needed, account for applicable "
        "AGENTS.md instructions, and return an ordered plan with likely files and "
        "validation. For broad review, onboarding, or feature-planning work, use "
        "project_health before opening many files. Use dependency_summary before "
        "choosing uncertain validation, build, or development commands, and "
        "git_status when existing dirty state affects the plan. Do not call "
        "project_health for a tiny single-file edit unless it adds relevant signal. "
        "Never edit files or run commands."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = PLANNER_ROLE

__all__ = ["PLANNER_ROLE", "ROLE"]
