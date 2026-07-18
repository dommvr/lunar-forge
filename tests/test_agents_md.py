from lunar_forge.instructions import (
    SAFETY_NOTICE,
    find_instruction_files,
    load_project_instructions,
)


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
