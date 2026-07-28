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
from lunar_forge.config import AppConfig
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
    fake_containers = ModuleType("textual.containers")
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

    class FakeContainer(FakeWidget):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeInput(FakeWidget):
        class Submitted:
            pass

    class FakeButton(FakeWidget):
        class Pressed:
            pass

    fake_textual.on = decorator
    fake_textual.work = decorator
    fake_textual_app.App = FakeApp
    fake_textual_app.ComposeResult = object
    fake_containers.Horizontal = FakeContainer
    fake_containers.Vertical = FakeContainer
    fake_widgets.Button = FakeButton
    fake_widgets.Input = FakeInput
    fake_widgets.Label = FakeWidget
    fake_widgets.RichLog = FakeWidget
    fake_widgets.Static = FakeWidget

    monkeypatch.setitem(sys.modules, "textual", fake_textual)
    monkeypatch.setitem(sys.modules, "textual.app", fake_textual_app)
    monkeypatch.setitem(
        sys.modules,
        "textual.containers",
        fake_containers,
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
    from lunar_forge.ui.textual_app import LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert textual_app.query_one("#transcript") is not None
            assert textual_app.query_one("#activity-status") is not None
            assert textual_app.query_one("#approval-panel") is not None
            assert textual_app.query_one("#tool-log") is not None
            assert textual_app.query_one("#chat-input") is not None
            assert textual_app.query_one("#metadata-footer") is not None

    asyncio.run(exercise())


def test_textual_app_pilot_runs_two_turns_with_live_memory(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Input

    from lunar_forge.ui.textual_app import LunarForgeTextualApp

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
            chat_input = textual_app.query_one("#chat-input", Input)
            chat_input.value = (
                "Explain this project in one sentence. Do not edit files. "
                "Do not run commands."
            )
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 1,
            )

            chat_input.value = (
                "Now show status. Do not edit files. Do not run commands."
            )
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 2,
            )

            chat_input.value = "/status"
            await pilot.press("enter")
            await pilot.pause()

        assert textual_app.controller.turn_count == 2
        assert any(
            "First pilot answer." in str(message.get("content"))
            for message in model.calls[1]["messages"]
        )

    asyncio.run(exercise())


def test_textual_app_pilot_resolves_command_approval(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Input

    from lunar_forge.ui.textual_app import LunarForgeTextualApp

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
            chat_input = textual_app.query_one("#chat-input", Input)
            chat_input.value = (
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
            await pilot.click("#approval-approve")
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 1,
            )

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

    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
        previous_session=previous,
        model_client=model,
    )
    result = controller.send_turn("What name did I ask you to remember?")

    assert result.final_text == "Copernicus."
    assert controller.session_path != previous.relative_path
    assert any(
        "Remember Copernicus." in str(message.get("content"))
        for message in model.calls[0]["messages"]
    )
    new_session_text = controller.session_logger.path.read_text(
        encoding="utf-8"
    )
    assert '"event":"session_resumed"' in new_session_text
    assert previous.relative_path in new_session_text


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
    assert tool is not None and tool.tool_text == "read_file · started"
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
    assert clear_result.clear_transcript is True
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
