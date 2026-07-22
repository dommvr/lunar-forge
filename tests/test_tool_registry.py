import re

import pytest

from lunar_forge.tools.registry import (
    PROVIDER_TOOL_NAME_PATTERN,
    Tool,
    ToolRegistry,
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
