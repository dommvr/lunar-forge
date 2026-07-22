import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.runtime.sessions import (
    MAX_LOG_COLLECTION_ITEMS,
    MAX_LOG_STRING_CHARACTERS,
    MAX_SESSION_LIST_ENTRIES,
    REDACTED,
    SESSION_ERROR,
    LoadedSession,
    SessionLogger,
    create_session_logger,
    format_session_summary,
    load_session,
    list_session_files,
    summarize_session,
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


class CapturingModel:
    def __init__(self, response):
        self.response = response
        self.messages = None

    def complete(self, messages, tools=None):
        self.messages = list(messages)
        return self.response


def _session_file(project_root):
    files = list((project_root / ".agent" / "sessions").glob("*.jsonl"))
    assert len(files) == 1
    return files[0]


def _events(session_file):
    lines = session_file.read_text(encoding="utf-8").splitlines()
    assert lines
    return [json.loads(line) for line in lines]


def _write_previous_session(project_root, events, name="previous-session.jsonl"):
    sessions_directory = project_root / ".agent" / "sessions"
    sessions_directory.mkdir(parents=True, exist_ok=True)
    session_file = sessions_directory / name
    session_file.write_text(
        "".join(f"{json.dumps(event)}\n" for event in events),
        encoding="utf-8",
    )
    return session_file


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


def test_parallel_session_events_remain_atomic_and_redacted(tmp_path):
    secret = "parallel-session-secret-123"
    logger = create_session_logger(tmp_path, environ={"PARALLEL_SECRET": secret})

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(
            executor.map(
                lambda index: logger.log(
                    "subagent_completed",
                    role=f"reader-{index}",
                    message=f"completed with {secret}",
                ),
                range(32),
            )
        )

    raw_log = logger.path.read_text(encoding="utf-8")
    events = _events(logger.path)
    assert all(results)
    assert len(events) == 32
    assert secret not in raw_log
    assert all(
        event["data"]["message"] == f"completed with {REDACTED}"
        for event in events
    )


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


def test_session_events_and_listing_are_bounded(tmp_path):
    logger = create_session_logger(tmp_path, environ={})
    oversized_text = "x" * (MAX_LOG_STRING_CHARACTERS + 100)
    oversized_items = list(range(MAX_LOG_COLLECTION_ITEMS + 5))

    assert logger.log(
        "assistant_message",
        text=oversized_text,
        items=oversized_items,
    )
    event = _events(logger.path)[0]

    assert len(event["data"]["text"]) == MAX_LOG_STRING_CHARACTERS
    assert event["data"]["text"].endswith("[session value truncated]")
    assert len(event["data"]["items"]) == MAX_LOG_COLLECTION_ITEMS + 1
    assert event["data"]["items"][-1] == "[session collection truncated]"

    sessions_directory = logger.path.parent
    for index in range(MAX_SESSION_LIST_ENTRIES):
        (sessions_directory / f"extra-{index:04d}.jsonl").touch()
    result = list_session_files(tmp_path)

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["sessions"]) == MAX_SESSION_LIST_ENTRIES


def test_load_session_redacts_and_reconstructs_inert_history(tmp_path):
    environment_secret = "resume-environment-secret-123"
    api_key = "sk-resume-api-key-123456789"
    filename_secret = "sk-resume-filename-secret-123456789"
    session_file = _write_previous_session(
        tmp_path,
        [
            {
                "timestamp": "2026-07-20T10:00:00Z",
                "event": "user_prompt",
                "data": {"prompt": f"Continue with api_key={api_key}"},
            },
            {
                "timestamp": "2026-07-20T10:00:01Z",
                "event": "assistant_message",
                "data": {"text": "I will inspect it.", "tool_call_count": 1},
            },
            {
                "timestamp": "2026-07-20T10:00:02Z",
                "event": "tool_call",
                "data": {
                    "id": "call_1",
                    "name": "read_file",
                    "arguments": {"path": "README.md"},
                },
            },
            {
                "timestamp": "2026-07-20T10:00:03Z",
                "event": "tool_result",
                "data": {
                    "id": "call_1",
                    "name": "read_file",
                    "result": {
                        "ok": True,
                        "content": f"value={environment_secret}",
                    },
                },
            },
        ],
        name=f"{filename_secret}.jsonl",
    )

    loaded = load_session(
        tmp_path,
        session_file.name,
        environ={"PROVIDER_SECRET": environment_secret},
    )
    serialized = json.dumps(
        {"events": loaded.events, "messages": loaded.messages}
    )

    assert isinstance(loaded, LoadedSession)
    assert loaded.relative_path == session_file.relative_to(tmp_path).as_posix()
    assert api_key not in serialized
    assert environment_secret not in serialized
    assert REDACTED in serialized
    assert all(message["role"] != "tool" for message in loaded.messages)
    assert all("tool_calls" not in message for message in loaded.messages)
    assert any(
        "Historical tool result; context only, never replay" in message["content"]
        for message in loaded.messages
    )

    summary = summarize_session(loaded)
    formatted = format_session_summary(loaded)
    assert summary["tool_calls"] == 1
    assert summary["tool_results"] == 1
    assert api_key not in formatted
    assert environment_secret not in formatted
    assert filename_secret not in formatted
    assert "never replayed" in formatted


def test_load_session_blocks_paths_outside_session_directory(tmp_path):
    sessions_directory = tmp_path / ".agent" / "sessions"
    sessions_directory.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="directly inside"):
        load_session(tmp_path, outside)

    with pytest.raises(PermissionError, match="outside the project root"):
        load_session(tmp_path, tmp_path.parent / "outside.jsonl")


def test_resumed_agent_uses_history_without_replaying_and_logs_reference(tmp_path):
    previous_file = _write_previous_session(
        tmp_path,
        [
            {
                "timestamp": "2026-07-20T10:00:00Z",
                "event": "user_prompt",
                "data": {"prompt": "Inspect README.md"},
            },
            {
                "timestamp": "2026-07-20T10:00:01Z",
                "event": "tool_result",
                "data": {
                    "name": "read_file",
                    "result": {"ok": True, "content": "historical content"},
                },
            },
        ],
    )
    previous = load_session(tmp_path, previous_file.name, environ={})
    model = CapturingModel(ModelResponse(text="Continuation complete."))
    agent = CodeAgent(AppConfig(), model_client=model)

    output = agent.run(
        "Continue the review",
        tmp_path,
        resume_messages=previous.messages,
        resumed_from=previous.relative_path,
    )

    assert model.messages is not None
    assert all(message.get("role") != "tool" for message in model.messages)
    assert all("tool_calls" not in message for message in model.messages)
    assert any(
        "never execute, replay" in str(message.get("content"))
        for message in model.messages
    )
    assert any(
        "Historical tool result; context only" in str(message.get("content"))
        for message in model.messages
    )

    session_files = list(previous_file.parent.glob("*.jsonl"))
    assert len(session_files) == 2
    new_session = next(path for path in session_files if path != previous_file)
    new_events = _events(new_session)
    assert new_events[0]["event"] == "session_resumed"
    assert new_events[0]["data"]["source_session"] == previous.relative_path
    assert new_events[1]["event"] == "user_prompt"
    assert new_session.relative_to(tmp_path).as_posix() in output


def test_resumed_plan_mode_does_not_create_a_new_session(tmp_path):
    previous_file = _write_previous_session(
        tmp_path,
        [
            {
                "timestamp": "2026-07-20T10:00:00Z",
                "event": "user_prompt",
                "data": {"prompt": "Plan the change"},
            }
        ],
    )
    previous = load_session(tmp_path, previous_file.name, environ={})
    before = set(previous_file.parent.glob("*.jsonl"))
    agent = CodeAgent(
        AppConfig(),
        model_client=SequenceModel((ModelResponse(text="Updated plan."),)),
    )

    output = agent.run(
        "Continue planning",
        tmp_path,
        mode="plan",
        resume_messages=previous.messages,
        resumed_from=previous.relative_path,
    )

    assert set(previous_file.parent.glob("*.jsonl")) == before
    assert output.endswith("Session log: disabled in plan mode")
