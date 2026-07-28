import json
from pathlib import Path

from lunar_forge.agent import run_agent_events
from lunar_forge.approvals import (
    ApprovalDecision,
    ApprovalRequest,
    AutoApprovalProvider,
    CliApprovalProvider,
    DenyApprovalProvider,
)
from lunar_forge.config import AppConfig
from lunar_forge.events import EventType, MAX_EVENT_PAYLOAD_CHARACTERS
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.permissions import PermissionLevel, PermissionManager
from lunar_forge.runtime.git import create_git_commit


class _RecordingProvider:
    def __init__(self, decision_provider=None):
        self.requests = []
        self.decision_provider = decision_provider or DenyApprovalProvider()

    def request_approval(self, request):
        self.requests.append(request)
        return self.decision_provider.request_approval(request)


class _SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None):
        return self.responses.pop(0)


def _request(**overrides):
    values = {
        "kind": "write",
        "title": "Change project files",
        "summary": "Write file: example.txt",
        "details": "Write file: example.txt.",
        "risk": "low",
        "mode": "default",
        "tool_name": "write_file",
        "file_path": "example.txt",
        "metadata": {"permission_mode": "yes"},
    }
    values.update(overrides)
    return ApprovalRequest.create(**values)


def test_cli_provider_preserves_full_then_short_local_prompts():
    prompts = []
    provider = CliApprovalProvider(
        input_func=lambda prompt: prompts.append(prompt) or "y"
    )
    manager = PermissionManager(
        approval_provider=provider,
        runtime_mode="local",
    )

    first = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "python --version"},
    )
    second = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "git --version"},
    )

    assert first.allowed is True
    assert second.allowed is True
    assert "not OS-level isolation" in prompts[0]
    assert prompts[1] == "Run local command: git --version. Allow? [y/N] "


def test_cli_provider_preserves_docker_prompt_wording():
    prompts = []
    manager = PermissionManager(
        approval_provider=CliApprovalProvider(
            input_func=lambda prompt: prompts.append(prompt) or "n"
        ),
        runtime_mode="docker",
    )

    decision = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "pwd"},
    )

    assert decision.allowed is False
    assert prompts == [
        (
            "Run Docker command: pwd\n"
            "This runs inside lunar-forge-sandbox with the project mounted at "
            "/workspace.\n"
            "Allow? [y/N] "
        )
    ]


def test_auto_provider_approves_safe_write_but_not_dependency_install():
    provider = AutoApprovalProvider()

    write_decision = provider.request_approval(_request())
    install_decision = provider.request_approval(
        _request(
            kind="command",
            title="Run command",
            summary="Run local command: python -m pip install example",
            details="Run local command: python -m pip install example.",
            risk="high",
            mode="local",
            command="python -m pip install example",
            tool_name="run_command",
            file_path=None,
            metadata={"dependency_install": True},
        )
    )

    assert write_decision.approved is True
    assert write_decision.source == "auto"
    assert install_decision.approved is False
    assert install_decision.source == "auto"


def test_yes_mode_delegates_dependency_install_to_cli_provider():
    prompts = []
    manager = PermissionManager(
        mode="yes",
        approval_provider=CliApprovalProvider(
            input_func=lambda prompt: prompts.append(prompt) or "n"
        ),
        runtime_mode="local",
    )

    decision = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "python -m pip install example"},
    )

    assert decision.allowed is False
    assert len(prompts) == 1
    assert "not OS-level isolation" in prompts[0]


def test_deny_provider_denies_requested_operation():
    request = _request()
    decision = DenyApprovalProvider().request_approval(request)

    assert decision.request_id == request.id
    assert decision.approved is False
    assert decision.source == "deny"


def test_approval_transport_payloads_are_plain_bounded_and_redacted():
    secret = "sk-approval-transport-secret-12345678"
    request = _request(
        command=f"tool --token {secret}",
        metadata={
            "artifact": Path(".agent/artifacts/browser/page.png"),
            "api_key": secret,
            "widget": object(),
        },
    )
    decision = ApprovalDecision.create(
        request.id,
        approved=False,
        reason=f"Denied because token={secret}",
        source="deny",
    )

    request_payload = request.to_dict()
    decision_payload = decision.to_dict()
    serialized = json.dumps(
        {
            "request": request_payload,
            "decision": decision_payload,
        }
    )

    assert secret not in serialized
    assert request_payload["metadata"]["artifact"] == str(
        Path(".agent/artifacts/browser/page.png")
    )
    assert request_payload["metadata"]["api_key"] == "[REDACTED]"
    assert request_payload["metadata"]["widget"] == (
        "[unsupported event value: object]"
    )
    assert decision_payload["approved"] is False
    assert len(json.dumps(request_payload)) <= MAX_EVENT_PAYLOAD_CHARACTERS


def test_dangerous_command_is_blocked_before_provider():
    provider = _RecordingProvider()
    manager = PermissionManager(approval_provider=provider)

    decision = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "rm -rf build"},
    )

    assert decision.allowed is False
    assert "prohibited pattern" in decision.reason
    assert provider.requests == []


def test_emitted_approval_payloads_are_bounded_and_redacted():
    secret = "sk-approval-secret-87654321"
    emitted = []
    manager = PermissionManager(
        approval_provider=DenyApprovalProvider(),
        approval_event_callback=lambda event, payload: emitted.append(
            (event, payload)
        ),
    )

    manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {
            "command": (
                f"tool --token {secret} "
                f"{'x' * (MAX_EVENT_PAYLOAD_CHARACTERS + 1_000)}"
            )
        },
    )

    assert [event for event, _ in emitted] == [
        "permission.requested",
        "permission.resolved",
    ]
    serialized = json.dumps(emitted)
    assert secret not in serialized
    assert len(json.dumps(emitted[0][1])) <= MAX_EVENT_PAYLOAD_CHARACTERS


def test_plan_mode_denies_writes_and_commands_without_provider_request():
    provider = _RecordingProvider()
    manager = PermissionManager(mode="plan", approval_provider=provider)

    write = manager.authorize(
        PermissionLevel.WRITE,
        "write_file",
        {"path": "example.txt", "content": "example"},
    )
    command = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "python --version"},
    )

    assert write.allowed is False
    assert command.allowed is False
    assert provider.requests == []


def test_no_command_mode_denies_execution_validation_and_git_without_request(
    tmp_path,
):
    provider = _RecordingProvider()
    manager = PermissionManager(
        mode="no-command",
        approval_provider=provider,
    )

    command = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_command",
        {"command": "python --version"},
    )
    validation = manager.authorize(
        PermissionLevel.EXECUTE,
        "run_validation",
        {},
    )
    commit = create_git_commit(
        tmp_path,
        "Blocked commit",
        mode="no-command",
        approval_provider=provider,
    )

    assert command.allowed is False
    assert validation.allowed is False
    assert commit["approval_requested"] is False
    assert commit["result_code"] == "no_command"
    assert provider.requests == []


def test_approval_events_and_session_records_are_redacted_and_correlated(
    tmp_path,
):
    secret = "sk-approval-secret-12345678"
    model = _SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="write-call",
                        name="write_file",
                        arguments={
                            "path": "created.txt",
                            "content": secret,
                        },
                    ),
                ),
            ),
            ModelResponse(text="Write was denied."),
        )
    )

    events = list(
        run_agent_events(
            "Create created.txt.",
            tmp_path,
            config=AppConfig(),
            model_client=model,
            approval_provider=DenyApprovalProvider(),
        )
    )
    requested = next(
        event
        for event in events
        if event.type == EventType.PERMISSION_REQUESTED.value
    )
    resolved = next(
        event
        for event in events
        if event.type == EventType.PERMISSION_RESOLVED.value
    )

    assert requested.payload["kind"] == "write"
    assert requested.payload["file_path"] == "created.txt"
    assert resolved.payload["request_id"] == requested.payload["request_id"]
    assert resolved.payload["approved"] is False
    assert resolved.parent_event_id == requested.event_id
    assert secret not in json.dumps(requested.to_dict())

    session_files = list((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    assert len(session_files) == 1
    serialized = session_files[0].read_text(encoding="utf-8")
    records = [
        json.loads(line)
        for line in serialized.splitlines()
        if line.strip()
    ]
    event_names = [record["event"] for record in records]
    assert "permission.requested" in event_names
    assert "permission.resolved" in event_names
    assert secret not in serialized
