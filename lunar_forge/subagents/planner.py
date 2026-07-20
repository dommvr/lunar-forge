"""Read-only planner role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {"list_dir", "read_file", "grep", "glob", "detect_project"}
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
        "validation. Never edit files or run commands."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = PLANNER_ROLE

__all__ = ["PLANNER_ROLE", "ROLE"]
