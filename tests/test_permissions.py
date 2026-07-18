from lunar_forge.permissions import (
    PermissionDecision,
    PermissionLevel,
    PermissionManager,
    PermissionRequest,
    requires_approval,
)
from lunar_forge.tools.registry import create_tool_registry


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
