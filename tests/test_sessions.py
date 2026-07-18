import json
from pathlib import Path

import pytest

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.runtime.sessions import (
    REDACTED,
    SESSION_ERROR,
    SessionLogger,
    create_session_logger,
    list_session_files,
)


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools=None):
        return self.responses.pop(0)


class FailingModel:
    def __init__(self, message):
        self.message = message

    def complete(self, messages, tools=None):
        raise RuntimeError(self.message)


def _session_file(project_root):
    files = list((project_root / ".agent" / "sessions").glob("*.jsonl"))
    assert len(files) == 1
    return files[0]


def _events(session_file):
    lines = session_file.read_text(encoding="utf-8").splitlines()
    assert lines
    return [json.loads(line) for line in lines]


def test_session_logger_creates_project_local_jsonl_events(tmp_path):
    logger = create_session_logger(tmp_path, environ={})

    assert logger.relative_path.startswith(".agent/sessions/")
    assert logger.path.parent == tmp_path / ".agent" / "sessions"
    assert logger.path.suffix == ".jsonl"
    assert logger.log("user_prompt", prompt="Inspect this project") is True
    assert logger.log("assistant_message", text="Done") is True

    events = _events(logger.path)
    assert [event["event"] for event in events] == [
        "user_prompt",
        "assistant_message",
    ]
    assert all(set(event) == {"timestamp", "event", "data"} for event in events)


def test_session_logger_redacts_api_keys_and_environment_values(tmp_path):
    environment_secret = "environment-secret-value-123"
    api_key = "sk-session-api-key-123456789"
    logger = create_session_logger(
        tmp_path,
        environ={"OPENAI_API_KEY": environment_secret},
    )

    logger.log(
        "tool_call",
        api_key=api_key,
        arguments={
            "content": f"values: {environment_secret} and {api_key}",
            "authorization": f"Bearer {api_key}",
        },
    )

    raw_log = logger.path.read_text(encoding="utf-8")
    assert environment_secret not in raw_log
    assert api_key not in raw_log
    assert REDACTED in raw_log
    json.loads(raw_log)


def test_session_logging_failure_is_generic_and_cannot_escape_root(
    monkeypatch,
    tmp_path,
):
    secret = "logging-error-secret-123"
    logger = create_session_logger(tmp_path, environ={"SECRET": secret})

    def failing_open(self, *args, **kwargs):
        raise OSError(f"could not write with {secret}")

    with monkeypatch.context() as context:
        context.setattr(Path, "open", failing_open)
        assert logger.log("error", message=secret) is False

    assert logger.last_error == SESSION_ERROR
    assert secret not in logger.last_error

    outside_path = tmp_path.parent / "outside-session.jsonl"
    unsafe_logger = SessionLogger(
        project_root=tmp_path.resolve(),
        path=outside_path.resolve(),
        _environment_names=frozenset(),
        _environment_values=(),
    )
    assert unsafe_logger.log("user_prompt", prompt="blocked") is False
    assert not outside_path.exists()


def test_agent_logs_messages_tools_results_and_permission_denials(
    monkeypatch,
    tmp_path,
):
    secret = "agent-environment-secret-123"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="call_write",
                        name="write_file",
                        arguments={
                            "path": "secret.txt",
                            "content": secret,
                        },
                    ),
                ),
            ),
            ModelResponse(text="No file was written."),
        )
    )
    agent = CodeAgent(
        AppConfig(),
        model_client=model,
        approval_callback=lambda request: False,
    )

    output = agent.run(f"Write this value: {secret}", tmp_path)

    session_file = _session_file(tmp_path)
    raw_log = session_file.read_text(encoding="utf-8")
    events = _events(session_file)
    event_names = [event["event"] for event in events]

    assert secret not in raw_log
    assert "user_prompt" in event_names
    assert event_names.count("assistant_message") == 2
    assert "tool_call" in event_names
    assert "tool_result" in event_names
    assert "permission_denial" in event_names
    assert not (tmp_path / "secret.txt").exists()
    assert f"Session log: {session_file.relative_to(tmp_path).as_posix()}" in output


def test_agent_error_event_is_redacted(monkeypatch, tmp_path):
    secret = "provider-environment-secret-123"
    monkeypatch.setenv("PROVIDER_SECRET", secret)
    agent = CodeAgent(
        AppConfig(),
        model_client=FailingModel(f"Provider failed with {secret}"),
    )

    with pytest.raises(RuntimeError):
        agent.run("Trigger provider", tmp_path)

    session_file = _session_file(tmp_path)
    raw_log = session_file.read_text(encoding="utf-8")
    events = _events(session_file)

    assert secret not in raw_log
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["message"] == f"Provider failed with {REDACTED}"


def test_plan_mode_does_not_create_runtime_files(tmp_path):
    agent = CodeAgent(
        AppConfig(),
        model_client=SequenceModel((ModelResponse(text="Plan ready."),)),
    )

    output = agent.run("Plan this change", tmp_path, mode="plan")

    assert not (tmp_path / ".agent").exists()
    assert output.endswith("Session log: disabled in plan mode")


def test_session_listing_returns_metadata_without_reading_contents(tmp_path):
    sessions_directory = tmp_path / ".agent" / "sessions"
    sessions_directory.mkdir(parents=True)
    secret = "sk-session-listing-secret-123456"
    older = sessions_directory / "20260101T000000000000Z-11111111.jsonl"
    newer = sessions_directory / "20260201T000000000000Z-22222222.jsonl"
    older.write_text(f'{{"secret":"{secret}"}}\n', encoding="utf-8")
    newer.write_text("{}\n", encoding="utf-8")

    result = list_session_files(tmp_path)

    assert result["ok"] is True
    assert [item["name"] for item in result["sessions"]] == [
        newer.name,
        older.name,
    ]
    assert secret not in json.dumps(result)
