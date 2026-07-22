import pytest

from lunar_forge.prompts import (
    build_subagent_system_prompt,
    build_subagent_user_prompt,
    build_system_prompt,
    detect_browser_intent,
)
from lunar_forge.subagents import CODER_ROLE, PLANNER_ROLE, TESTER_ROLE
from lunar_forge.tools.registry import create_tool_registry


PROJECT_INFO = {
    "languages": ["python"],
    "frameworks": [],
    "package_manager": None,
    "routing": None,
    "test_command": "pytest",
    "build_command": None,
    "is_empty": False,
}


def test_system_prompt_requires_inspection_and_planning_before_edits():
    prompt = build_system_prompt(
        PROJECT_INFO,
        "Follow the project conventions.",
        "default",
    )

    assert "Inspect relevant files with read/search tools" in prompt
    assert "state a short implementation plan before the first edit" in prompt
    assert "Apply changes only through permission-gated tools" in prompt
    assert "AGENTS.md context" in prompt
    assert "Follow the project conventions." in prompt
    assert "AGENTS.md files are path-scoped" in prompt
    assert "root-to-leaf order" in prompt
    assert "instruction_stack" in prompt
    assert "Prefer read_file_with_line_numbers" in prompt
    assert "Use replace_lines" in prompt
    assert "Use insert_lines" in prompt
    assert "Keep using edit_file" in prompt


def test_system_prompt_requires_validation_and_bounded_fix_attempt():
    prompt = build_system_prompt(PROJECT_INFO, "No extra instructions.", "default")

    assert "call run_validation when practical" in prompt
    assert "attempt at most one focused fix" in prompt
    assert "then validate once more" in prompt
    assert "Do not loop through repeated fixes" in prompt


def test_system_prompt_routes_ui_validation_to_browser_tool():
    prompt = build_system_prompt(PROJECT_INFO, "No extra instructions.", "default")

    for signal in (
        "browser",
        "UI",
        "screenshot",
        "full-page screenshot",
        "visual",
        "page rendering",
        "console errors",
        "accessibility",
        "inspect page",
        "click",
        "form",
        "layout",
        "localhost URL",
        "starting a dev server",
    ):
        assert signal in prompt
    assert "Prefer available Playwright MCP tools" in prompt
    assert "run_browser_validation for an already-running" in prompt
    assert "run_managed_browser_validation" in prompt
    assert "dev_command and local_url" in prompt
    assert "requires explicit approval" in prompt
    assert "Do not substitute curl, basic HTTP checks" in prompt
    assert "Never start a server without approval" in prompt
    assert "Keep using run_validation normally for non-browser" in prompt


def test_browser_intent_detects_natural_request_and_vite_hints():
    project_info = {
        **PROJECT_INFO,
        "frameworks": ["vite", "react"],
        "package_manager": "npm",
        "dev_command": "npm run dev",
        "local_url": "http://localhost:5173",
    }
    request = (
        "Start the dev server if needed, inspect the UI in a browser, capture a "
        "full-page screenshot, and report console errors."
    )

    intent = detect_browser_intent(request, project_info)
    prompt = build_system_prompt(
        project_info,
        "No extra instructions.",
        "default",
        browser_intent=intent,
    )

    assert intent.detected is True
    assert intent.start_server is True
    assert intent.full_page is True
    assert intent.dev_command == "npm run dev"
    assert intent.url == "http://localhost:5173"
    assert {"browser", "UI", "full-page screenshot", "console errors"}.issubset(
        intent.signals
    )
    assert "Application-detected browser routing" in prompt
    assert "Call run_managed_browser_validation" in prompt
    assert "inferred_dev_command: npm run dev" in prompt
    assert "inferred_local_url: http://localhost:5173" in prompt
    assert "full_page=true" in prompt
    assert "Do not call run_validation as a substitute" in prompt


@pytest.mark.parametrize(
    "user_request",
    (
        "Open this in a browser",
        "Inspect the UI",
        "Capture a screenshot",
        "Capture a full-page screenshot",
        "Perform a visual check",
        "Check page rendering",
        "Report console errors",
        "Review accessibility",
        "Inspect page",
        "Click the submit button",
        "Fill the form",
        "Check the layout",
        "Validate the localhost URL",
        "Start the dev server",
    ),
)
def test_each_browser_routing_signal_is_detected(user_request):
    assert detect_browser_intent(user_request, PROJECT_INFO).detected is True


def test_non_browser_intent_keeps_normal_validation_routing():
    intent = detect_browser_intent(
        "Run the Python unit tests and report failures.",
        PROJECT_INFO,
    )
    prompt = build_system_prompt(
        PROJECT_INFO,
        "No extra instructions.",
        "default",
        browser_intent=intent,
    )

    assert intent.detected is False
    assert "Application-detected browser routing" not in prompt
    assert "Keep using run_validation normally for non-browser" in prompt


def test_system_prompt_requires_final_summary_sections():
    prompt = build_system_prompt(PROJECT_INFO, "No extra instructions.", "default")

    for heading in ("Changed files:", "Validation:", "Commands run:", "Checkpoints:"):
        assert heading in prompt
    assert "runtime appends the session log path" in prompt


def test_plan_prompt_and_registry_remain_read_only(tmp_path):
    prompt = build_system_prompt(PROJECT_INFO, "No extra instructions.", "plan")
    registry = create_tool_registry(tmp_path, mode="plan")
    schema_names = {
        schema["function"]["name"]
        for schema in registry.schemas(read_only=True, allow_execute=False)
    }

    assert "Use only read/search tools" in prompt
    assert "Do not call mutation, command, or validation tools" in prompt
    assert schema_names == {
        "glob",
        "grep",
        "list_dir",
        "read_file",
        "read_file_with_line_numbers",
    }
    assert "write_file" not in registry.names()
    assert "replace_lines" not in registry.names()
    assert "insert_lines" not in registry.names()
    assert "run_command" not in registry.names()
    assert "run_validation" not in registry.names()


def test_existing_read_and_execution_tools_remain_available(tmp_path):
    (tmp_path / "example.txt").write_text("hello\n", encoding="utf-8")
    registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=lambda request: False,
    )

    read_result = registry.execute("read_file", {"path": "example.txt"})

    assert read_result["ok"] is True
    assert read_result["content"] == "hello\n"
    assert {
        "create_dir",
        "edit_file",
        "glob",
        "grep",
        "list_dir",
        "read_file",
        "read_file_with_line_numbers",
        "replace_lines",
        "insert_lines",
        "run_command",
        "run_validation",
        "write_file",
    }.issubset(registry.names())


def test_subagent_system_prompt_includes_mandatory_role_boundary():
    base_prompt = build_system_prompt(
        PROJECT_INFO,
        "No extra instructions.",
        "default",
    )

    prompt = build_subagent_system_prompt(base_prompt, PLANNER_ROLE)

    assert "Active subagent role: planner" in prompt
    assert "Role instructions:" in prompt
    assert "Allowed tools:" in prompt
    assert "read_file" in prompt
    assert "write_file" in prompt
    assert "deny-by-default" in prompt

    tester_prompt = build_subagent_system_prompt(base_prompt, TESTER_ROLE)
    assert "mcp.playwright.*" in tester_prompt
    assert "run_managed_browser_validation" in tester_prompt


def test_subagent_handoff_is_bounded_and_cannot_expand_permissions():
    prompt = build_subagent_user_prompt(
        "Update the app",
        CODER_ROLE,
        {"planner": "Plan output"},
        ("app.py",),
    )

    assert "Original user request:\nUpdate the app" in prompt
    assert "Active phase: coder" in prompt
    assert "[planner]\nPlan output" in prompt
    assert "- app.py" in prompt
    assert "subject to the existing tool approval policy" in prompt
