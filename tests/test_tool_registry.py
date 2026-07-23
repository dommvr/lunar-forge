import re
from pathlib import Path

import pytest

from lunar_forge.permissions import PermissionLevel
from lunar_forge.tools.registry import (
    PROVIDER_TOOL_NAME_PATTERN,
    Tool,
    ToolRegistry,
    create_tool_registry,
    provider_safe_tool_name,
)


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
