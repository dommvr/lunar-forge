import re
from pathlib import Path

import pytest

from lunar_forge.permissions import PermissionLevel
from lunar_forge.tools.registry import (
    BROWSER_TOOL_NAMES,
    COMMAND_TOOL_NAMES,
    GIT_INSPECTION_TOOLS,
    PROVIDER_TOOL_NAME_PATTERN,
    READ_NAVIGATION_TOOLS,
    PROJECT_INSPECTION_TOOLS,
    TaskProfile,
    Tool,
    ToolRegistry,
    WRITE_TOOL_NAMES,
    create_tool_registry,
    provider_safe_tool_name,
    select_task_profile,
    tool_names_for_profile,
)
from lunar_forge.tools.structured_readers import MAX_REQUEST_PATH_CHARACTERS


def _tool(name, calls=None):
    def handler(**arguments):
        if calls is not None:
            calls.append((name, arguments))
        return {"ok": True, "arguments": arguments}

    return Tool(
        name=name,
        description=f"Tool {name}.",
        parameters={"type": "object"},
        handler=handler,
    )


def test_provider_schema_names_are_safe_and_builtins_stay_unchanged():
    registry = ToolRegistry(
        (
            _tool("read_file"),
            _tool("mcp.playwright.browser_navigate"),
            _tool("example.echo"),
        )
    )

    schema_names = {
        schema["function"]["name"] for schema in registry.schemas()
    }

    assert schema_names == {
        "read_file",
        "mcp_playwright_browser_navigate",
        "example_echo",
    }
    assert all(PROVIDER_TOOL_NAME_PATTERN.fullmatch(name) for name in schema_names)
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]+", name) for name in schema_names)
    assert registry.names() == (
        "example.echo",
        "mcp.playwright.browser_navigate",
        "read_file",
    )


def test_model_alias_routes_to_internal_tool_identity():
    calls = []
    registry = ToolRegistry((_tool("example.echo", calls),))

    result = registry.execute("example_echo", {"message": "hello"})

    assert result["ok"] is True
    assert calls == [("example.echo", {"message": "hello"})]
    assert registry.internal_name_for("example_echo") == "example.echo"
    assert registry.model_name_for("example.echo") == "example_echo"


def test_provider_safe_name_collisions_are_rejected_clearly():
    registry = ToolRegistry((_tool("example.echo"),))

    with pytest.raises(
        ValueError,
        match=(
            "Provider-safe tool name collision: .*example\\.echo.*"
            "example_echo.*example_echo"
        ),
    ):
        registry.register(_tool("example_echo"))

    assert registry.names() == ("example.echo",)


def test_provider_name_normalization_rejects_empty_names():
    assert provider_safe_tool_name("mcp.playwright.navigate") == (
        "mcp_playwright_navigate"
    )
    with pytest.raises(ValueError, match="non-empty"):
        provider_safe_tool_name(" ")


def test_project_intelligence_tools_are_read_only_and_provider_safe(
    tmp_path,
):
    registry = create_tool_registry(tmp_path, mode="plan")
    intelligence_tools = {
        "project_health",
        "dependency_summary",
        "git_status",
        "git_diff",
        "list_changed_files",
    }

    assert intelligence_tools.issubset(registry.names())
    for name in intelligence_tools:
        tool = registry.get(name)
        model_name = registry.model_name_for(name)
        assert tool.permission is PermissionLevel.READ
        assert model_name == name
        assert PROVIDER_TOOL_NAME_PATTERN.fullmatch(model_name)


def test_structured_readers_are_read_only_and_provider_safe(tmp_path):
    registry = create_tool_registry(tmp_path, mode="plan")
    structured_readers = {"read_json", "read_yaml", "read_many_files"}

    assert structured_readers.issubset(registry.names())
    for name in structured_readers:
        tool = registry.get(name)
        model_name = registry.model_name_for(name)
        assert tool.permission is PermissionLevel.READ
        assert model_name == name
        assert PROVIDER_TOOL_NAME_PATTERN.fullmatch(model_name)

    many_schema = registry.get("read_many_files").parameters
    assert many_schema["properties"]["paths"]["maxItems"] == 20
    assert many_schema["additionalProperties"] is False
    for name in ("read_json", "read_yaml"):
        path_schema = registry.get(name).parameters["properties"]["path"]
        assert path_schema["minLength"] == 1
        assert path_schema["maxLength"] == MAX_REQUEST_PATH_CHARACTERS
    many_path_schema = many_schema["properties"]["paths"]["items"]
    assert many_path_schema["maxLength"] == MAX_REQUEST_PATH_CHARACTERS
    assert "small bounded set" in registry.get("read_many_files").description


def test_list_symbols_is_read_only_and_provider_safe(tmp_path):
    registry = create_tool_registry(tmp_path, mode="plan")

    assert "list_symbols" in registry.names()
    tool = registry.get("list_symbols")
    assert tool.permission is PermissionLevel.READ
    assert registry.model_name_for("list_symbols") == "list_symbols"
    assert PROVIDER_TOOL_NAME_PATTERN.fullmatch("list_symbols")
    assert tool.parameters["required"] == ["path"]
    assert tool.parameters["additionalProperties"] is False


def test_ci_summary_is_read_only_and_provider_safe(tmp_path):
    registry = create_tool_registry(tmp_path, mode="plan")

    assert "ci_summary" in registry.names()
    tool = registry.get("ci_summary")
    assert tool.permission is PermissionLevel.READ
    assert registry.model_name_for("ci_summary") == "ci_summary"
    assert PROVIDER_TOOL_NAME_PATTERN.fullmatch("ci_summary")
    assert tool.parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_plan_registry_exposes_context_suite_without_write_or_command_tools(
    tmp_path,
):
    registry = create_tool_registry(tmp_path, mode="plan")
    context_tools = {
        "project_health",
        "dependency_summary",
        "git_status",
        "git_diff",
        "list_changed_files",
        "read_json",
        "read_yaml",
        "read_many_files",
        "list_symbols",
        "ci_summary",
    }

    assert context_tools.issubset(registry.names())
    assert all(
        registry.get(name).permission is PermissionLevel.READ
        for name in context_tools
    )
    assert {
        "create_dir",
        "write_file",
        "edit_file",
        "replace_lines",
        "insert_lines",
        "run_command",
        "run_validation",
        "git_commit",
    }.isdisjoint(registry.names())


def _profile_schema_names(registry, profile, **kwargs):
    return {
        registry.internal_name_for(schema["function"]["name"])
        for schema in registry.schemas(profile=profile, **kwargs)
    }


def test_task_profile_selection_is_deterministic():
    cases = (
        (
            "Run read_json on package.json and summarize scripts.",
            {},
            TaskProfile.EXPLICIT_READONLY,
            ("read_json",),
        ),
        (
            "Plan how to update the parser.",
            {"mode": "plan"},
            TaskProfile.PLAN_ONLY,
            (),
        ),
        (
            "Review the current diff without changing files.",
            {},
            TaskProfile.REVIEW_ONLY,
            (),
        ),
        (
            "Implement parser support.",
            {},
            TaskProfile.EDIT_TASK,
            (),
        ),
        (
            "Use web_design.review_files to review index.html. Do not edit files.",
            {},
            TaskProfile.REVIEW_ONLY,
            (),
        ),
        (
            "Update the UI and capture a screenshot.",
            {"browser_intent": True},
            TaskProfile.BROWSER_TASK,
            (),
        ),
        (
            "Update docs and commit them.",
            {"commit_requested": True},
            TaskProfile.COMMIT_TASK,
            (),
        ),
        (
            "Create a new starter.",
            {"new_project": True},
            TaskProfile.NEW_PROJECT,
            (),
        ),
    )

    for request, kwargs, expected_profile, expected_tools in cases:
        selection = select_task_profile(request, **kwargs)
        assert selection.profile is expected_profile
        assert selection.requested_tools == expected_tools


def test_explicit_readonly_exposes_requested_and_minimal_support_only(tmp_path):
    registry = create_tool_registry(tmp_path, mode="default")

    assert _profile_schema_names(
        registry,
        TaskProfile.EXPLICIT_READONLY,
        requested_tools=("read_json",),
    ) == {"read_json"}
    assert _profile_schema_names(
        registry,
        TaskProfile.EXPLICIT_READONLY,
        requested_tools=("list_symbols",),
    ) == {"list_symbols", "read_file_with_line_numbers"}
    assert _profile_schema_names(
        registry,
        TaskProfile.EXPLICIT_READONLY,
        requested_tools=("git_diff",),
    ) == {"git_diff", "git_status"}


def test_plan_review_edit_and_new_project_profiles_have_bounded_sets(tmp_path):
    registry = create_tool_registry(tmp_path, mode="default")
    available = set(registry.names())
    read_only = (
        READ_NAVIGATION_TOOLS
        | PROJECT_INSPECTION_TOOLS
        | GIT_INSPECTION_TOOLS
    ) & available

    plan_tools = _profile_schema_names(registry, TaskProfile.PLAN_ONLY)
    review_tools = _profile_schema_names(registry, TaskProfile.REVIEW_ONLY)
    edit_tools = _profile_schema_names(registry, TaskProfile.EDIT_TASK)
    new_project_tools = _profile_schema_names(
        registry,
        TaskProfile.NEW_PROJECT,
    )

    assert plan_tools == read_only
    assert review_tools == read_only
    assert edit_tools == (
        read_only | WRITE_TOOL_NAMES | COMMAND_TOOL_NAMES
    )
    assert new_project_tools == {
        "list_dir",
        "read_file",
        "read_json",
        "read_yaml",
        "dependency_summary",
        "create_dir",
        "write_file",
        "run_command",
        "run_validation",
    }
    assert BROWSER_TOOL_NAMES.isdisjoint(edit_tools)


def test_browser_commit_and_no_command_gates_are_explicit(tmp_path):
    registry = create_tool_registry(tmp_path, mode="default")
    registry.register(
        Tool(
            name="git_commit",
            description="Synthetic commit execution helper.",
            parameters={"type": "object"},
            handler=lambda **arguments: {"ok": True},
            permission=PermissionLevel.EXECUTE,
        )
    )

    browser_hidden = _profile_schema_names(
        registry,
        TaskProfile.BROWSER_TASK,
        browser_intent=False,
    )
    browser_visible = _profile_schema_names(
        registry,
        TaskProfile.BROWSER_TASK,
        browser_intent=True,
    )
    commit_hidden = _profile_schema_names(
        registry,
        TaskProfile.COMMIT_TASK,
        commit_requested=False,
    )
    commit_visible = _profile_schema_names(
        registry,
        TaskProfile.COMMIT_TASK,
        commit_requested=True,
    )
    no_command = _profile_schema_names(
        registry,
        TaskProfile.COMMIT_TASK,
        commit_requested=True,
        allow_execute=False,
    )

    assert BROWSER_TOOL_NAMES.isdisjoint(browser_hidden)
    assert BROWSER_TOOL_NAMES <= browser_visible
    assert "git_commit" not in commit_hidden
    assert "git_commit" in commit_visible
    assert {
        "run_command",
        "run_validation",
        "git_commit",
    }.isdisjoint(no_command)


def test_plan_profile_omits_extensions_mutation_browser_and_commit_tools():
    registry = ToolRegistry(
        (
            _tool("read_file"),
            _tool("example.echo"),
            _tool("mcp.playwright.browser_navigate"),
            Tool(
                name="write_file",
                description="Write.",
                parameters={"type": "object"},
                handler=lambda **arguments: {"ok": True},
                permission=PermissionLevel.WRITE,
            ),
            Tool(
                name="run_managed_browser_validation",
                description="Start a server.",
                parameters={"type": "object"},
                handler=lambda **arguments: {"ok": True},
                permission=PermissionLevel.EXECUTE,
            ),
            Tool(
                name="git_commit",
                description="Commit.",
                parameters={"type": "object"},
                handler=lambda **arguments: {"ok": True},
                permission=PermissionLevel.EXECUTE,
            ),
        )
    )

    names = _profile_schema_names(
        registry,
        TaskProfile.PLAN_ONLY,
        read_only=True,
        requested_tools=(
            "example.echo",
            "mcp.playwright.browser_navigate",
            "git_commit",
        ),
        browser_intent=True,
        commit_requested=True,
    )

    assert names == {"read_file"}


def test_relevant_extensions_keep_provider_safe_mapping():
    registry = ToolRegistry(
        (
            _tool("read_file"),
            _tool("example.echo"),
            _tool("mcp.playwright.browser_navigate"),
        )
    )

    plugin_schemas = registry.schemas(
        profile=TaskProfile.EDIT_TASK,
        requested_tools=registry.relevant_tool_names("Use the example plugin"),
    )
    browser_schemas = registry.schemas(
        profile=TaskProfile.BROWSER_TASK,
        browser_intent=True,
    )

    assert {
        schema["function"]["name"] for schema in plugin_schemas
    } == {"read_file", "example_echo"}
    assert {
        schema["function"]["name"] for schema in browser_schemas
    } == {"read_file", "mcp_playwright_browser_navigate"}
    assert tool_names_for_profile(
        TaskProfile.EXPLICIT_READONLY,
        requested_tools=("read_json",),
        available_tools=("read_json", "write_file"),
    ) == ("read_json",)


def test_provider_sdk_imports_are_isolated_to_model_clients():
    package_root = Path(__file__).parents[1] / "lunar_forge"
    provider_import = re.compile(
        r"^\s*(?:from|import)\s+(?:litellm|openai|anthropic)\b",
        re.MULTILINE,
    )
    leaked_imports = []

    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root)
        if relative.parts[0] == "model_clients":
            continue
        if provider_import.search(path.read_text(encoding="utf-8")):
            leaked_imports.append(relative.as_posix())

    assert leaked_imports == []
