"""Permission-gated validation role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "run_command",
        "run_validation",
        "read_file",
        "read_file_with_line_numbers",
        "grep",
    }
)

TESTER_ROLE = SubagentRole(
    name="tester",
    purpose=(
        "Select and run focused validation, inspect failures, and recommend at most "
        "one bounded fix path."
    ),
    system_prompt_fragment=(
        "Act as the tester. Use the existing permission-gated command tools for "
        "focused validation, report exact outcomes, and stop after identifying one "
        "reasonable fix path. Never create or edit files."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = TESTER_ROLE

__all__ = ["ROLE", "TESTER_ROLE"]
