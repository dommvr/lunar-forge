import json
from typing import Any

import pytest

import lunar_forge.mcp.config as mcp_config_module
from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig, MCPRuntimeConfig
from lunar_forge.mcp.client import (
    MAX_DISCOVERED_TOOLS,
    MAX_OUTPUT_CHARACTERS,
    MCPClient,
    MCPClientError,
)
from lunar_forge.mcp.config import MCPConfig, MCPServerConfig
from lunar_forge.mcp.registry import namespace_mcp_tool, register_mcp_tools
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.permissions import PermissionLevel, PermissionManager
from lunar_forge.tools.registry import ToolRegistry, create_tool_registry


class FakeTransport:
    def __init__(self, tools, result=None):
        self.tools = tools
        self.result = {"content": "created"} if result is None else result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self):
        return self.tools

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return self.result


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.tool_schemas = []

    def complete(self, messages, tools=None):
        self.tool_schemas.append(list(tools or []))
        return self.responses.pop(0)


def _config(*, enabled: bool) -> MCPConfig:
    server = MCPServerConfig(
        name="github",
        command="github-mcp-server",
        enabled=enabled,
    )
    return MCPConfig(servers={"github": server})


def _github_tools():
    return [
        {
            "name": "create_issue",
            "description": "Create an issue.",
            "inputSchema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
        }
    ]


def _github_tools_with_read_only_search():
    return _github_tools() + [
        {
            "name": "search_issues",
            "description": "Search issues without modifying them.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True},
        }
    ]


def test_disabled_server_is_not_contacted_or_registered():
    factory_calls = []

    def unexpected_factory(server):
        factory_calls.append(server)
        raise AssertionError("Disabled MCP server was contacted")

    client = MCPClient(_config(enabled=False), unexpected_factory)
    registry = ToolRegistry()

    assert register_mcp_tools(registry, client) == ()
    assert registry.names() == ()
    assert factory_calls == []


def test_mocked_schema_registers_as_namespaced_registry_tool():
    transport = FakeTransport(_github_tools())
    client = MCPClient(_config(enabled=True), lambda server: transport)
    registry = ToolRegistry()

    registered = register_mcp_tools(registry, client)
    tool = registry.get("mcp.github.create_issue")

    assert registered == ("mcp.github.create_issue",)
    assert tool.name == "mcp.github.create_issue"
    assert tool.parameters["required"] == ["title"]
    assert tool.permission is PermissionLevel.EXECUTE
    assert registry.names() == ("mcp.github.create_issue",)
    assert registry.schemas()[0]["function"]["name"] == (
        "mcp_github_create_issue"
    )


def test_integrated_registry_preserves_builtins_and_adds_mcp_tools(tmp_path):
    transport = FakeTransport(_github_tools())
    registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=lambda request: True,
        mcp_client=MCPClient(_config(enabled=True), lambda server: transport),
    )

    assert {"list_dir", "read_file", "write_file", "run_command"}.issubset(
        registry.names()
    )
    assert "mcp.github.create_issue" in registry.names()


def test_namespaced_calls_route_to_the_matching_server_client(tmp_path):
    github_transport = FakeTransport(
        [{"name": "search", "inputSchema": {"type": "object"}}],
        result={"source": "github"},
    )
    notes_transport = FakeTransport(
        [{"name": "search", "inputSchema": {"type": "object"}}],
        result={"source": "notes"},
    )
    config = MCPConfig(
        servers={
            "github": MCPServerConfig(
                name="github",
                command="github-mcp-server",
                enabled=True,
            ),
            "notes": MCPServerConfig(
                name="notes",
                command="notes-mcp-server",
                enabled=True,
            ),
        }
    )
    transports = {"github": github_transport, "notes": notes_transport}
    registry = create_tool_registry(
        tmp_path,
        approval_callback=lambda request: True,
        mcp_client=MCPClient(config, lambda server: transports[server.name]),
    )

    github_result = registry.execute("mcp_github_search", {"query": "one"})
    notes_result = registry.execute("mcp_notes_search", {"query": "two"})

    assert github_result["result"] == {"source": "github"}
    assert notes_result["result"] == {"source": "notes"}
    assert github_transport.calls == [("search", {"query": "one"})]
    assert notes_transport.calls == [("search", {"query": "two"})]


def test_plan_registry_includes_only_explicitly_read_only_mcp_tools(tmp_path):
    requests = []
    transport = FakeTransport(
        _github_tools_with_read_only_search(),
        result={"issues": []},
    )
    registry = create_tool_registry(
        tmp_path,
        mode="plan",
        approval_callback=lambda request: requests.append(request) or True,
        mcp_client=MCPClient(_config(enabled=True), lambda server: transport),
    )

    assert "list_dir" in registry.names()
    assert "mcp.github.search_issues" in registry.names()
    assert "mcp.github.create_issue" not in registry.names()
    read_tool = registry.get("mcp.github.search_issues")
    assert read_tool.permission is PermissionLevel.NETWORK
    assert read_tool.plan_safe is True

    result = registry.execute(
        "mcp_github_search_issues",
        {"query": "is:open"},
    )

    assert result["ok"] is True
    assert transport.calls == [("search_issues", {"query": "is:open"})]
    assert len(requests) == 1


def test_agent_discovers_and_executes_enabled_mcp_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_config_module.Path,
        "home",
        classmethod(lambda cls: tmp_path / "home"),
    )
    config_directory = tmp_path / ".agent"
    config_directory.mkdir()
    (config_directory / "mcp.yaml").write_text(
        """
servers:
  github:
    command: github-mcp-server
    enabled: true
""".lstrip(),
        encoding="utf-8",
    )
    transport = FakeTransport(_github_tools())
    transport.closed = False
    transport.close = lambda: setattr(transport, "closed", True)
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="mcp-call",
                        name="mcp_github_create_issue",
                        arguments={"title": "Integrated"},
                    ),
                ),
            ),
            ModelResponse(text="Issue created."),
        )
    )
    agent = CodeAgent(
        AppConfig(mcp=MCPRuntimeConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: True,
        mcp_transport_factory=lambda server: transport,
    )

    output = agent.run("Create a tracked issue", tmp_path)

    first_schema_names = {
        schema["function"]["name"] for schema in model.tool_schemas[0]
    }
    assert "read_file" in first_schema_names
    assert "mcp_github_create_issue" in first_schema_names
    assert all("." not in name for name in first_schema_names)
    assert transport.calls == [("create_issue", {"title": "Integrated"})]
    assert transport.closed is True
    assert output.startswith("Issue created.")

    session_file = next((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    events = [
        json.loads(line)
        for line in session_file.read_text(encoding="utf-8").splitlines()
    ]
    tool_call_event = next(
        event for event in events if event["event"] == "tool_call"
    )
    assert tool_call_event["data"]["model_tool_name"] == (
        "mcp_github_create_issue"
    )
    assert tool_call_event["data"]["internal_tool_name"] == (
        "mcp.github.create_issue"
    )


def test_agent_does_not_discover_mcp_when_global_switch_is_disabled(tmp_path):
    config_directory = tmp_path / ".agent"
    config_directory.mkdir()
    (config_directory / "mcp.yaml").write_text(
        """
servers:
  github:
    command: github-mcp-server
    enabled: true
""".lstrip(),
        encoding="utf-8",
    )
    model = SequenceModel((ModelResponse(text="No MCP used."),))

    output = CodeAgent(
        AppConfig(),
        model_client=model,
        mcp_transport_factory=lambda server: pytest.fail(
            "Disabled MCP integration must not create a transport"
        ),
    ).run("Explain the project", tmp_path, mode="plan")

    schema_names = {schema["function"]["name"] for schema in model.tool_schemas[0]}
    assert not any(name.startswith("mcp_") for name in schema_names)
    assert output.startswith("No MCP used.")


def test_mcp_call_denial_prevents_transport_invocation():
    transport = FakeTransport(_github_tools())
    client = MCPClient(_config(enabled=True), lambda server: transport)
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: False,
        )
    )
    register_mcp_tools(registry, client)

    result = registry.execute(
        "mcp.github.create_issue",
        {"title": "Denied"},
    )

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert transport.calls == []


def test_approved_mcp_call_reaches_mock_transport():
    requests = []
    transport = FakeTransport(_github_tools())
    client = MCPClient(_config(enabled=True), lambda server: transport)
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: requests.append(request) or True,
        )
    )
    register_mcp_tools(registry, client)

    result = registry.execute(
        "mcp_github_create_issue",
        {"title": "Approved"},
    )

    assert result == {
        "ok": True,
        "result": {"content": "created"},
        "truncated": False,
    }
    assert transport.calls == [("create_issue", {"title": "Approved"})]
    assert requests[0].tool_name == "mcp.github.create_issue"
    assert requests[0].permission is PermissionLevel.EXECUTE


def test_yes_mode_still_requests_mcp_approval():
    requests = []
    transport = FakeTransport(_github_tools())
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="yes",
            approval_callback=lambda request: requests.append(request) or False,
        )
    )
    register_mcp_tools(
        registry,
        MCPClient(_config(enabled=True), lambda server: transport),
    )

    result = registry.execute(
        "mcp.github.create_issue",
        {"title": "Still gated"},
    )

    assert result["permission_denied"] is True
    assert len(requests) == 1
    assert transport.calls == []


def test_plan_mode_blocks_registered_mcp_tool_without_prompting():
    transport = FakeTransport(_github_tools())

    def unexpected_prompt(request):
        raise AssertionError("Plan mode should block before prompting")

    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="plan",
            approval_callback=unexpected_prompt,
        )
    )
    register_mcp_tools(
        registry,
        MCPClient(_config(enabled=True), lambda server: transport),
    )

    result = registry.execute(
        "mcp.github.create_issue",
        {"title": "Blocked"},
    )

    assert result["permission_denied"] is True
    assert "Plan mode blocks" in result["error"]
    assert transport.calls == []


def test_mcp_output_is_bounded_and_json_serializable():
    transport = FakeTransport(_github_tools(), result={"content": "x" * 100_000})
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: True,
        )
    )
    register_mcp_tools(
        registry,
        MCPClient(_config(enabled=True), lambda server: transport),
    )

    result = registry.execute(
        "mcp.github.create_issue",
        {"title": "Bound output"},
    )

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(json.dumps(result)) <= MAX_OUTPUT_CHARACTERS


def test_non_serializable_mcp_output_becomes_safe_error():
    transport = FakeTransport(_github_tools(), result=object())
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: True,
        )
    )
    register_mcp_tools(
        registry,
        MCPClient(_config(enabled=True), lambda server: transport),
    )

    result = registry.execute(
        "mcp.github.create_issue",
        {"title": "Bad output"},
    )

    assert result == {
        "ok": False,
        "error": "MCP tool returned a non-serializable result.",
    }


def test_non_finite_mcp_output_becomes_safe_error():
    transport = FakeTransport(_github_tools(), result={"value": float("nan")})
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: True,
        )
    )
    register_mcp_tools(
        registry,
        MCPClient(_config(enabled=True), lambda server: transport),
    )

    result = registry.execute(
        "mcp.github.create_issue",
        {"title": "Bad output"},
    )

    assert result == {
        "ok": False,
        "error": "MCP tool returned a non-serializable result.",
    }


def test_mcp_transport_exception_does_not_expose_its_message():
    secret = "transport-secret-value"

    class BrokenTransport(FakeTransport):
        def call_tool(self, name, arguments):
            raise ValueError(secret)

    registry = ToolRegistry(
        permission_manager=PermissionManager(
            approval_callback=lambda request: True,
        )
    )
    register_mcp_tools(
        registry,
        MCPClient(_config(enabled=True), lambda server: BrokenTransport(_github_tools())),
    )

    result = registry.execute("mcp.github.create_issue", {"title": "Failure"})

    assert result["error"] == "MCP tool call failed with ValueError."
    assert secret not in json.dumps(result)


@pytest.mark.parametrize(
    ("server_name", "tool_name"),
    (("GitHub", "create_issue"), ("github", "create issue")),
)
def test_invalid_namespace_parts_are_rejected(server_name, tool_name):
    with pytest.raises(ValueError, match="Invalid MCP"):
        namespace_mcp_tool(server_name, tool_name)


def test_client_closes_created_mock_transport():
    transport = FakeTransport(_github_tools())
    transport.closed = False
    transport.close = lambda: setattr(transport, "closed", True)
    client = MCPClient(_config(enabled=True), lambda server: transport)

    client.discover_tools("github")
    client.close()

    assert transport.closed is True


def test_registry_bounds_total_tools_across_enabled_servers():
    tools_per_server = (MAX_DISCOVERED_TOOLS // 2) + 1
    transports = {
        server_name: FakeTransport(
            [
                {
                    "name": f"tool_{index}",
                    "inputSchema": {"type": "object"},
                }
                for index in range(tools_per_server)
            ]
        )
        for server_name in ("one", "two")
    }
    config = MCPConfig(
        servers={
            name: MCPServerConfig(name=name, command="mock", enabled=True)
            for name in transports
        }
    )
    registry = ToolRegistry()

    with pytest.raises(MCPClientError, match="too many tools"):
        register_mcp_tools(
            registry,
            MCPClient(config, lambda server: transports[server.name]),
        )

    assert registry.names() == ()
