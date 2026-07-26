"""Read-only planner role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "list_dir",
        "read_file",
        "read_file_with_line_numbers",
        "read_json",
        "read_yaml",
        "read_many_files",
        "list_symbols",
        "grep",
        "glob",
        "detect_project",
        "project_health",
        "ci_summary",
        "dependency_summary",
        "git_status",
        "git_diff",
        "list_changed_files",
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
        "choosing uncertain validation, build, or development commands. When CI "
        "configuration exists, prefer ci_summary before finalizing validation "
        "commands instead of inventing commands. Use read_json for known JSON "
        "configuration and read_yaml for known YAML configuration instead of raw "
        "read_file; use read_many_files only for a small, known set of related "
        "text files. Use "
        "list_symbols before reading a large source file when locating definitions, "
        "then request only the relevant range. Before "
        "planning a review or commit, use git_status and list_changed_files; use "
        "git_diff only when changed-file details are needed. Do not call "
        "project_health or every introspection tool for a tiny single-file edit "
        "unless it adds relevant signal. "
        "Never edit files, run project commands, or request a commit."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = PLANNER_ROLE

__all__ = ["PLANNER_ROLE", "ROLE"]
