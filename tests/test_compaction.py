import json

from lunar_forge.approvals import DenyApprovalProvider
from lunar_forge.config import (
    AppConfig,
    ChatUIConfig,
    PermissionConfig,
    UIConfig,
)
from lunar_forge.events import EventFactory, EventType
from lunar_forge.model_clients import ModelResponse
from lunar_forge.runtime.compaction import ConversationCompactor
from lunar_forge.runtime.conversation import ConversationMemory
from lunar_forge.runtime.sessions import create_session_logger, load_session
from lunar_forge.ui.textual_widgets import TextualChatController


class RecordingModel:
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
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _config(*, compact_at=100, compact_to=25):
    return AppConfig(
        ui=UIConfig(
            chat=ChatUIConfig(
                compact_at_tokens=compact_at,
                compact_to_tokens=compact_to,
            )
        )
    )


def _long_memory():
    return ConversationMemory(
        (
            {
                "role": "user",
                "content": "Initial task: update app.py. " + "a" * 300,
            },
            {
                "role": "assistant",
                "content": "Started the task. TODO: validate the change.",
            },
            {
                "role": "user",
                "content": "Do not change public APIs. " + "b" * 300,
            },
            {
                "role": "assistant",
                "content": "Updated app.py; validation remains open.",
            },
            {
                "role": "user",
                "content": "Keep the existing CLI behavior.",
            },
            {
                "role": "assistant",
                "content": "The CLI behavior remains unchanged.",
            },
            {
                "role": "user",
                "content": "Next step: finish the documentation.",
            },
            {
                "role": "assistant",
                "content": "Documentation is still pending.",
            },
        )
    )


def _state_events():
    factory = EventFactory(session_id="session_source", turn_id="turn_source")
    return (
        factory.create(
            EventType.TOOL_FINISHED,
            {
                "tool_name": "write_file",
                "ok": True,
                "result": {
                    "ok": True,
                    "path": "app.py",
                    "changed_files": ["app.py"],
                    "stdout": "raw output " * 20_000,
                },
            },
        ),
        factory.create(
            EventType.VALIDATION_FINISHED,
            {
                "tool_name": "run_validation",
                "ok": False,
                "error": "One test remains failing.",
            },
        ),
        factory.create(
            EventType.PERMISSION_RESOLVED,
            {
                "request_id": "approval_current",
                "approved": True,
                "source": "textual",
                "reason": "Approved for this historical operation.",
            },
        ),
        factory.create(
            EventType.ERROR,
            {
                "source": "validation",
                "message": "A follow-up validation error remains unresolved.",
            },
        ),
    )


def test_compaction_triggers_above_threshold_and_persists_safe_state(
    monkeypatch,
    tmp_path,
):
    secret = "sk-compaction-secret-123456789"
    hidden_reasoning = "private scratchpad text"
    monkeypatch.setenv("COMPACTION_API_TOKEN", secret)
    model = RecordingModel(
        (
            ModelResponse(
                text=json.dumps(
                    {
                        "summary": (
                            f"Continue app.py work. api_key={secret}\n"
                            f"hidden_reasoning: {hidden_reasoning}"
                        ),
                        "facts": {
                            "open_items": ["Finish documentation."],
                            "hidden_reasoning": hidden_reasoning,
                        },
                    }
                )
            ),
        )
    )
    memory = _long_memory()
    compactor = ConversationCompactor(
        tmp_path,
        "session_compaction",
        _config(),
        model,
    )

    result = compactor.maybe_compact(
        memory,
        incoming_user_text="Continue and preserve the validation failure.",
        instruction_context=(
            "Project instructions from AGENTS.md (scope: .):\n"
            "Preserve CLI compatibility."
        ),
        events=_state_events(),
        source_session_event_count=12,
    )

    assert result.triggered is True
    assert result.compacted is True
    assert result.pressure.total_tokens >= 100
    assert result.summary_path == (
        ".agent/summaries/session_compaction.summary.json"
    )
    summary_path = tmp_path / result.summary_path
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    serialized = json.dumps(summary)
    assert summary["schema_version"] == 1
    assert summary["session_id"] == "session_compaction"
    assert summary["source_event_count"] == 12
    assert "app.py" in summary["facts"]["changed_files"]
    assert summary["facts"]["validation"][0]["ok"] is False
    assert "Finish documentation." in summary["facts"]["open_items"]
    assert summary["facts"]["current_task_goal"] == (
        "Continue and preserve the validation failure."
    )
    assert summary["facts"]["project_root"] == str(tmp_path.resolve())
    assert summary["facts"]["runtime_mode"] == "local"
    assert summary["facts"]["permission_mode"] == "default"
    assert summary["facts"]["model"] == "openai/gpt-5.5"
    assert summary["facts"]["approval_decisions"][0]["approved"] is True
    assert summary["facts"]["unresolved_errors"] == [
        "A follow-up validation error remains unresolved."
    ]
    assert summary["facts"]["important_tool_results"][0]["tool_name"] == (
        "write_file"
    )
    assert "api_key=[REDACTED]" in summary["summary"]
    assert secret not in serialized
    assert hidden_reasoning not in serialized
    assert "hidden_reasoning" not in serialized
    assert "raw output" not in serialized
    assert summary["facts"]["reasoning_effort"] == "medium"
    assert summary["facts"]["instruction_notes"] == [
        "Reload current instructions from AGENTS.md before acting."
    ]
    assert len(memory.messages) == 5
    assert memory.messages[0]["content"].startswith(
        "[Working-memory compacted summary]"
    )
    assert "Initial task" not in json.dumps(memory.messages)
    assert memory.messages[-1]["content"] == "Documentation is still pending."
    assert model.calls[0]["tools"] == []


def test_compaction_does_not_trigger_below_threshold(tmp_path):
    model = RecordingModel((ModelResponse(text="unused"),))
    memory = ConversationMemory(
        (
            {"role": "user", "content": "Short question."},
            {"role": "assistant", "content": "Short answer."},
            {"role": "user", "content": "Another question."},
            {"role": "assistant", "content": "Another answer."},
            {"role": "user", "content": "Third question."},
            {"role": "assistant", "content": "Third answer."},
        )
    )
    compactor = ConversationCompactor(
        tmp_path,
        "session_below",
        _config(compact_at=10_000, compact_to=1_000),
        model,
    )

    result = compactor.maybe_compact(
        memory,
        incoming_user_text="Continue.",
        instruction_context="No project instructions.",
        events=(),
        source_session_event_count=0,
    )

    assert result.triggered is False
    assert result.compacted is False
    assert model.calls == []
    assert not (tmp_path / ".agent" / "summaries").exists()


def test_pending_operation_defers_compaction_without_changing_memory(tmp_path):
    model = RecordingModel((ModelResponse(text="unused"),))
    memory = _long_memory()
    before = memory.messages
    compactor = ConversationCompactor(
        tmp_path,
        "session_pending",
        _config(),
        model,
    )

    result = compactor.maybe_compact(
        memory,
        incoming_user_text="Continue.",
        instruction_context="AGENTS.md applies.",
        events=(),
        source_session_event_count=0,
        pending_operation=True,
    )

    assert result.triggered is False
    assert "pending" in result.reason
    assert memory.messages == before
    assert model.calls == []


def test_resume_loads_summary_boundary_recent_turns_and_newer_events(tmp_path):
    logger = create_session_logger(tmp_path, environ={})
    original_messages = (
        ("user_prompt", {"prompt": "Old user context that should be replaced."}),
        ("assistant_message", {"text": "Old assistant context."}),
        ("user_prompt", {"prompt": "Recent user one."}),
        ("assistant_message", {"text": "Recent assistant one."}),
        ("user_prompt", {"prompt": "Recent user two."}),
        ("assistant_message", {"text": "Recent assistant two."}),
    )
    for event, data in original_messages:
        logger.log(event, **data)
    memory = ConversationMemory(
        (
            {"role": "user", "content": original_messages[0][1]["prompt"]},
            {"role": "assistant", "content": original_messages[1][1]["text"]},
            {"role": "user", "content": original_messages[2][1]["prompt"]},
            {"role": "assistant", "content": original_messages[3][1]["text"]},
            {"role": "user", "content": original_messages[4][1]["prompt"]},
            {"role": "assistant", "content": original_messages[5][1]["text"]},
        )
    )
    model = RecordingModel(
        (ModelResponse(text='{"summary":"The old context was compacted.","facts":{}}'),)
    )
    compactor = ConversationCompactor(
        tmp_path,
        f"session_{logger.path.stem}",
        _config(compact_at=10, compact_to=5),
        model,
    )
    result = compactor.maybe_compact(
        memory,
        incoming_user_text="Continue after compaction.",
        instruction_context="AGENTS.md applies.",
        events=(),
        source_session_event_count=logger.record_count,
    )
    assert result.compacted is True
    logger.log("user_prompt", prompt="New user after compaction.")
    logger.log("assistant_message", text="New assistant after compaction.")

    loaded = load_session(
        tmp_path,
        logger.path.name,
        environ={},
        require_resumable=True,
    )
    serialized = json.dumps(loaded.messages)

    assert len(loaded.compacted_summaries) == 1
    assert "The old context was compacted." in serialized
    assert "Old user context that should be replaced." not in serialized
    assert "Recent user one." in serialized
    assert "Recent assistant two." in serialized
    assert "New user after compaction." in serialized
    assert "New assistant after compaction." in serialized


def test_textual_chat_emits_compaction_pair_and_logs_usage(tmp_path):
    model = RecordingModel(
        (
            ModelResponse(text="First answer " + "a" * 180),
            ModelResponse(text="Second answer " + "b" * 180),
            ModelResponse(text="Third answer " + "c" * 180),
            ModelResponse(
                text=json.dumps(
                    {
                        "summary": "Earlier chat safely summarized.",
                        "facts": {"open_items": ["Keep testing."]},
                    }
                )
            ),
            ModelResponse(text="Fourth answer."),
        )
    )
    controller = TextualChatController(
        tmp_path,
        _config(compact_at=100, compact_to=25),
        DenyApprovalProvider(),
        model_client=model,
    )
    events = []

    controller.send_turn("First prompt " + "x" * 180)
    controller.send_turn("Second prompt " + "y" * 180)
    controller.send_turn("Third prompt " + "z" * 180)
    fourth = controller.send_turn(
        "Fourth prompt after pressure.",
        event_callback=events.append,
    )

    compaction_events = [
        event
        for event in events
        if event.type
        in {
            EventType.MEMORY_COMPACTION_STARTED.value,
            EventType.MEMORY_COMPACTION_FINISHED.value,
        }
    ]
    assert [event.type for event in compaction_events] == [
        EventType.MEMORY_COMPACTION_STARTED.value,
        EventType.MEMORY_COMPACTION_FINISHED.value,
    ]
    assert compaction_events[1].parent_event_id == compaction_events[0].event_id
    assert compaction_events[1].payload["status"] == "completed"
    assert fourth.final_text == "Fourth answer."
    assert controller.conversation_messages[0]["content"].startswith(
        "[Working-memory compacted summary]"
    )
    summary_files = list(
        (tmp_path / ".agent" / "summaries").glob("*.summary.json")
    )
    assert len(summary_files) == 1
    records = [
        json.loads(line)
        for line in controller.session_logger.path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    event_names = [record["event"] for record in records]
    assert "memory.compaction.started" in event_names
    assert "memory.compaction.finished" in event_names
    usage = [
        record["data"]
        for record in records
        if record["event"] == "model_usage"
        and record["data"].get("phase") == "memory_compaction"
    ]
    assert len(usage) == 1
    assert usage[0]["reasoning_effort"] == "medium"
    assert usage[0]["tool_schema_count"] == 0


def test_manual_compact_uses_existing_flow_and_writes_summary(tmp_path):
    model = RecordingModel(
        (
            ModelResponse(text="First answer."),
            ModelResponse(text="Second answer."),
            ModelResponse(text="Third answer."),
            ModelResponse(
                text=json.dumps(
                    {
                        "summary": "The first turn was safely summarized.",
                        "facts": {"open_items": ["Continue the chat."]},
                    }
                )
            ),
        )
    )
    controller = TextualChatController(
        tmp_path,
        _config(compact_at=100_000, compact_to=25),
        DenyApprovalProvider(),
        model_client=model,
    )
    controller.send_turn("First prompt.")
    controller.send_turn("Second prompt.")
    controller.send_turn("Third prompt.")
    events = []

    command = controller.handle_slash_command("/compact")
    outcome = controller.run_slash_action(
        command.action,
        event_callback=events.append,
    )

    compaction_events = [
        event
        for event in events
        if event.type
        in {
            EventType.MEMORY_COMPACTION_STARTED.value,
            EventType.MEMORY_COMPACTION_FINISHED.value,
        }
    ]
    assert outcome["ok"] is True
    assert outcome["compacted"] is True
    assert outcome["text"] == "Context compacted. Summary saved."
    assert [event.type for event in compaction_events] == [
        EventType.MEMORY_COMPACTION_STARTED.value,
        EventType.MEMORY_COMPACTION_FINISHED.value,
    ]
    assert all(
        event.payload["trigger"] == "manual"
        for event in compaction_events
    )
    assert compaction_events[1].parent_event_id == (
        compaction_events[0].event_id
    )
    summary_path = (
        tmp_path / outcome["result"]["summary_path"]
    )
    assert summary_path.is_file()
    assert controller.conversation_messages[0]["content"].startswith(
        "[Working-memory compacted summary]"
    )
    assert len(model.calls) == 4
    assert model.calls[-1]["tools"] == []


def test_manual_compact_is_clear_noop_without_older_context(tmp_path):
    model = RecordingModel((ModelResponse(text="unused"),))
    controller = TextualChatController(
        tmp_path,
        _config(compact_at=100_000, compact_to=25),
        DenyApprovalProvider(),
        model_client=model,
    )
    events = []

    command = controller.handle_slash_command("/compact")
    outcome = controller.run_slash_action(
        command.action,
        event_callback=events.append,
    )

    assert outcome["ok"] is True
    assert outcome["compacted"] is False
    assert outcome["status"] == "noop"
    assert "not enough older conversation context" in outcome["text"]
    assert model.calls == []
    assert not (tmp_path / ".agent" / "summaries").exists()
    assert not any(
        event.type.startswith("memory.compaction.")
        for event in events
    )


def test_textual_chat_continues_with_warning_when_compaction_fails(tmp_path):
    class CompactionFailingModel:
        def __init__(self):
            self.agent_calls = 0

        def complete(self, messages, tools=None):
            if messages and str(messages[0].get("content", "")).startswith(
                "Summarize older LunarForge"
            ):
                raise RuntimeError("Synthetic compaction failure.")
            self.agent_calls += 1
            return ModelResponse(
                text=f"Agent answer {self.agent_calls}. " + "x" * 180
            )

    controller = TextualChatController(
        tmp_path,
        _config(compact_at=100, compact_to=25),
        DenyApprovalProvider(),
        model_client=CompactionFailingModel(),
    )
    controller.send_turn("First prompt " + "a" * 180)
    controller.send_turn("Second prompt " + "b" * 180)
    controller.send_turn("Third prompt " + "c" * 180)
    events = []

    result = controller.send_turn(
        "Fourth prompt still runs.",
        event_callback=events.append,
    )

    assert result.final_text.startswith("Agent answer 4.")
    finished = [
        event
        for event in events
        if event.type == EventType.MEMORY_COMPACTION_FINISHED.value
    ]
    assert len(finished) == 1
    assert finished[0].payload["status"] == "failed"
    assert "continuing with the existing safe context" in (
        finished[0].payload["warning"]
    )
    assert "Compaction warning:" in controller.status_text()


def test_plan_mode_chat_does_not_compact_without_session_log(tmp_path):
    config = AppConfig(
        permissions=PermissionConfig(mode="plan"),
        ui=UIConfig(
            chat=ChatUIConfig(
                compact_at_tokens=100,
                compact_to_tokens=25,
            )
        ),
    )
    model = RecordingModel(
        tuple(
            ModelResponse(text=f"Plan answer {index}. " + "x" * 180)
            for index in range(4)
        )
    )
    controller = TextualChatController(
        tmp_path,
        config,
        DenyApprovalProvider(),
        model_client=model,
    )

    for index in range(4):
        controller.send_turn(f"Plan prompt {index}. " + "y" * 180)

    assert controller.session_logger is None
    assert not (tmp_path / ".agent").exists()
    assert len(model.calls) == 4
