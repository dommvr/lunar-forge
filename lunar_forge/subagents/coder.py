"""Approved-plan implementation role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "list_dir",
        "read_file",
        "grep",
        "glob",
        "create_dir",
        "write_file",
        "edit_file",
    }
)

CODER_ROLE = SubagentRole(
    name="coder",
    purpose=(
        "Apply an approved implementation plan with small, coherent file changes "
        "that honor path-scoped project instructions."
    ),
    system_prompt_fragment=(
        "Act as the coder. Implement only the approved plan, keep changes focused, "
        "and follow the instruction stack for every target file. Do not run shell "
        "commands or validation; leave those actions to the tester."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = CODER_ROLE

__all__ = ["CODER_ROLE", "ROLE"]
