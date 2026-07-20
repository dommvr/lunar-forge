import pytest

from lunar_forge.instructions import (
    SAFETY_NOTICE,
    find_instruction_files,
    get_instruction_stack_for_path,
    load_project_instructions,
)
from lunar_forge.tools.files import edit_file, read_file, write_file


def test_find_instruction_files_discovers_agents_md(tmp_path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Instructions", encoding="utf-8")

    assert find_instruction_files(tmp_path) == [agents_md]


def test_load_project_instructions_reads_root_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Project rules", encoding="utf-8")

    instructions = load_project_instructions(tmp_path)

    assert instructions.startswith(SAFETY_NOTICE)
    assert "# Project rules" in instructions


def test_load_project_instructions_returns_clear_fallback(tmp_path):
    instructions = load_project_instructions(tmp_path)

    assert instructions.startswith(SAFETY_NOTICE)
    assert "No AGENTS.md was found" in instructions


def test_load_project_instructions_limits_content(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x" * 100, encoding="utf-8")

    instructions = load_project_instructions(tmp_path, max_characters=20)

    assert "x" * 20 in instructions
    assert "x" * 21 not in instructions
    assert "content truncated" in instructions


def test_find_instruction_files_discovers_nested_files_in_scope_order(tmp_path):
    root_agents = tmp_path / "AGENTS.md"
    app_agents = tmp_path / "app" / "AGENTS.md"
    admin_agents = tmp_path / "app" / "admin" / "AGENTS.md"
    ignored_agents = tmp_path / "node_modules" / "AGENTS.md"

    for path in (root_agents, app_agents, admin_agents, ignored_agents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Rules", encoding="utf-8")

    assert find_instruction_files(tmp_path) == [
        root_agents,
        app_agents,
        admin_agents,
    ]


def test_instruction_stack_is_root_to_leaf_with_relative_metadata(tmp_path):
    root_agents = tmp_path / "AGENTS.md"
    app_agents = tmp_path / "app" / "AGENTS.md"
    admin_agents = tmp_path / "app" / "admin" / "AGENTS.md"
    sibling_agents = tmp_path / "other" / "AGENTS.md"
    for path, content in (
        (root_agents, "root rules"),
        (app_agents, "app rules"),
        (admin_agents, "admin rules"),
        (sibling_agents, "sibling rules"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    stack = get_instruction_stack_for_path(
        tmp_path,
        "app/admin/page.py",
    )

    assert [item["path"] for item in stack] == [
        "AGENTS.md",
        "app/AGENTS.md",
        "app/admin/AGENTS.md",
    ]
    assert [item["scope"] for item in stack] == [".", "app", "app/admin"]
    assert [item["content"] for item in stack] == [
        "root rules",
        "app rules",
        "admin rules",
    ]
    assert all(item["truncated"] is False for item in stack)


def test_instruction_stack_blocks_paths_outside_project(tmp_path):
    with pytest.raises(PermissionError, match="outside the project root"):
        get_instruction_stack_for_path(tmp_path, "../outside.py")


def test_instruction_stack_shares_a_bounded_content_budget(tmp_path):
    (tmp_path / "AGENTS.md").write_text("r" * 100, encoding="utf-8")
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("n" * 100, encoding="utf-8")

    stack = get_instruction_stack_for_path(
        tmp_path,
        "src/example.py",
        max_characters=21,
    )

    assert sum(len(item["content"]) for item in stack) <= 21
    assert [item["path"] for item in stack] == ["AGENTS.md", "src/AGENTS.md"]
    assert all(item["truncated"] is True for item in stack)


def test_nested_instructions_are_loaded_into_project_context(tmp_path):
    (tmp_path / "AGENTS.md").write_text("root rules", encoding="utf-8")
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("nested rules", encoding="utf-8")

    instructions = load_project_instructions(tmp_path)

    assert instructions.index("Project instructions from AGENTS.md") < (
        instructions.index("Project instructions from src/AGENTS.md")
    )
    assert "scope: ." in instructions
    assert "scope: src" in instructions


def test_file_tools_report_the_applicable_instruction_stack(tmp_path):
    (tmp_path / "AGENTS.md").write_text("root rules", encoding="utf-8")
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("src rules", encoding="utf-8")
    target = nested / "example.py"
    target.write_text("old value\n", encoding="utf-8")

    read_result = read_file(tmp_path, "src/example.py")
    edit_result = edit_file(
        tmp_path,
        "src/example.py",
        "old value",
        "new value",
    )
    write_result = write_file(tmp_path, "src/created.py", "created = True\n")

    expected_paths = ["AGENTS.md", "src/AGENTS.md"]
    assert [item["path"] for item in read_result["instruction_stack"]] == (
        expected_paths
    )
    assert [item["path"] for item in edit_result["instruction_stack"]] == (
        expected_paths
    )
    assert [item["path"] for item in write_result["instruction_stack"]] == (
        expected_paths
    )
