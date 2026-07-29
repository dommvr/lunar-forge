import asyncio
import importlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import ModuleType

import pytest
from typer.testing import CliRunner

import lunar_forge.cli as cli_module
from lunar_forge.approvals import (
    ApprovalRequest,
    DenyApprovalProvider,
    TextualApprovalProvider,
)
from lunar_forge.cli import TEXTUAL_INSTALL_MESSAGE, app
from lunar_forge.config import AppConfig, PermissionConfig, RuntimeConfig
from lunar_forge.events import EventFactory, EventType
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.runtime.sessions import create_session_logger, load_session
from lunar_forge.ui.textual_widgets import (
    TextualApprovalBridge,
    TextualChatController,
    TextualEventRenderer,
)


class _SequenceModel:
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


def _approval_request():
    return ApprovalRequest.create(
        kind="command",
        title="Run command",
        summary="python --version",
        details="Run local command: python --version.",
        risk="medium",
        mode="local",
        command="python --version",
        tool_name="run_command",
    )


def test_chat_help_does_not_import_textual_app(monkeypatch):
    monkeypatch.delitem(
        sys.modules,
        "lunar_forge.ui.textual_app",
        raising=False,
    )

    result = CliRunner().invoke(app, ["chat", "--help"])

    assert result.exit_code == 0
    assert "--project" in result.stdout
    assert "--resume" in result.stdout
    assert "lunar_forge.ui.textual_app" not in sys.modules


def test_chat_missing_dependency_prints_setup_message(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli_module,
        "_load_textual_chat_launcher",
        lambda: None,
    )
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Config must not load when Textual is unavailable.")
        ),
    )

    result = CliRunner().invoke(
        app,
        ["chat", "--project", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == TEXTUAL_INSTALL_MESSAGE


def test_textual_app_module_imports_with_mocked_dependency(monkeypatch):
    fake_textual = ModuleType("textual")
    fake_textual_app = ModuleType("textual.app")
    fake_textual_binding = ModuleType("textual.binding")
    fake_containers = ModuleType("textual.containers")
    fake_textual_message = ModuleType("textual.message")
    fake_widgets = ModuleType("textual.widgets")

    def decorator(*args, **kwargs):
        def wrap(function):
            return function

        return wrap

    class FakeApp:
        @classmethod
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, *args, **kwargs):
            pass

    class FakeWidget:
        def __init__(self, *args, **kwargs):
            pass

    class FakeMessage:
        def __init__(self, *args, **kwargs):
            pass

    class FakeBinding:
        def __init__(self, *args, **kwargs):
            pass

    class FakeContainer(FakeWidget):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeTextArea(FakeWidget):
        pass

    class FakeInput(FakeWidget):
        class Submitted:
            pass

    class FakeSelect(FakeWidget):
        NULL = object()

        def set_options(self, options):
            pass

    class FakeButton(FakeWidget):
        class Pressed:
            pass

    fake_textual.on = decorator
    fake_textual.work = decorator
    fake_textual.events = ModuleType("textual.events")
    fake_textual_app.App = FakeApp
    fake_textual_app.ComposeResult = object
    fake_textual_binding.Binding = FakeBinding
    fake_containers.Horizontal = FakeContainer
    fake_containers.Vertical = FakeContainer
    fake_textual_message.Message = FakeMessage
    fake_widgets.Button = FakeButton
    fake_widgets.Input = FakeInput
    fake_widgets.Label = FakeWidget
    fake_widgets.RichLog = FakeWidget
    fake_widgets.Select = FakeSelect
    fake_widgets.Static = FakeWidget
    fake_widgets.TextArea = FakeTextArea

    monkeypatch.setitem(sys.modules, "textual", fake_textual)
    monkeypatch.setitem(sys.modules, "textual.app", fake_textual_app)
    monkeypatch.setitem(
        sys.modules,
        "textual.binding",
        fake_textual_binding,
    )
    monkeypatch.setitem(
        sys.modules,
        "textual.containers",
        fake_containers,
    )
    monkeypatch.setitem(
        sys.modules,
        "textual.message",
        fake_textual_message,
    )
    monkeypatch.setitem(sys.modules, "textual.widgets", fake_widgets)
    monkeypatch.delitem(
        sys.modules,
        "lunar_forge.ui.textual_app",
        raising=False,
    )

    module = importlib.import_module("lunar_forge.ui.textual_app")

    assert hasattr(module, "LunarForgeTextualApp")
    assert callable(module.run_textual_chat)
    sys.modules.pop("lunar_forge.ui.textual_app", None)


def test_textual_app_mounts_when_optional_dependency_is_available(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import (
        ChatInput,
        LunarForgeTextualApp,
    )

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            top_card = textual_app.query_one("#top-card")
            assert top_card.border_title == textual_app.top_card_title
            assert textual_app.top_card_title.startswith("LunarForge v")
            assert textual_app.top_card_title not in textual_app.top_card_text
            assert "What are we building today?" in textual_app.top_card_text
            assert textual_app.query_one("#transcript") is not None
            assert textual_app.query_one("#approval-panel") is not None
            assert textual_app.query_one("#chat-input", ChatInput) is not None
            assert len(textual_app.query("#activity-status")) == 0
            assert len(textual_app.query("#activity-panel")) == 0
            assert len(textual_app.query("#tool-log")) == 0
            assert len(textual_app.query("#metadata-footer")) == 0

    asyncio.run(exercise())


def test_textual_exit_command_closes_pilot_cleanly(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/exit"
            await pilot.press("enter")

    asyncio.run(exercise())
    assert textual_app.is_running is False


def test_textual_app_shows_resumed_session_greeting(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import LunarForgeTextualApp

    previous_logger = create_session_logger(tmp_path)
    previous_logger.log("user_prompt", prompt="Remember the crater.")
    previous_logger.log("assistant_message", text="Crater remembered.")
    previous = load_session(
        tmp_path,
        previous_logger.path.name,
        require_resumable=True,
    )
    textual_app = LunarForgeTextualApp(
        tmp_path,
        AppConfig(),
        previous_session=previous,
    )

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert "Let’s get back to building." in textual_app.top_card_text
            assert "What are we building today?" not in (
                textual_app.top_card_text
            )
            assert textual_app.top_card_title not in textual_app.top_card_text

    asyncio.run(exercise())


def test_textual_app_pilot_runs_two_turns_with_live_memory(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    model = _SequenceModel(
        (
            ModelResponse(text="First pilot answer."),
            ModelResponse(text="Second pilot answer."),
        )
    )
    textual_app = LunarForgeTextualApp(
        tmp_path,
        AppConfig(),
        model_client=model,
    )

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = (
                "Explain this project in one sentence. Do not edit files. "
                "Do not run commands."
            )
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 1,
            )

            chat_input.text = (
                "Now show status. Do not edit files. Do not run commands."
            )
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 2,
            )

            chat_input.text = "/status"
            await pilot.press("enter")
            await pilot.pause()

        assert textual_app.controller.turn_count == 2
        transcript = textual_app.transcript_plain_text
        assert "\n\nLunarForge\nFirst pilot answer." in transcript
        assert "\n\nYou\nNow show status." in transcript
        assert "Done in " in transcript
        assert any(
            "First pilot answer." in str(message.get("content"))
            for message in model.calls[1]["messages"]
        )

    asyncio.run(exercise())


def test_textual_progress_block_lifecycle_uses_events(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    class BlockingModel:
        def __init__(self):
            self.started = Event()
            self.release = Event()

        def complete(self, messages, tools=None):
            self.started.set()
            if not self.release.wait(2):
                raise RuntimeError("Fake model was not released.")
            return ModelResponse(text="Progress answer.")

    model = BlockingModel()
    textual_app = LunarForgeTextualApp(
        tmp_path,
        AppConfig(),
        model_client=model,
    )

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = (
                "Explain this project. Do not edit files or run commands."
            )
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: (
                    model.started.is_set()
                    and textual_app.progress_text is not None
                ),
            )
            assert "Working" in (textual_app.progress_text or "")
            assert "Elapsed: " in (textual_app.progress_text or "")

            factory = EventFactory(
                session_id=textual_app.controller.session_id,
                turn_id="turn_progress",
            )
            textual_app._handle_agent_event(
                factory.create(
                    EventType.STATUS_UPDATED,
                    {"message": "Inspecting project files"},
                )
            )
            textual_app._handle_agent_event(
                factory.create(
                    EventType.TOOL_STARTED,
                    {
                        "tool_name": "read_file",
                        "args_preview": {"path": "README.md"},
                    },
                )
            )
            assert "Inspecting project files." in (
                textual_app.progress_text or ""
            )
            assert "Tool: read_file" in (textual_app.progress_text or "")

            model.release.set()
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 1,
            )
            assert textual_app.progress_text is None
            assert "LunarForge\nProgress answer." in (
                textual_app.transcript_plain_text
            )
            assert "Done in " in textual_app.transcript_plain_text

    try:
        asyncio.run(exercise())
    finally:
        model.release.set()


def test_textual_chat_input_paste_multiline_keys_and_bound(tmp_path):
    pytest.importorskip("textual")
    from textual import events

    from lunar_forge.ui.textual_app import (
        MAX_CHAT_INPUT_CHARACTERS,
        ChatInput,
        LunarForgeTextualApp,
    )

    model = _SequenceModel((ModelResponse(text="Input accepted."),))
    textual_app = LunarForgeTextualApp(
        tmp_path,
        AppConfig(),
        model_client=model,
    )

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)

            await chat_input._on_paste(events.Paste("first\nsecond"))
            assert chat_input.text == "first\nsecond"

            chat_input.load_text("alpha")
            chat_input.move_cursor((0, 5))
            await pilot.press("shift+enter")
            assert chat_input.text == "alpha\n"

            chat_input.load_text("")
            await chat_input._on_paste(
                events.Paste("x" * (MAX_CHAT_INPUT_CHARACTERS + 50))
            )
            await pilot.pause()
            assert len(chat_input.text) == MAX_CHAT_INPUT_CHARACTERS
            assert "Input was bounded" in textual_app.transcript_plain_text

            chat_input.load_text(
                "Summarize marker.txt.\n"
                "Do not edit files or run commands."
            )
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 1,
            )
            assert textual_app.controller.conversation_messages[0] == {
                "role": "user",
                "content": (
                    "Summarize marker.txt.\n"
                    "Do not edit files or run commands."
                ),
            }

            chat_input.text = "   /status"
            await pilot.press("enter")
            await pilot.pause()
            assert textual_app.controller.turn_count == 1
            assert "Completed turns: 1" in (
                textual_app.transcript_plain_text
            )

    asyncio.run(exercise())


def test_textual_clear_uses_confirmation_and_retains_session_log(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())
    session_path = textual_app.controller.session_logger.path

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            textual_app._write_transcript("system", "Keep until confirmed.")
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/clear"
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.query_one("#slash-popup").display,
            )

            assert "Keep until confirmed." in (
                textual_app.transcript_plain_text
            )
            assert session_path.exists()

            await pilot.click("#slash-apply")
            await _wait_for(
                pilot,
                lambda: not textual_app.query_one("#slash-popup").display,
            )

            assert "Keep until confirmed." not in (
                textual_app.transcript_plain_text
            )
            assert "session logs were retained" in (
                textual_app.transcript_plain_text
            )
            assert session_path.exists()

    asyncio.run(exercise())


def test_textual_config_popup_validates_and_saves_only_explicitly(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Select

    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())
    config_path = tmp_path / ".agent" / "config.yaml"

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/runtime"
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.query_one("#slash-popup").display,
            )
            assert "Current: local" in str(
                textual_app.query_one("#slash-details").render()
            )
            assert not config_path.exists()

            slash_choice = textual_app.query_one("#slash-choice", Select)
            assert slash_choice.value == "local"
            slash_choice.value = "docker"
            await pilot.click("#slash-apply")
            await _wait_for(
                pilot,
                lambda: not textual_app.query_one("#slash-popup").display,
            )
            assert textual_app.controller.config.runtime.mode == "docker"
            assert "Mode: docker" in textual_app.top_card_text
            assert not config_path.exists()

            chat_input.text = "/reasoning-effort"
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.query_one("#slash-popup").display,
            )
            slash_choice.value = "high"
            await pilot.click("#slash-save")
            await _wait_for(
                pilot,
                lambda: (
                    textual_app._approval_bridge.pending_request is not None
                ),
            )
            assert not config_path.exists()
            assert (
                textual_app.controller.config.model.reasoning.effort
                == "medium"
            )
            await pilot.click("#approval-approve")
            await _wait_for(
                pilot,
                lambda: (
                    config_path.exists()
                    and not textual_app._config_save_in_progress
                ),
            )
            assert (
                textual_app.controller.config.model.reasoning.effort
                == "high"
            )
            assert "Saved setting to" in textual_app.transcript_plain_text

    asyncio.run(exercise())


def test_textual_typed_project_scope_waits_for_approval(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())
    config_path = tmp_path / ".agent" / "config.yaml"

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/reasoning-effort high scope=project"
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: (
                    textual_app._approval_bridge.pending_request is not None
                ),
            )

            assert not config_path.exists()
            assert (
                textual_app.controller.config.model.reasoning.effort
                == "medium"
            )

            await pilot.click("#approval-approve")
            await _wait_for(
                pilot,
                lambda: (
                    config_path.exists()
                    and not textual_app._config_save_in_progress
                ),
            )

            assert (
                textual_app.controller.config.model.reasoning.effort
                == "high"
            )
            assert "Saved setting to" in textual_app.transcript_plain_text

    asyncio.run(exercise())


def test_textual_config_popup_reports_denied_write_without_state_change(
    tmp_path,
):
    pytest.importorskip("textual")
    from textual.widgets import Select

    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())
    config_path = tmp_path / ".agent" / "config.yaml"

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/plugins"
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.query_one("#slash-popup").display,
            )
            slash_choice = textual_app.query_one("#slash-choice", Select)
            assert slash_choice.value == "false"
            slash_choice.value = "true"
            await pilot.click("#slash-save")
            await _wait_for(
                pilot,
                lambda: (
                    textual_app._approval_bridge.pending_request is not None
                ),
            )

            await pilot.click("#approval-deny")
            await _wait_for(
                pilot,
                lambda: not textual_app._config_save_in_progress,
            )

            assert not config_path.exists()
            assert textual_app.controller.config.plugins.enabled is False
            assert "Could not save project config" in (
                textual_app.transcript_plain_text
            )
            assert "Denied in Textual UI" in (
                textual_app.transcript_plain_text
            )

    asyncio.run(exercise())


def test_textual_app_pilot_resolves_command_approval(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    model = _SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="command-call",
                        name="run_command",
                        arguments={"command": "python --version"},
                    ),
                ),
            ),
            ModelResponse(text="Python version checked."),
        )
    )
    textual_app = LunarForgeTextualApp(
        tmp_path,
        AppConfig(),
        model_client=model,
    )

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = (
                "Use run_command to run python --version. Do not edit files."
            )
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: (
                    textual_app._approval_bridge.pending_request is not None
                ),
            )
            assert textual_app.controller.turn_count == 0
            await _wait_for(
                pilot,
                lambda: (
                    textual_app.progress_text is not None
                    and "Command: python --version"
                    in textual_app.progress_text
                ),
            )
            await pilot.click("#approval-approve")
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 1,
            )
            assert textual_app.progress_text is None
            assert "Done in " in textual_app.transcript_plain_text

        final_message = textual_app.controller.conversation_messages[-1]
        assert final_message["role"] == "assistant"
        assert final_message["content"].startswith("Python version checked.")
        assert "python --version: passed" in final_message["content"]
        session_text = textual_app.controller.session_logger.path.read_text(
            encoding="utf-8"
        )
        assert '"event":"permission.requested"' in session_text
        assert '"event":"permission.resolved"' in session_text
        assert '"approved":true' in session_text

    asyncio.run(exercise())


def test_chat_controller_runs_two_turns_in_one_session(tmp_path):
    model = _SequenceModel(
        (
            ModelResponse(text="First answer."),
            ModelResponse(text="Second answer."),
        )
    )
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
        model_client=model,
    )
    rendered_events = []

    first = controller.send_turn(
        "Explain this project in one sentence. Do not edit files. "
        "Do not run commands.",
        event_callback=rendered_events.append,
    )
    second = controller.send_turn(
        "Now show status. Do not edit files. Do not run commands.",
        event_callback=rendered_events.append,
    )

    assert first.session_id == second.session_id == controller.session_id
    assert first.turn_id != second.turn_id
    assert controller.turn_count == 2
    assert controller.conversation_messages[-4:] == (
        {
            "role": "user",
            "content": (
                "Explain this project in one sentence. Do not edit files. "
                "Do not run commands."
            ),
        },
        {"role": "assistant", "content": "First answer."},
        {
            "role": "user",
            "content": (
                "Now show status. Do not edit files. Do not run commands."
            ),
        },
        {"role": "assistant", "content": "Second answer."},
    )
    assert any(
        "First answer." in str(message.get("content"))
        for message in model.calls[1]["messages"]
    )
    assert {
        event.session_id for event in rendered_events
    } == {controller.session_id}
    assert sum(
        event.type == EventType.SESSION_STARTED.value
        for event in rendered_events
    ) == 1

    session_files = list((tmp_path / ".agent" / "sessions").glob("*.jsonl"))
    assert len(session_files) == 1
    records = [
        json.loads(line)
        for line in session_files[0].read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert sum(record["event"] == "user_prompt" for record in records) == 2
    assert sum(
        record["event"] == "assistant_message"
        for record in records
    ) == 2


def test_chat_controller_can_continue_after_safe_turn_error(tmp_path):
    class ErrorThenResponseModel:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Synthetic model failure.")
            return ModelResponse(text="Recovered safely.")

    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
        model_client=ErrorThenResponseModel(),
    )
    events = []

    with pytest.raises(RuntimeError, match="Synthetic model failure"):
        controller.send_turn(
            "First read-only turn.",
            event_callback=events.append,
        )
    recovered = controller.send_turn(
        "Second read-only turn.",
        event_callback=events.append,
    )

    assert any(event.type == EventType.ERROR.value for event in events)
    assert recovered.final_text == "Recovered safely."
    assert controller.turn_count == 1
    assert controller.conversation_messages[-1]["content"] == (
        "Recovered safely."
    )


def test_chat_controller_resumes_history_into_a_new_linked_session(tmp_path):
    previous_logger = create_session_logger(tmp_path)
    previous_logger.log("user_prompt", prompt="Remember Copernicus.")
    previous_logger.log(
        "assistant_message",
        text="I will remember Copernicus.",
    )
    previous = load_session(tmp_path, "latest")
    model = _SequenceModel((ModelResponse(text="Copernicus."),))

    current_config = AppConfig(
        runtime=RuntimeConfig(mode="docker"),
        permissions=PermissionConfig(mode="no-command"),
    )
    rendered_events = []
    controller = TextualChatController(
        tmp_path,
        current_config,
        DenyApprovalProvider(),
        previous_session=previous,
        model_client=model,
    )
    result = controller.send_turn(
        "What name did I ask you to remember?",
        event_callback=rendered_events.append,
    )

    assert result.final_text == "Copernicus."
    assert controller.session_path != previous.relative_path
    assert controller.resumed_session_id == previous.session_id
    assert previous.session_id in (controller.resume_notice or "")
    status = controller.status_text()
    assert f"Resumed from session: {previous.session_id}" in status
    assert "Runtime mode: docker" in status
    assert "Permission mode: no-command" in status
    assert any(
        "Remember Copernicus." in str(message.get("content"))
        for message in model.calls[0]["messages"]
    )
    resumed_events = [
        event
        for event in rendered_events
        if event.type == EventType.SESSION_RESUMED.value
    ]
    assert len(resumed_events) == 1
    assert resumed_events[0].payload["source_session_id"] == previous.session_id
    assert resumed_events[0].payload["tool_calls_replayed"] is False
    assert resumed_events[0].payload["approvals_reused"] is False
    new_session_text = controller.session_logger.path.read_text(encoding="utf-8")
    new_records = [
        json.loads(line)
        for line in new_session_text.splitlines()
        if line.strip()
    ]
    assert '"event":"session_resumed"' in new_session_text
    assert previous.relative_path in new_session_text
    assert previous.session_id in new_session_text
    assert new_records[0]["event"] == "session_started"
    assert new_records[0]["data"]["runtime_mode"] == "docker"
    assert new_records[0]["data"]["permission_mode"] == "no-command"


def test_textual_resume_requires_fresh_approval_after_historical_grant(tmp_path):
    class RecordingDenyProvider(DenyApprovalProvider):
        def __init__(self):
            self.requests = []

        def request_approval(self, request):
            self.requests.append(request)
            return super().request_approval(request)

    previous_logger = create_session_logger(tmp_path)
    previous_logger.log(
        "user_prompt",
        prompt="Run python --version after approval.",
    )
    previous_logger.log(
        "permission.resolved",
        request_id="old_request",
        approved=True,
        source="cli",
    )
    previous_logger.log(
        "assistant_message",
        text="The old request was approved.",
    )
    previous = load_session(
        tmp_path,
        previous_logger.path.name,
        require_resumable=True,
    )
    provider = RecordingDenyProvider()
    model = _SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="call_fresh_approval",
                        name="run_command",
                        arguments={"command": "python --version"},
                    ),
                )
            ),
            ModelResponse(text="The fresh request was denied."),
        )
    )
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        provider,
        previous_session=previous,
        model_client=model,
    )

    result = controller.send_turn(
        "Use run_command to run python --version. Do not edit files."
    )

    assert result.final_text == "The fresh request was denied."
    assert len(provider.requests) == 1
    assert provider.requests[0].id != "old_request"
    assert all(
        "old_request" not in message["content"]
        for message in previous.messages
    )
    new_records = [
        json.loads(line)
        for line in controller.session_logger.path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    decisions = [
        record
        for record in new_records
        if record["event"] == "permission.resolved"
    ]
    assert len(decisions) == 1
    assert decisions[0]["data"]["approved"] is False


def test_textual_controller_refuses_loaded_session_from_another_project(
    tmp_path,
):
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    previous_logger = create_session_logger(first_project)
    previous_logger.log("user_prompt", prompt="First project context.")
    previous = load_session(
        first_project,
        previous_logger.path.name,
        require_resumable=True,
    )

    with pytest.raises(ValueError, match="belongs to another project"):
        TextualChatController(
            second_project,
            AppConfig(),
            DenyApprovalProvider(),
            previous_session=previous,
        )


def test_textual_approval_provider_approves_and_denies():
    notified = Event()
    bridge = TextualApprovalBridge(lambda request: notified.set())
    provider = TextualApprovalProvider(bridge.resolve)

    with ThreadPoolExecutor(max_workers=1) as executor:
        approved_future = executor.submit(
            provider.request_approval,
            _approval_request(),
        )
        assert notified.wait(1)
        assert bridge.approve() is True
        approved = approved_future.result(timeout=1)

    notified.clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        denied_future = executor.submit(
            provider.request_approval,
            _approval_request(),
        )
        assert notified.wait(1)
        assert bridge.deny() is True
        denied = denied_future.result(timeout=1)

    assert approved.approved is True
    assert approved.source == "textual"
    assert denied.approved is False
    assert denied.source == "textual"


def test_textual_renderer_consumes_status_tool_final_and_error_events():
    factory = EventFactory(
        session_id="session_test",
        turn_id="turn_test",
    )
    renderer = TextualEventRenderer()

    status = renderer.handle(
        factory.create(
            EventType.STATUS_UPDATED,
            {"message": "Thinking safely"},
        )
    )
    tool = renderer.handle(
        factory.create(
            EventType.TOOL_STARTED,
            {"tool_name": "read_file"},
        )
    )
    final = renderer.handle(
        factory.create(
            EventType.ASSISTANT_MESSAGE_COMPLETED,
            {
                "text": (
                    "Done.\n\n"
                    "Session log: .agent/sessions/example.jsonl"
                )
            },
        )
    )
    error = renderer.handle(
        factory.create(EventType.ERROR, {"message": "Turn exploded."})
    )

    assert status is not None and status.status == "Thinking safely"
    assert tool is not None
    assert tool.tool_text == "Tool: read_file · started"
    assert final is not None and final.transcript_text == "Done."
    assert final.transcript_role == "assistant"
    assert error is not None and error.transcript_text == "Turn exploded."
    assert error.transcript_role == "error"


def test_textual_slash_commands_are_handled(tmp_path):
    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
    )

    help_result = controller.handle_slash_command("/help")
    status_result = controller.handle_slash_command("/status")
    clear_result = controller.handle_slash_command("/clear")
    exit_result = controller.handle_slash_command("/exit")
    normal_result = controller.handle_slash_command("hello")

    assert help_result.handled is True
    assert "/status" in (help_result.message or "")
    assert status_result.handled is True
    assert controller.session_id in (status_result.message or "")
    assert clear_result.confirmation is not None
    confirmed = controller.confirm_slash_command(clear_result.confirmation)
    assert confirmed.clear_transcript is True
    assert exit_result.exit_app is True
    assert normal_result.handled is False


def test_chat_cli_passes_latest_session_to_launcher(monkeypatch, tmp_path):
    session = create_session_logger(tmp_path)
    session.log("user_prompt", prompt="Remember the crater.")
    session.log("assistant_message", text="Crater remembered.")
    captured = {}

    def launch(project_root, config, **kwargs):
        captured["project_root"] = project_root
        captured["config"] = config
        captured.update(kwargs)

    monkeypatch.setattr(
        cli_module,
        "_load_textual_chat_launcher",
        lambda: launch,
    )

    result = CliRunner().invoke(
        app,
        [
            "chat",
            "--project",
            str(tmp_path),
            "--resume",
            "latest",
        ],
    )

    assert result.exit_code == 0
    assert captured["project_root"] == tmp_path.resolve()
    previous = captured["previous_session"]
    assert previous.relative_path == session.relative_path
    assert any(
        "Remember the crater." in message["content"]
        for message in previous.messages
    )


async def _wait_for(pilot, condition, attempts=200):
    for _ in range(attempts):
        await pilot.pause()
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Textual pilot condition was not reached.")
