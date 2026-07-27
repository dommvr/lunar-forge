import pytest

from lunar_forge.prompts import (
    build_readonly_fast_path_messages,
    build_subagent_system_prompt,
    build_subagent_user_prompt,
    build_system_prompt,
    detect_browser_intent,
)
from lunar_forge.subagents import (
    CODER_ROLE,
    PLANNER_ROLE,
    REVIEWER_ROLE,
    SECURITY_ROLE,
    TESTER_ROLE,
)
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
    assert "start with project_health and dependency_summary" in prompt
    assert "call dependency_summary" in prompt
    assert "tiny targeted edit" in prompt
    assert "Use read_json for a known JSON" in prompt
    assert "Use read_yaml for a known YAML" in prompt
    assert "instead of raw\n  read_file" in prompt
    assert "Use read_many_files only for a small" in prompt
    assert "Never try another tool to bypass" in prompt
    assert "prefer list_symbols before reading broad file ranges" in prompt
    assert "Skip list_symbols" in prompt
    assert "call ci_summary before" in prompt
    assert "inventing or finalizing validation commands" in prompt
    assert "Do not call ci_summary when" in prompt
    assert "Do not call every introspection tool for\n  every small task" in prompt
    assert "Never use bare `python`, `python.exe`, `py`, or `py.exe`" in prompt
    assert "prefer `python -B -m compileall .`" in prompt


def test_system_prompt_scales_project_intelligence_to_the_task():
    prompt = build_system_prompt(PROJECT_INFO, "No extra instructions.", "default")

    assert "broad project reviews" in prompt
    assert "onboarding, or feature\n  planning" in prompt
    assert "start with project_health and dependency_summary" in prompt
    assert "Before planning validation" in prompt
    assert "call dependency_summary" in prompt
    assert "Before a review, final change summary, or commit proposal" in prompt
    assert "call git_status and\n  list_changed_files first" in prompt
    assert "Use git_diff only when Git changes exist" in prompt
    assert "For a tiny targeted edit" in prompt
    assert "Tool calls are not a checklist" in prompt


def test_system_and_subagent_prompts_name_active_task_profiles():
    base_prompt = build_system_prompt(
        PROJECT_INFO,
        "No extra instructions.",
        "default",
        task_profile="review_only",
    )
    subagent_prompt = build_subagent_system_prompt(
        base_prompt,
        REVIEWER_ROLE,
        task_profile="review_only",
    )

    assert "Active task profile: review_only" in base_prompt
    assert "Only the profile-relevant provider-safe tool schemas" in base_prompt
    assert "Active model-call task profile: review_only" in subagent_prompt


def test_planner_inspection_phase_directly_answers_readonly_request():
    base_prompt = build_system_prompt(
        PROJECT_INFO,
        "No extra instructions.",
        "default",
        task_profile="review_only",
    )
    system_prompt = build_subagent_system_prompt(
        base_prompt,
        PLANNER_ROLE,
        task_profile="review_only",
        phase="inspect",
    )
    user_prompt = build_subagent_user_prompt(
        "Explain the package layout.",
        PLANNER_ROLE,
        phase="inspect",
    )

    assert "Active subagent phase: inspect" in system_prompt
    assert "directly answer the read-only request" in system_prompt
    assert "Active phase: inspect" in user_prompt
    assert "Do not produce an implementation plan" in user_prompt
    assert "hand work to another role" in user_prompt


def test_readonly_fast_path_prompt_is_compact_and_action_free():
    messages = build_readonly_fast_path_messages(
        "Run git_status and report whether the repo is clean.",
        "git_status",
        {},
        '{"ok": true, "clean": true}',
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "read-only result summarizer" in messages[0]["content"]
    assert "Do not claim edits, validation, browser actions, commits" in (
        messages[0]["content"]
    )
    assert "Do not add workflow status sections or a subagent list" in (
        messages[0]["content"]
    )
    assert "Read-only tool: git_status" in messages[1]["content"]
    assert '"clean": true' in messages[1]["content"]


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
        (
            "Use read_json to inspect "
            "examples/projects/browser-demo/package.json."
        ),
        (
            "Use list_symbols on "
            "examples/projects/browser-demo/src/App.jsx."
        ),
        (
            "Use list_symbols on browser-demo/App.jsx. "
            "Do not edit files."
        ),
        "Use list_symbols on App.jsx.",
    ),
)
def test_file_paths_do_not_trigger_browser_intent(user_request):
    intent = detect_browser_intent(user_request, PROJECT_INFO)

    assert intent.detected is False
    assert intent.signals == ()
    assert intent.url is None
    assert intent.dev_command is None


def test_explicit_source_review_plugin_does_not_trigger_browser_intent():
    request = (
        "Use web_design.review_files to review index.html, src\\App.jsx, "
        "and src\\App.css for accessibility, responsive layout, and visual "
        "hierarchy. Do not edit files."
    )

    intent = detect_browser_intent(request, PROJECT_INFO)

    assert intent.detected is False
    assert intent.signals == ()


@pytest.mark.parametrize(
    "user_request",
    (
        (
            "Open examples/projects/browser-demo/src/App.jsx in a browser "
            "and capture a screenshot."
        ),
        "Validate the local frontend and report console errors.",
        "Run browser validation for examples/projects/browser-demo.",
    ),
)
def test_explicit_browser_behavior_with_paths_remains_detected(user_request):
    assert detect_browser_intent(user_request, PROJECT_INFO).detected is True


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


def test_no_edit_execution_profile_guidance_preserves_non_edit_tools():
    prompt = build_system_prompt(
        PROJECT_INFO,
        "No extra instructions.",
        "default",
        task_profile="no_edit_execution_allowed",
    )

    assert "blocks filesystem mutation tools" in prompt
    assert "explicitly requested command" in prompt
    assert "normal permissions allow it" in prompt


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
        "dependency_summary",
        "ci_summary",
        "git_diff",
        "git_status",
        "glob",
        "grep",
        "list_dir",
        "list_changed_files",
        "list_symbols",
        "project_health",
        "read_file",
        "read_file_with_line_numbers",
        "read_json",
        "read_many_files",
        "read_yaml",
    }
    assert "write_file" not in registry.names()
    assert "replace_lines" not in registry.names()
    assert "insert_lines" not in registry.names()
    assert "run_command" not in registry.names()
    assert "run_validation" not in registry.names()

    write_result = registry.execute(
        "write_file",
        {"path": "should-not-exist.txt", "content": "blocked"},
    )
    command_result = registry.execute(
        "run_command",
        {"command": "python -c \"print('must not run')\""},
    )

    assert write_result["ok"] is False
    assert command_result["ok"] is False
    assert not (tmp_path / "should-not-exist.txt").exists()


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


def test_subagent_handoffs_include_role_specific_intelligence_guidance():
    planner = build_subagent_user_prompt("Plan a feature", PLANNER_ROLE)
    coder = build_subagent_user_prompt("Implement a feature", CODER_ROLE)
    tester = build_subagent_user_prompt("Validate a feature", TESTER_ROLE)
    reviewer = build_subagent_user_prompt("Review a feature", REVIEWER_ROLE)
    security = build_subagent_user_prompt("Audit a feature", SECURITY_ROLE)

    assert "broad review, onboarding, or feature planning" in planner
    assert "dependency_summary before" in planner
    assert "tiny single-file tasks narrowly scoped" in planner
    assert "instead of calling every introspection tool" in planner
    assert "git_status and list_changed_files" in planner
    assert "git_diff only when changed-file details are needed" in planner
    assert "Use read_json for known JSON configuration" in planner
    assert "read_yaml for known YAML configuration instead of raw read_file" in planner
    assert "batch only a small related file set" in planner
    assert "list_symbols before broad reads" in planner
    assert "CI configuration exists, use ci_summary before inventing" in planner
    assert "Use read_json for known JSON configuration" in coder
    assert "read_many_files only for a small known related file set" in coder
    assert "list_symbols before broad reads" in coder
    assert "dependency_summary before guessing uncertain commands" in tester
    assert "ci_summary before inventing validation commands" in tester
    assert "Use read_json for relevant JSON configuration" in tester
    assert "read_many_files only for a small known set" in tester
    assert "list_changed_files when it helps focus validation" in tester
    assert "Use read_json for relevant JSON configuration" in reviewer
    assert "read_many_files only for a small known changed-file set" in reviewer
    assert "list_symbols before broad reads" in reviewer
    assert "Use read_json for relevant non-secret JSON configuration" in security
    assert "read_many_files only for a small known set" in security
