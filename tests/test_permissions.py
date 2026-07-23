import json
from importlib import import_module

from lunar_forge.permissions import (
    PermissionDecision,
    PermissionLevel,
    PermissionManager,
    PermissionRequest,
    requires_approval,
)
from lunar_forge.tools.registry import (
    MAX_REGISTRY_RESULT_CHARACTERS,
    MAX_REGISTERED_TOOLS,
    REDACTED_TOOL_VALUE,
    Tool,
    ToolRegistry,
    create_tool_registry,
)


project_health_module = import_module("lunar_forge.tools.project_health")


def test_permission_decision_defaults_to_empty_reason():
    decision = PermissionDecision(allowed=True)

    assert decision.allowed
    assert decision.reason == ""


def test_write_requires_approval_by_default():
    assert requires_approval(PermissionLevel.WRITE)


def test_default_mode_asks_before_write_tool(tmp_path):
    requests: list[PermissionRequest] = []

    def approve(request: PermissionRequest) -> bool:
        requests.append(request)
        return True

    registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=approve,
    )
    result = registry.execute("create_dir", {"path": "src"})

    assert result["ok"] is True
    assert (tmp_path / "src").is_dir()
    assert len(requests) == 1
    assert requests[0].tool_name == "create_dir"
    assert requests[0].permission is PermissionLevel.WRITE
    assert requests[0].description == "Create directory for src."


def test_default_mode_denial_prevents_write(tmp_path):
    registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=lambda request: False,
    )

    result = registry.execute(
        "write_file",
        {"path": "denied.txt", "content": "not written"},
    )

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert not (tmp_path / "denied.txt").exists()


def test_plan_mode_excludes_and_blocks_write_tools(tmp_path):
    plan_registry = create_tool_registry(tmp_path, mode="plan")

    assert "write_file" not in plan_registry.names()
    assert "edit_file" not in plan_registry.names()
    assert "replace_lines" not in plan_registry.names()
    assert "insert_lines" not in plan_registry.names()
    assert "create_dir" not in plan_registry.names()

    default_registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=lambda request: True,
    )
    default_registry.set_permission_manager(PermissionManager(mode="plan"))
    result = default_registry.execute(
        "write_file",
        {"path": "blocked.txt", "content": "not written"},
    )

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert "Plan mode blocks" in result["error"]
    assert not (tmp_path / "blocked.txt").exists()


def test_default_mode_asks_before_command_execution():
    requests: list[PermissionRequest] = []

    def deny(request: PermissionRequest) -> bool:
        requests.append(request)
        return False

    manager = PermissionManager(mode="default", approval_callback=deny)
    decision = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "python --version"},
    )

    assert decision.allowed is False
    assert decision.reason == "Denied by user."
    assert len(requests) == 1
    assert requests[0].permission is PermissionLevel.EXECUTE
    assert requests[0].description == "Run command: python --version."


def test_plan_and_no_command_modes_block_command_execution(tmp_path):
    plan_registry = create_tool_registry(tmp_path, mode="plan")
    no_command_registry = create_tool_registry(tmp_path, mode="no-command")

    assert "run_command" not in plan_registry.names()
    assert "run_command" not in no_command_registry.names()
    read_only_intelligence_tools = {
        "project_health",
        "dependency_summary",
        "git_status",
        "git_diff",
        "list_changed_files",
    }
    assert read_only_intelligence_tools.issubset(plan_registry.names())
    assert read_only_intelligence_tools.issubset(
        no_command_registry.names()
    )
    assert no_command_registry.execute("git_status", {})["ok"] is False
    assert "No-command mode" in no_command_registry.execute(
        "git_diff",
        {},
    )["error"]

    for mode, expected_reason in (
        ("plan", "Plan mode blocks"),
        ("no-command", "No-command mode blocks"),
    ):
        manager = PermissionManager(mode=mode)
        decision = manager.authorize(
            PermissionLevel.EXECUTE,
            "run_command",
            {"command": "python --version"},
        )
        assert decision.allowed is False
        assert expected_reason in decision.reason


def test_no_command_project_health_skips_git_without_requesting_approval(
    monkeypatch,
    tmp_path,
):
    def unexpected_git(root):
        raise AssertionError("No-command health inspection must not execute Git")

    monkeypatch.setattr(
        project_health_module,
        "_tracked_suspicious_paths",
        unexpected_git,
    )
    registries = (
        create_tool_registry(
            tmp_path,
            mode="no-command",
            approval_callback=lambda request: (_ for _ in ()).throw(
                AssertionError("Read-only tools must not request approval")
            ),
        ),
        create_tool_registry(
            tmp_path,
            mode="default",
            runtime_mode="no-command",
            approval_callback=lambda request: (_ for _ in ()).throw(
                AssertionError("Read-only tools must not request approval")
            ),
        ),
    )

    for registry in registries:
        result = registry.execute("project_health", {})

        assert result["ok"] is True
        assert result["tracked_path_check"] == "skipped_no_command"


def test_plan_mode_allows_approved_plan_safe_network_read():
    requests: list[PermissionRequest] = []
    manager = PermissionManager(
        mode="plan",
        approval_callback=lambda request: requests.append(request) or True,
    )

    decision = manager.authorize(
        PermissionLevel.NETWORK,
        "mcp.github.search_issues",
        {"query": "is:open"},
        plan_safe=True,
    )

    assert decision.allowed is True
    assert len(requests) == 1
    assert requests[0].permission is PermissionLevel.NETWORK


def test_plan_safe_flag_cannot_allow_writes_in_plan_mode():
    def unexpected_approval(request: PermissionRequest) -> bool:
        raise AssertionError("Plan mode must block writes before prompting")

    decision = PermissionManager(
        mode="plan",
        approval_callback=unexpected_approval,
    ).authorize(
        PermissionLevel.WRITE,
        "write_file",
        {"path": "blocked.txt"},
        plan_safe=True,
    )

    assert decision.allowed is False
    assert "Plan mode blocks" in decision.reason


def test_dangerous_command_is_denied_without_requesting_approval():
    def unexpected_approval(request: PermissionRequest) -> bool:
        raise AssertionError(f"Approval requested for {request.tool_name}")

    manager = PermissionManager(
        mode="default",
        approval_callback=unexpected_approval,
    )
    decision = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "sudo python --version"},
    )

    assert decision.allowed is False
    assert "blocked by safety policy" in decision.reason


def test_command_approval_preview_redacts_api_key_like_values():
    requests: list[PermissionRequest] = []
    secret = "credential-value-without-a-provider-prefix"

    manager = PermissionManager(
        mode="default",
        approval_callback=lambda request: requests.append(request) or False,
    )
    manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": f"example-program --api-key {secret} --version"},
    )

    assert len(requests) == 1
    assert secret not in requests[0].description
    assert "[REDACTED]" in requests[0].description


def test_quoted_dangerous_command_is_denied_before_approval():
    def unexpected_approval(request: PermissionRequest) -> bool:
        raise AssertionError(f"Approval requested for {request.tool_name}")

    decision = PermissionManager(
        mode="default",
        approval_callback=unexpected_approval,
    ).authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "rm '-rf' build"},
    )

    assert decision.allowed is False
    assert "rm -rf" in decision.reason


def test_registry_contains_handler_exceptions_without_exposing_messages():
    secret = "handler-secret-value"

    def broken():
        raise RuntimeError(secret)

    registry = ToolRegistry((Tool("broken", "Break.", {"type": "object"}, broken),))

    result = registry.execute("broken", {})

    assert result["error"] == "Tool broken failed with RuntimeError."
    assert secret not in json.dumps(result)


def test_registry_rejects_non_finite_results():
    registry = ToolRegistry(
        (
            Tool(
                "non_finite",
                "Return non-finite JSON.",
                {"type": "object"},
                lambda: {"ok": True, "value": float("nan")},
            ),
        )
    )

    result = registry.execute("non_finite", {})

    assert result == {
        "ok": False,
        "error": "Tool non_finite returned a non-serializable result.",
    }


def test_registry_redacts_sensitive_result_fields_before_model_context():
    secret = "returned-secret-value"
    registry = ToolRegistry(
        (
            Tool(
                "credential_result",
                "Return a credential-shaped result.",
                {"type": "object"},
                lambda: {
                    "ok": True,
                    "result": {
                        "access_token": secret,
                        "nested": [{"password": secret}],
                        "safe": "visible",
                    },
                },
            ),
        )
    )

    result = registry.execute("credential_result", {})

    assert secret not in json.dumps(result)
    assert result["result"]["access_token"] == REDACTED_TOOL_VALUE
    assert result["result"]["nested"][0]["password"] == REDACTED_TOOL_VALUE
    assert result["result"]["safe"] == "visible"


def test_registry_bounds_results_from_every_tool_source():
    registry = ToolRegistry(
        (
            Tool(
                "large",
                "Return large output.",
                {"type": "object"},
                lambda: {"ok": True, "content": "x" * 300_000},
            ),
        )
    )

    result = registry.execute("large", {})

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(json.dumps(result)) < MAX_REGISTRY_RESULT_CHARACTERS


def test_registry_bounds_total_registered_tools():
    registry = ToolRegistry(
        Tool(
            f"tool_{index}",
            "Bounded tool.",
            {"type": "object"},
            lambda: {"ok": True},
        )
        for index in range(MAX_REGISTERED_TOOLS)
    )

    try:
        registry.register(
            Tool(
                "one_too_many",
                "Rejected tool.",
                {"type": "object"},
                lambda: {"ok": True},
            )
        )
    except ValueError as exc:
        assert "at most" in str(exc)
    else:
        raise AssertionError("Registry accepted an unbounded tool definition")
