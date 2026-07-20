"""New-project scaffolding role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {"create_dir", "write_file", "run_command", "run_validation"}
)

SCAFFOLDER_ROLE = SubagentRole(
    name="scaffolder",
    purpose=(
        "Choose an appropriate built-in starter and create an approved new project "
        "without overwriting existing work."
    ),
    system_prompt_fragment=(
        "Act as the scaffolder for an approved new-project plan. Use explicit "
        "templates, refuse non-empty targets, and route dependency installation and "
        "validation through the existing permission-gated tools."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = SCAFFOLDER_ROLE

__all__ = ["ROLE", "SCAFFOLDER_ROLE"]
