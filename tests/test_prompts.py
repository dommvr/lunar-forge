from lunar_forge.prompts import (
    build_subagent_system_prompt,
    build_subagent_user_prompt,
    build_system_prompt,
)
from lunar_forge.subagents import CODER_ROLE, PLANNER_ROLE
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


def test_system_prompt_requires_validation_and_bounded_fix_attempt():
    prompt = build_system_prompt(PROJECT_INFO, "No extra instructions.", "default")

    assert "call run_validation when practical" in prompt
    assert "attempt at most one focused fix" in prompt
    assert "then validate once more" in prompt
    assert "Do not loop through repeated fixes" in prompt


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
    assert schema_names == {"glob", "grep", "list_dir", "read_file"}
    assert "write_file" not in registry.names()
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
