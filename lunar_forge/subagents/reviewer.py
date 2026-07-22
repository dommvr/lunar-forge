"""Read-only implementation reviewer role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {"read_file", "read_file_with_line_numbers", "grep", "glob"}
)

REVIEWER_ROLE = SubagentRole(
    name="reviewer",
    purpose=(
        "Review changed files for requirement coverage, correctness, clarity, and "
        "unnecessary or risky complexity."
    ),
    system_prompt_fragment=(
        "Act as the reviewer. Inspect the relevant changed files, report concrete "
        "findings with file references, and do not modify project state. Prioritize "
        "correctness and maintainability over stylistic preferences."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = REVIEWER_ROLE

__all__ = ["REVIEWER_ROLE", "ROLE"]
