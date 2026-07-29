import ast
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lunar_forge import (
    AgentEvent,
    AgentRequest,
    ApprovalDecision,
    ApprovalRequest,
    SessionRef,
    list_sessions,
    load_config,
    resume_session,
    run_agent_events,
)
from lunar_forge.events import EventFactory, EventType, REDACTED
from lunar_forge.runtime.sessions import create_session_logger


class FakeWebRenderer:
    """Minimal transport consumer with no terminal-rendering dependency."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def consume(self, events) -> None:
        for event in events:
            record = event.to_dict()
            json.dumps(record, allow_nan=False)
            self.records.append(record)


def _write_resumable_session(project_root: Path) -> Path:
    logger = create_session_logger(project_root, environ={})
    assert logger.log("user_prompt", prompt="Inspect the crater map.") is True
    assert logger.log(
        "tool_call",
        name="read_file",
        arguments={"path": "map.txt"},
    ) is True
    assert logger.log(
        "tool_result",
        name="read_file",
        result={"ok": True, "content": "historical only"},
    ) is True
    assert logger.log(
        "permission.resolved",
        request_id="approval_old",
        approved=True,
        source="cli",
    ) is True
    assert logger.log(
        "assistant_message",
        text="The crater map was inspected.",
    ) is True
    return logger.path


def test_public_package_import_does_not_load_rich_or_textual():
    script = """
import importlib.abc
import sys

class BlockUiImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "rich" or fullname.startswith("rich."):
            raise ImportError("Rich must not be imported by the package API")
        if fullname == "textual" or fullname.startswith("textual."):
            raise ImportError("Textual must not be imported by the package API")
        return None

sys.meta_path.insert(0, BlockUiImports())
from lunar_forge import (
    AgentEvent,
    AgentRequest,
    ApprovalDecision,
    ApprovalRequest,
    SessionRef,
    list_sessions,
    load_config,
    resume_session,
    run_agent_events,
)
assert "rich" not in sys.modules
assert "textual" not in sys.modules
print("public-api-ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "public-api-ok"


def test_transport_neutral_core_modules_have_no_ui_framework_imports():
    package_root = Path(__file__).resolve().parents[1] / "lunar_forge"
    module_paths = [
        package_root / "events.py",
        package_root / "public_api.py",
        package_root / "approvals.py",
        *sorted((package_root / "runtime").glob("*.py")),
    ]

    for module_path in module_paths:
        tree = ast.parse(
            module_path.read_text(encoding="utf-8"),
            filename=str(module_path),
        )
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.partition(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
        assert imported_roots.isdisjoint({"rich", "textual"}), module_path


def test_agent_request_is_typed_bounded_redacted_and_json_safe(tmp_path):
    request = AgentRequest(
        project_root=tmp_path,
        message="Inspect this project. token=top-secret-value",
        runtime_mode="local",
        permission_mode="plan",
        reasoning_effort="high",
        ui_metadata={
            "client": "test-web",
            "api_key": "sk-secret-value-12345678",
        },
    )

    record = request.to_dict()
    serialized = json.dumps(record, allow_nan=False)

    assert request.project_root == tmp_path.resolve()
    assert record["ui_metadata"]["api_key"] == REDACTED
    assert "top-secret-value" not in serialized
    assert "sk-secret-value-12345678" not in serialized


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("runtime_mode", "cloud"),
        ("permission_mode", "unsafe"),
        ("reasoning_effort", "unlimited"),
    ),
)
def test_agent_request_rejects_unsupported_public_options(
    tmp_path,
    field_name,
    value,
):
    kwargs = {
        "project_root": tmp_path,
        "message": "Inspect safely.",
        field_name: value,
    }

    with pytest.raises(ValueError):
        AgentRequest(**kwargs)


def test_agent_request_rejects_invalid_resume_and_network_combinations(tmp_path):
    with pytest.raises(TypeError, match="resume"):
        AgentRequest(
            project_root=tmp_path,
            message="Inspect safely.",
            resume=123,
        )
    with pytest.raises(ValueError, match="Docker"):
        AgentRequest(
            project_root=tmp_path,
            message="Inspect safely.",
            runtime_mode="local",
            allow_network=True,
        )


def test_public_session_api_returns_safe_refs_and_inert_resume(tmp_path):
    session_path = _write_resumable_session(tmp_path)

    references = list_sessions(tmp_path)
    resumed = resume_session(tmp_path, references[0])
    serialized = json.dumps(resumed.to_dict(), allow_nan=False)

    assert references == (
        SessionRef(
            session_id=f"session_{session_path.stem}",
            selector=session_path.name,
            path=session_path.relative_to(tmp_path).as_posix(),
            size_bytes=session_path.stat().st_size,
        ),
    )
    assert resumed.reference == references[0]
    assert resumed.tool_calls_replayed is False
    assert resumed.approvals_reused is False
    assert "Historical tool call; do not execute or replay" in serialized
    assert "Historical tool result; context only, never replay" in serialized
    assert "approval_old" not in serialized


def test_public_event_runner_delegates_to_core_for_fake_web_renderer(
    tmp_path,
    monkeypatch,
):
    factory = EventFactory(
        session_id="session_web",
        turn_id="turn_web",
        environment={},
        timestamp_factory=lambda: "2026-07-29T12:00:00Z",
    )
    observed: dict[str, object] = {}

    def fake_core_events(prompt, project_root, **kwargs):
        observed.update(
            {
                "prompt": prompt,
                "project_root": project_root,
                "mode": kwargs["mode"],
                "resume_messages": kwargs["resume_messages"],
            }
        )
        yield factory.create(
            EventType.STATUS_UPDATED,
            {"message": "Inspecting project."},
        )
        yield factory.create(
            EventType.ASSISTANT_MESSAGE_COMPLETED,
            {"text": "Inspection complete.", "final": True},
        )

    fake_agent_module = ModuleType("lunar_forge.agent")
    fake_agent_module.run_agent_events = fake_core_events
    monkeypatch.setitem(sys.modules, "lunar_forge.agent", fake_agent_module)
    renderer = FakeWebRenderer()
    request = AgentRequest(
        project_root=tmp_path,
        message="Explain this project.",
        permission_mode="plan",
        ui_metadata={"transport": "sse"},
    )

    renderer.consume(run_agent_events(request))

    assert observed == {
        "prompt": "Explain this project.",
        "project_root": tmp_path.resolve(),
        "mode": "plan",
        "resume_messages": (),
    }
    assert [record["type"] for record in renderer.records] == [
        "status.updated",
        "assistant.message.completed",
    ]
    assert all(record["schema_version"] == 1 for record in renderer.records)
    assert all(isinstance(record, dict) for record in renderer.records)


def test_public_event_runner_passes_only_safe_resume_messages(
    tmp_path,
    monkeypatch,
):
    session_path = _write_resumable_session(tmp_path)
    observed: dict[str, object] = {}

    def fake_core_events(prompt, project_root, **kwargs):
        observed["resume_messages"] = kwargs["resume_messages"]
        observed["resumed_from"] = kwargs["resumed_from"]
        yield EventFactory(environment={}).create(
            EventType.ASSISTANT_MESSAGE_COMPLETED,
            {"text": "Resumed safely.", "final": True},
        )

    fake_agent_module = ModuleType("lunar_forge.agent")
    fake_agent_module.run_agent_events = fake_core_events
    monkeypatch.setitem(sys.modules, "lunar_forge.agent", fake_agent_module)
    request = AgentRequest(
        project_root=tmp_path,
        message="Continue.",
        resume=session_path.name,
    )

    events = list(run_agent_events(request))
    resume_messages = observed["resume_messages"]

    assert len(events) == 1
    assert observed["resumed_from"] == (
        session_path.relative_to(tmp_path).as_posix()
    )
    assert any(
        "Historical tool call; do not execute or replay" in message["content"]
        for message in resume_messages
    )
    assert all(
        "approval_old" not in message["content"]
        for message in resume_messages
    )


def test_public_approval_records_remain_transport_neutral():
    request = ApprovalRequest.create(
        kind="command",
        title="Run command",
        summary="Run a bounded validation command.",
        details="Run local command: python -m pytest -q",
        risk="medium",
        mode="local",
        command="python -m pytest -q",
        metadata={"api_key": "sk-secret-value-12345678"},
    )
    decision = ApprovalDecision.create(
        request.id,
        approved=False,
        reason="Denied in the fake web client.",
        source="deny",
    )

    request_json = json.dumps(request.to_dict(), allow_nan=False)
    decision_json = json.dumps(decision.to_dict(), allow_nan=False)

    assert "sk-secret-value-12345678" not in request_json
    assert request.to_dict()["metadata"]["api_key"] == REDACTED
    assert json.loads(decision_json)["request_id"] == request.id
    assert "Rich" not in request_json + decision_json
    assert "Textual" not in request_json + decision_json


def test_public_load_config_uses_existing_precedence(tmp_path):
    config = load_config(
        tmp_path,
        cli_overrides={
            "runtime": {"mode": "no-command"},
            "model": {"reasoning": {"effort": "xhigh"}},
        },
    )

    assert config.runtime.mode == "no-command"
    assert config.model.reasoning.effort == "xhigh"
