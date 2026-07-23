"""Permission-gated validation role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "run_command",
        "run_validation",
        "run_browser_validation",
        "run_managed_browser_validation",
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "dependency_summary",
        "git_status",
        "list_changed_files",
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
        "focused validation. Use dependency_summary before guessing commands when "
        "the validation route is unclear, and use changed-file or Git status "
        "metadata only to focus validation. For application-detected browser "
        "intent, use available "
        "Playwright MCP tools or the built-in browser validation tool requested by "
        "the routing context instead of ordinary run_validation. Report whether "
        "browser validation ran, its final URL, page title, screenshot path, full-page "
        "mode, console error count, and failed request count. Tool results are the "
        "authoritative validation record. Report the exact commands and outcomes "
        "returned by run_validation and run_command. Stop after identifying one "
        "reasonable fix path. Never create or edit files."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
    allowed_tool_prefixes=("mcp.playwright.",),
)

ROLE = TESTER_ROLE

__all__ = ["ROLE", "TESTER_ROLE"]
