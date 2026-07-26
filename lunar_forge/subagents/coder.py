"""Approved-plan implementation role."""

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
        "create_dir",
        "write_file",
        "edit_file",
        "replace_lines",
        "insert_lines",
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
        "and follow the instruction stack for every target file. Read stable line "
        "numbers before precise line edits. Use read_json for known JSON "
        "configuration and read_yaml for known YAML configuration instead of raw "
        "read_file. Use read_many_files only for a small known set of related "
        "files. Use "
        "list_symbols before reading a large source file to locate definitions, "
        "then read the relevant lines before editing. Do not run shell commands or "
        "validation; leave those actions to the tester."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = CODER_ROLE

__all__ = ["CODER_ROLE", "ROLE"]
