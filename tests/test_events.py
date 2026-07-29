import json
from pathlib import Path

from typer.testing import CliRunner

import lunar_forge.agent as agent_module
import lunar_forge.cli as cli_module
from lunar_forge.agent import run_agent, run_agent_events
from lunar_forge.cli import app
from lunar_forge.config import AppConfig, ModelConfig, ReasoningConfig
from lunar_forge.events import (
    AgentEvent,
    EventFactory,
    EventType,
    MAX_EVENT_STRING_CHARACTERS,
    REDACTED,
    SCHEMA_VERSION,
    deserialize_event,
    events_from_session_record,
    serialize_event,
)
from lunar_forge.model_clients import ModelResponse, ModelUsage, ToolCall


class FinalResponseModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": list(tools or []),
            }
        )
        return self.response


class SequenceResponseModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": list(tools or []),
            }
        )
        return self.responses.pop(0)


def _factory(**kwargs):
    return EventFactory(
        session_id="session_test",
        turn_id="turn_test",
        timestamp_factory=lambda: "2026-07-28T12:00:00Z",
        **kwargs,
    )


def test_event_serializes_to_json_and_back_with_required_fields():
    event = _factory().create(
        EventType.STATUS_UPDATED,
        {"message": "Inspecting project."},
    )

    serialized = serialize_event(event)
    record = json.loads(serialized)
    restored = deserialize_event(serialized)

    assert set(record) == {
        "schema_version",
        "event_id",
        "session_id",
        "turn_id",
        "sequence",
        "timestamp",
        "type",
        "payload",
    }
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["type"] == "status.updated"
    assert restored == event
    assert AgentEvent.from_dict(event.to_dict()) == event


def test_initial_event_type_values_are_stable():
    assert {event_type.value for event_type in EventType} >= {
        "session.started",
        "session.resumed",
        "turn.started",
        "turn.finished",
        "turn.cancelled",
        "status.updated",
        "assistant.message.delta",
        "assistant.message.completed",
        "model.call.started",
        "model.call.finished",
        "model.usage",
        "tool.started",
        "tool.finished",
        "tool.failed",
        "permission.requested",
        "permission.resolved",
        "validation.started",
        "validation.finished",
        "browser.started",
        "browser.finished",
        "checkpoint.created",
        "git.proposal",
        "git.commit.created",
        "git.commit.skipped",
        "memory.compaction.started",
        "memory.compaction.finished",
        "rollback.started",
        "rollback.finished",
        "error",
    }


def test_event_factory_sequences_events_and_supports_parent_ids():
    factory = _factory()
    parent = factory.create(EventType.TURN_STARTED, {"request": "Inspect"})
    child = factory.create(
        EventType.STATUS_UPDATED,
        {"message": "Working"},
        parent_event_id=parent.event_id,
    )

    assert parent.sequence == 1
    assert child.sequence == 2
    assert child.parent_event_id == parent.event_id
    assert child.session_id == parent.session_id
    assert child.turn_id == parent.turn_id


def test_event_payload_redacts_secrets_environment_and_private_reasoning():
    secret = "event-environment-secret-123"
    event = _factory(environment={"OPENAI_API_KEY": secret}).create(
        EventType.MODEL_USAGE,
        {
            "api_key": "sk-super-secret-value",
            "authorization": "Bearer top-secret",
            "message": f"Provider returned {secret}",
            "hidden_reasoning": "private chain of thought",
            "reasoning_effort": "high",
            "input_tokens": 10,
        },
    )
    serialized = event.to_json()

    assert secret not in serialized
    assert "sk-super-secret-value" not in serialized
    assert "private chain of thought" not in serialized
    assert event.payload["api_key"] == REDACTED
    assert event.payload["authorization"] == REDACTED
    assert event.payload["hidden_reasoning"] == REDACTED
    assert event.payload["reasoning_effort"] == "high"
    assert event.payload["input_tokens"] == 10


def test_event_payload_is_bounded_and_replaces_non_json_objects():
    class TerminalObject:
        pass

    event = _factory(environment={}).create(
        EventType.STATUS_UPDATED,
        {
            "message": "\x1b[31m" + ("x" * (MAX_EVENT_STRING_CHARACTERS + 100)),
            "terminal": TerminalObject(),
        },
    )
    serialized = event.to_json()

    assert "\x1b" not in serialized
    assert event.payload["message"].endswith("...[event value truncated]")
    assert event.payload["terminal"] == (
        "[unsupported event value: TerminalObject]"
    )
    json.loads(serialized)


def test_browser_artifact_reference_is_a_plain_serializable_record():
    events = events_from_session_record(
        _factory(environment={}),
        "tool_result",
        {
            "name": "run_browser_validation",
            "id": "browser-call",
            "result": {
                "ok": True,
                "screenshot_path": Path(
                    ".agent/artifacts/browser/page.png"
                ),
            },
        },
    )
    browser_event = next(
        event
        for event in events
        if event.type == EventType.BROWSER_FINISHED.value
    )

    assert browser_event.payload["artifact_path"] == str(
        Path(".agent/artifacts/browser/page.png")
    )
    assert isinstance(browser_event.payload["artifact_path"], str)
    assert json.loads(browser_event.to_json())["payload"] == dict(
        browser_event.payload
    )


def test_core_event_payloads_do_not_gain_textual_labels_or_styles():
    factory = EventFactory(
        session_id="session_ui_neutral",
        turn_id="turn_ui_neutral",
    )

    event = factory.create(
        EventType.ASSISTANT_MESSAGE_COMPLETED,
        {"text": "The answer is unchanged."},
    )
    serialized = event.to_json()

    assert event.payload == {"text": "The answer is unchanged."}
    assert "LunarForge:" not in serialized
    assert "You:" not in serialized
    assert "bold cyan" not in serialized
    assert "bold green" not in serialized
    assert json.loads(serialized)["payload"] == {
        "text": "The answer is unchanged."
    }


def test_run_agent_events_wraps_existing_one_shot_result(tmp_path):
    model = FinalResponseModel(ModelResponse(text="Inspection complete."))

    events = list(
        run_agent_events(
            "Explain this project. Do not edit files or run commands.",
            tmp_path,
            config=AppConfig(),
            model_client=model,
        )
    )
    types = [event.type for event in events]
    final_event = next(
        event
        for event in events
        if event.type == EventType.ASSISTANT_MESSAGE_COMPLETED.value
    )

    assert types[0] == EventType.SESSION_STARTED.value
    assert EventType.TURN_STARTED.value in types
    assert EventType.MODEL_CALL_STARTED.value in types
    assert EventType.MODEL_CALL_FINISHED.value in types
    assert EventType.MODEL_USAGE.value in types
    assert types[-1] == EventType.TURN_FINISHED.value
    assert final_event.payload["text"].startswith("Inspection complete.")
    assert "Session log:" in final_event.payload["text"]


def test_run_agent_events_adapts_tool_telemetry(tmp_path):
    (tmp_path / "README.md").write_text("LunarForge\n", encoding="utf-8")
    model = SequenceResponseModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="read-call",
                        name="read_file",
                        arguments={"path": "README.md"},
                    ),
                ),
            ),
            ModelResponse(text="README inspected."),
        )
    )

    events = list(
        run_agent_events(
            "Inspect README.md. Do not edit files or run commands.",
            tmp_path,
            config=AppConfig(),
            model_client=model,
        )
    )
    started = next(
        event
        for event in events
        if event.type == EventType.TOOL_STARTED.value
    )
    finished = next(
        event
        for event in events
        if event.type == EventType.TOOL_FINISHED.value
    )

    assert started.payload["tool_name"] == "read_file"
    assert started.payload["args_preview"] == {"path": "README.md"}
    assert finished.payload["tool_name"] == "read_file"
    assert finished.payload["ok"] is True


def test_run_agent_events_represents_approval_without_changing_decision(
    tmp_path,
):
    model = SequenceResponseModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="write-call",
                        name="write_file",
                        arguments={
                            "path": "created.txt",
                            "content": "not written",
                        },
                    ),
                ),
            ),
            ModelResponse(text="Write was denied."),
        )
    )

    events = list(
        run_agent_events(
            "Create created.txt with a short marker.",
            tmp_path,
            config=AppConfig(),
            model_client=model,
            approval_callback=lambda request: False,
        )
    )
    requested = [
        event
        for event in events
        if event.type == EventType.PERMISSION_REQUESTED.value
    ]
    resolved = [
        event
        for event in events
        if event.type == EventType.PERMISSION_RESOLVED.value
    ]

    assert len(requested) == 1
    assert len(resolved) == 1
    assert requested[0].payload["tool_name"] == "write_file"
    assert resolved[0].payload["allowed"] is False
    assert resolved[0].parent_event_id == requested[0].event_id
    assert not (tmp_path / "created.txt").exists()


def test_run_agent_keeps_quiet_one_shot_output_shape(tmp_path):
    output = run_agent(
        "Explain this project. Do not edit files or run commands.",
        tmp_path,
        config=AppConfig(),
        model_client=FinalResponseModel(
            ModelResponse(text="Read-only answer.")
        ),
    )

    assert output.startswith("Read-only answer.")
    assert "Session log:" in output
    assert "Working..." not in output
    assert "Tool:" not in output


def test_one_shot_cli_still_prints_final_answer(monkeypatch, tmp_path):
    model = FinalResponseModel(ModelResponse(text="CLI read-only answer."))
    monkeypatch.setattr(agent_module, "create_model_client", lambda config: model)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda *args, **kwargs: AppConfig(),
    )

    result = CliRunner().invoke(
        app,
        [
            "Explain this project. Do not edit files or run commands.",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.startswith("CLI read-only answer.")
    assert "Working..." not in result.stdout


def test_one_shot_cli_usage_keeps_reasoning_effort(
    monkeypatch,
    tmp_path,
):
    config = AppConfig(
        model=ModelConfig(
            reasoning=ReasoningConfig(effort="high"),
        )
    )
    model = FinalResponseModel(
        ModelResponse(
            text="Usage answer.",
            model="gpt-test",
            usage=ModelUsage(
                input_tokens=40,
                output_tokens=10,
                total_tokens=50,
                model="gpt-test",
                provider="openai",
                exact=True,
            ),
        )
    )
    monkeypatch.setattr(agent_module, "create_model_client", lambda config: model)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda *args, **kwargs: config,
    )

    result = CliRunner().invoke(
        app,
        [
            "--show-usage",
            "--reasoning-effort",
            "high",
            "Explain this project. Do not edit files or run commands.",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Model usage:" in result.stdout
    assert "- Reasoning effort: high" in result.stdout
    assert "- Total tokens: 50" in result.stdout
