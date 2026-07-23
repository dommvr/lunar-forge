"""Read-only implementation reviewer role."""

from lunar_forge.subagents.base import BUILTIN_SUBAGENT_TOOLS, SubagentRole


_ALLOWED_TOOLS = frozenset(
    {
        "read_file",
        "read_file_with_line_numbers",
        "grep",
        "glob",
        "project_health",
        "dependency_summary",
    }
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
        "correctness and maintainability over stylistic preferences. Use project "
        "health or dependency metadata only when the review is broad enough to need "
        "it. Validation "
        "status belongs to tester and tool results. In parallel mode the tester may "
        "still be running. Do not make global browser-validation status claims or "
        "claims about whether screenshots, console errors, failed requests, page "
        "titles, or final URLs were captured or inspected. Do not report this role's "
        "browser tool limitations. Focus on code-review findings and defer browser "
        "status silently to the authoritative validation summary."
    ),
    allowed_tools=_ALLOWED_TOOLS,
    blocked_tools=BUILTIN_SUBAGENT_TOOLS - _ALLOWED_TOOLS,
)

ROLE = REVIEWER_ROLE

__all__ = ["REVIEWER_ROLE", "ROLE"]
