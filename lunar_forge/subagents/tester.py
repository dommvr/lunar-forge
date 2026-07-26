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
        "read_json",
        "read_yaml",
        "read_many_files",
        "grep",
        "dependency_summary",
        "ci_summary",
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
        "focused validation. Use dependency_summary before selecting commands when "
        "the validation route is unclear. When CI configuration exists, prefer "
        "ci_summary before inventing or choosing commands so local checks align "
        "with CI. Use read_json for relevant JSON configuration and read_yaml for "
        "relevant YAML configuration instead of raw read_file. Use read_many_files "
        "only for a small known set of related validation files. Use "
        "list_changed_files when it helps focus validation or failure inspection. "
        "Git status metadata should only narrow the work. For application-detected "
        "browser "
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
