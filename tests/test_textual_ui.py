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
from lunar_forge.tools.files import write_file
from lunar_forge.ui.textual_widgets import (
    ChatTurnCancelled,
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
        class Changed:
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
    fake_containers.VerticalScroll = FakeContainer
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
            frame = textual_app.query_one("#chat-frame")
            top_card = textual_app.query_one("#top-card")
            input_area = textual_app.query_one("#input-area")
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            assert frame.border_title == textual_app.top_card_title
            assert top_card.border_title is None
            assert textual_app.top_card_title.startswith("LunarForge v")
            assert textual_app.top_card_title not in textual_app.top_card_text
            assert "What are we building today?" in textual_app.top_card_text
            assert len(textual_app.query("#chat-frame")) == 1
            assert frame.query_one("#top-card") is top_card
            assert frame.query_one("#transcript") is not None
            assert frame.query_one("#input-area") is input_area
            assert input_area.query_one("#chat-input") is chat_input
            assert frame.styles.border_top[0] != "none"
            assert top_card.styles.border_bottom[0] != "none"
            assert input_area.styles.border_top[0] != "none"
            assert (
                frame.styles.border_top[1]
                == top_card.styles.border_bottom[1]
                == input_area.styles.border_top[1]
            )
            assert chat_input.styles.border_top[0] in {"", "none"}
            assert textual_app.query_one("#approval-panel") is not None
            assert textual_app.query_one("#slash-hints") is not None
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
        assert "\n\nLunarForge:\nFirst pilot answer." in transcript
        assert "\n\nYou:\nNow show status." in transcript
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
            assert "Gathering information about the project" in (
                textual_app.progress_text or ""
            )
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
            assert "Gathering information about the project" in (
                textual_app.progress_text or ""
            )
            assert "Tool: read_file" in (textual_app.progress_text or "")

            model.release.set()
            await _wait_for(
                pilot,
                lambda: textual_app.controller.turn_count == 1,
            )
            assert textual_app.progress_text is None
            assert "LunarForge:\nProgress answer." in (
                textual_app.transcript_plain_text
            )
            assert "Done in " in textual_app.transcript_plain_text

    try:
        asyncio.run(exercise())
    finally:
        model.release.set()


@pytest.mark.parametrize("approved", (True, False))
def test_textual_approval_progress_restores_active_phase(
    tmp_path,
    approved,
):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(100, 34)) as pilot:
            textual_app._turn_running = True
            textual_app._begin_progress("Applying project changes...")
            factory = EventFactory(
                session_id=textual_app.controller.session_id,
                turn_id="turn_approval_progress",
            )
            textual_app._handle_agent_event(
                factory.create(
                    EventType.PERMISSION_REQUESTED,
                    {
                        "request_id": "approval_progress",
                        "tool_name": "write_file",
                        "file_path": "README.md",
                    },
                )
            )
            assert "Waiting for approval" in (
                textual_app.progress_text or ""
            )
            assert "Tool: write_file" in (
                textual_app.progress_text or ""
            )

            textual_app._handle_agent_event(
                factory.create(
                    EventType.PERMISSION_RESOLVED,
                    {
                        "request_id": "approval_progress",
                        "approved": approved,
                    },
                )
            )
            await pilot.pause()

            assert "Applying project changes" in (
                textual_app.progress_text or ""
            )
            assert "Approval granted" not in (
                textual_app.progress_text or ""
            )
            assert "Approval denied" not in (
                textual_app.progress_text or ""
            )
            textual_app._turn_running = False
            textual_app._end_progress()

    asyncio.run(exercise())


def test_textual_progress_ellipsis_advances_deterministically(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import (
        ELLIPSIS_TICK_SECONDS,
        PROGRESS_REFRESH_SECONDS,
        _ProgressState,
        LunarForgeTextualApp,
    )

    assert ELLIPSIS_TICK_SECONDS == pytest.approx(2.0 / 3.0)
    assert ELLIPSIS_TICK_SECONDS * 3 == pytest.approx(2.0)
    assert PROGRESS_REFRESH_SECONDS < ELLIPSIS_TICK_SECONDS

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())
    textual_app._render_transcript = lambda: None
    textual_app._progress = _ProgressState(
        sentence="Planning the implementation",
        started_at=0.0,
        animate_ellipsis=True,
    )

    # Responsive elapsed-time refreshes do not advance the visible dots.
    textual_app._refresh_progress_timer()
    textual_app._refresh_progress_timer()
    assert textual_app.progress_text.splitlines()[0] == (
        "Planning the implementation."
    )
    textual_app._refresh_progress_ellipsis()
    assert textual_app.progress_text.splitlines()[0] == (
        "Planning the implementation.."
    )
    textual_app._refresh_progress_ellipsis()
    assert textual_app.progress_text.splitlines()[0] == (
        "Planning the implementation..."
    )
    textual_app._refresh_progress_ellipsis()
    assert textual_app.progress_text.splitlines()[0] == (
        "Planning the implementation."
    )

    textual_app._progress = None
    textual_app._refresh_progress_ellipsis()
    assert textual_app.progress_text is None


def test_textual_mount_schedules_visible_ellipsis_at_two_second_cycle(
    tmp_path,
):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import (
        ELLIPSIS_TICK_SECONDS,
        PROGRESS_REFRESH_SECONDS,
        LunarForgeTextualApp,
    )

    class RecordingIntervalApp(LunarForgeTextualApp):
        def __init__(self, *args, **kwargs):
            self.scheduled_intervals = []
            super().__init__(*args, **kwargs)

        def set_interval(self, interval, callback, **kwargs):
            self.scheduled_intervals.append(
                (interval, callback.__name__)
            )
            return super().set_interval(interval, callback, **kwargs)

    textual_app = RecordingIntervalApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            scheduled = dict(
                (callback_name, interval)
                for interval, callback_name
                in textual_app.scheduled_intervals
            )
            assert scheduled["_refresh_progress_timer"] == pytest.approx(
                PROGRESS_REFRESH_SECONDS
            )
            assert scheduled[
                "_refresh_progress_ellipsis"
            ] == pytest.approx(ELLIPSIS_TICK_SECONDS)
            assert (
                scheduled["_refresh_progress_ellipsis"] * 3
            ) == pytest.approx(2.0)

    asyncio.run(exercise())


@pytest.mark.parametrize("viewport", ((56, 32), (140, 50)))
def test_textual_long_approval_keeps_scroll_body_and_fixed_footer(
    tmp_path,
    viewport,
):
    pytest.importorskip("textual")
    from textual.containers import VerticalScroll

    from lunar_forge.ui.textual_app import LunarForgeTextualApp

    request = ApprovalRequest.create(
        kind="command",
        title="Run a reviewed project command",
        summary="Long approval summary " * 80,
        details="Long wrapped approval detail line. " * 300,
        risk="medium",
        mode="local",
        command="python scripts/verify_project.py --all-checks",
        tool_name="run_command",
    )
    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=viewport) as pilot:
            textual_app._turn_running = True
            textual_app._begin_progress("Running an approved command...")
            with ThreadPoolExecutor(max_workers=1) as executor:
                decision_future = executor.submit(
                    textual_app.controller.approval_provider.request_approval,
                    request,
                )
                await _wait_for(
                    pilot,
                    lambda: (
                        textual_app._approval_bridge.pending_request
                        is not None
                    ),
                )
                panel = textual_app.query_one("#approval-panel")
                body = textual_app.query_one(
                    "#approval-details-scroll",
                    VerticalScroll,
                )
                actions = textual_app.query_one("#approval-actions")
                approve = textual_app.query_one("#approval-approve")
                deny = textual_app.query_one("#approval-deny")
                input_area = textual_app.query_one("#input-area")

                assert panel.display is True
                assert body.max_scroll_y > 0
                assert actions.region.y >= body.region.y
                assert actions.region.bottom <= panel.region.bottom
                assert approve.region.width > 0
                assert deny.region.width > 0
                assert approve.region.bottom <= textual_app.screen.size.height
                assert deny.region.bottom <= textual_app.screen.size.height
                assert input_area.region.y >= panel.region.bottom - 1

                body.scroll_end(animate=False)
                await pilot.pause()
                assert body.scroll_y > 0

                await pilot.click("#approval-approve")
                await _wait_for(
                    pilot,
                    lambda: (
                        textual_app._approval_bridge.pending_request is None
                    ),
                )
                decision = decision_future.result(timeout=1)
                assert decision.approved is True
                assert "Running an approved command" in (
                    textual_app.progress_text or ""
                )
            textual_app._turn_running = False
            textual_app._end_progress()

    asyncio.run(exercise())


def test_textual_slash_hints_filter_and_hide(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            hints = textual_app.query_one("#slash-hints")

            chat_input.load_text("/")
            await _wait_for(pilot, lambda: hints.display)
            rendered = str(hints.render())
            for command in (
                "/help",
                "/status",
                "/compact",
                "/sessions",
                "/resume",
            ):
                assert command in rendered

            chat_input.load_text("/s")
            await pilot.pause()
            rendered = str(hints.render())
            assert "/show-usage" in rendered
            assert "/subagents" in rendered
            assert "/sessions" in rendered

            chat_input.load_text("/git")
            await pilot.pause()
            rendered = str(hints.render())
            assert "/git status" in rendered
            assert "/git commit" in rendered

            chat_input.load_text("/not-a-command")
            await pilot.pause()
            assert "No matching commands." in str(hints.render())

            await pilot.press("escape")
            await pilot.pause()
            assert hints.display is False

            chat_input.load_text("/status")
            await _wait_for(pilot, lambda: hints.display)
            await pilot.press("enter")
            await pilot.pause()
            assert hints.display is False
            assert textual_app.controller.turn_count == 0

            chat_input.load_text("ordinary text")
            await pilot.pause()
            assert hints.display is False

    asyncio.run(exercise())


def _create_resumable_sessions(project_root, count):
    for index in range(count):
        logger = create_session_logger(project_root, environ={})
        logger.log(
            "user_prompt",
            prompt=f"Historical question {index}.",
        )
        logger.log(
            "assistant_message",
            text=f"Historical answer {index}.",
        )


def test_textual_short_sessions_picker_renders_without_scroll(tmp_path):
    pytest.importorskip("textual")
    from textual.containers import VerticalScroll

    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    _create_resumable_sessions(tmp_path, 1)
    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(100, 34)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/sessions"
            await pilot.press("enter")
            await pilot.pause()

            details = textual_app.query_one(
                "#slash-details-scroll",
                VerticalScroll,
            )
            assert textual_app.query_one("#slash-popup").display is True
            assert details.max_scroll_y == 0
            assert "Select a compatible session" in str(
                textual_app.query_one("#slash-details").render()
            )
            assert chat_input.region.height > 0
            assert chat_input.region.bottom <= textual_app.screen.size.height

    asyncio.run(exercise())


@pytest.mark.parametrize("viewport", ((62, 30), (140, 52)))
def test_textual_long_sessions_picker_is_bounded_and_keeps_input_visible(
    tmp_path,
    viewport,
):
    pytest.importorskip("textual")
    from textual.containers import VerticalScroll

    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    _create_resumable_sessions(tmp_path, 24)
    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=viewport) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/sessions"
            await pilot.press("enter")
            await pilot.pause()

            popup = textual_app.query_one("#slash-popup")
            details = textual_app.query_one(
                "#slash-details-scroll",
                VerticalScroll,
            )
            input_area = textual_app.query_one("#input-area")
            frame = textual_app.query_one("#chat-frame")

            assert popup.display is True
            assert details.max_scroll_y > 0
            assert popup.region.bottom <= input_area.region.y + 1
            assert input_area.region.height > 0
            assert chat_input.region.height > 0
            assert chat_input.region.bottom <= frame.region.bottom
            assert frame.region.bottom <= textual_app.screen.size.height

            details.scroll_end(animate=False)
            await pilot.pause()
            assert details.scroll_y > 0
            chat_input.focus()
            chat_input.load_text("/status")
            await pilot.pause()
            assert chat_input.has_focus is True

    asyncio.run(exercise())


def test_textual_transcript_labels_are_colon_styled_and_separated(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import (
        TRANSCRIPT_LABEL_STYLES,
        LunarForgeTextualApp,
    )

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(120, 40)) as pilot:
            textual_app._write_transcript("user", "First message.")
            textual_app._write_transcript(
                "assistant",
                "Second message.",
            )
            await pilot.pause()

            assert textual_app.transcript_plain_text == (
                "You:\nFirst message.\n\n"
                "LunarForge:\nSecond message."
            )
            entries = textual_app._transcript_entries
            assert entries[0].label_style == (
                TRANSCRIPT_LABEL_STYLES["user"]
            )
            assert entries[1].label_style == (
                TRANSCRIPT_LABEL_STYLES["assistant"]
            )
            assert entries[0].label_style != entries[1].label_style

    asyncio.run(exercise())


def test_textual_chat_input_paste_multiline_keys_and_bound(
    tmp_path,
    monkeypatch,
):
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

            textual_app.copy_to_clipboard("clipboard\npaste")
            await pilot.press("ctrl+v")
            await pilot.pause()
            assert chat_input.text == "clipboard\npaste"
            assert textual_app.controller.turn_count == 0

            textual_app.copy_to_clipboard("")
            monkeypatch.setitem(
                ChatInput.action_paste.__globals__,
                "_read_system_clipboard_text",
                lambda: "fallback\nclipboard",
            )
            chat_input.load_text("")
            chat_input.action_paste()
            await pilot.pause()
            assert chat_input.text == "fallback\nclipboard"
            assert textual_app.controller.turn_count == 0

            chat_input.load_text("")
            await chat_input._on_paste(events.Paste("first\nsecond"))
            assert chat_input.text == "first\nsecond"
            assert textual_app.controller.turn_count == 0

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


def test_textual_ctrl_v_unavailable_shows_note_without_submitting(
    tmp_path,
    monkeypatch,
):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())
    monkeypatch.setitem(
        ChatInput.action_paste.__globals__,
        "_read_system_clipboard_text",
        lambda: None,
    )

    async def exercise():
        async with textual_app.run_test(size=(100, 32)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            textual_app.copy_to_clipboard("")
            chat_input.action_paste()
            await pilot.pause()

            assert chat_input.text == ""
            assert textual_app.controller.turn_count == 0
            assert "Clipboard text was unavailable" in (
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
            assert textual_app.query_one("#slash-apply").display is True
            assert textual_app.query_one("#slash-save").display is True
            assert textual_app.query_one("#slash-save-user").display is True
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


def test_textual_finish_without_active_task_keeps_chat_open(tmp_path):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    textual_app = LunarForgeTextualApp(tmp_path, AppConfig())

    async def exercise():
        async with textual_app.run_test(size=(100, 32)) as pilot:
            chat_input = textual_app.query_one("#chat-input", ChatInput)
            chat_input.text = "/finish"
            await pilot.press("enter")
            await pilot.pause()

            assert textual_app.controller.turn_count == 0
            assert "No active task to finish." in (
                textual_app.transcript_plain_text
            )
            assert chat_input.disabled is False
            assert textual_app._turn_running is False

    asyncio.run(exercise())


def test_textual_finish_clears_pending_approval_and_keeps_chat_open(
    tmp_path,
):
    pytest.importorskip("textual")
    from lunar_forge.ui.textual_app import ChatInput, LunarForgeTextualApp

    model = _SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="finish-command",
                        name="run_command",
                        arguments={"command": "python --version"},
                    ),
                ),
            ),
            ModelResponse(text="The denied command was not run."),
        )
    )
    textual_app = LunarForgeTextualApp(
        tmp_path,
        AppConfig(),
        model_client=model,
    )

    async def exercise():
        async with textual_app.run_test(size=(110, 36)) as pilot:
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
            assert chat_input.disabled is False

            chat_input.text = "/finish"
            chat_input.focus()
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: (
                    textual_app._approval_bridge.pending_request is None
                    and not textual_app._turn_running
                ),
            )

            assert textual_app.controller.turn_count == 0
            assert "Task finished. Current-turn changes were revoked." in (
                textual_app.transcript_plain_text
            )
            assert "No current-turn file changes needed to be revoked." in (
                textual_app.transcript_plain_text
            )
            assert chat_input.disabled is False

        session_text = (
            textual_app.controller.session_logger.path.read_text(
                encoding="utf-8"
            )
        )
        assert '"event":"turn.cancelled"' in session_text
        assert '"event":"rollback.started"' in session_text
        assert '"event":"rollback.finished"' in session_text
        assert '"approved":false' in session_text

    asyncio.run(exercise())


def test_chat_controller_finish_rolls_back_only_current_turn_files(
    tmp_path,
):
    edited = tmp_path / "edited.txt"
    previous_turn = tmp_path / "previous-turn.txt"
    unrelated = tmp_path / "unrelated.txt"
    edited.write_text("before active turn\n", encoding="utf-8")
    previous_turn.write_text("kept from prior turn\n", encoding="utf-8")
    unrelated.write_text("unrelated dirty content\n", encoding="utf-8")
    callback_reached = Event()
    release_callback = Event()

    def event_runner(prompt, project_root, **kwargs):
        factory = kwargs["event_factory"]
        edited_result = write_file(
            project_root,
            "edited.txt",
            "changed by active turn\n",
            overwrite=True,
        )
        yield factory.create(
            EventType.TOOL_FINISHED,
            {
                "tool_name": "write_file",
                "call_id": "edit-existing",
                "ok": True,
                "result": edited_result,
            },
        )
        created_result = write_file(
            project_root,
            "created-this-turn.txt",
            "created by active turn\n",
        )
        yield factory.create(
            EventType.TOOL_FINISHED,
            {
                "tool_name": "write_file",
                "call_id": "create-new",
                "ok": True,
                "result": created_result,
            },
        )
        yield factory.create(
            EventType.ASSISTANT_MESSAGE_COMPLETED,
            {"text": "This result should be cancelled."},
        )

    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
        event_runner=event_runner,
    )

    def observe(event):
        if (
            event.type == EventType.TOOL_FINISHED.value
            and event.payload.get("call_id") == "create-new"
        ):
            callback_reached.set()
            assert release_callback.wait(2)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            controller.send_turn,
            "Make two tracked file changes.",
            event_callback=observe,
        )
        assert callback_reached.wait(1)
        assert controller.request_active_turn_finish() is True
        release_callback.set()
        with pytest.raises(ChatTurnCancelled) as cancelled:
            future.result(timeout=3)

    summary = cancelled.value.summary
    assert summary.complete is True
    assert summary.restored_files == ("edited.txt",)
    assert summary.removed_files == ("created-this-turn.txt",)
    assert edited.read_text(encoding="utf-8") == "before active turn\n"
    assert not (tmp_path / "created-this-turn.txt").exists()
    assert previous_turn.read_text(encoding="utf-8") == (
        "kept from prior turn\n"
    )
    assert unrelated.read_text(encoding="utf-8") == (
        "unrelated dirty content\n"
    )
    assert controller.turn_count == 0
    assert controller.conversation_messages == ()

    session_text = controller.session_logger.path.read_text(
        encoding="utf-8"
    )
    assert '"event":"turn.cancelled"' in session_text
    assert '"event":"rollback.started"' in session_text
    assert '"event":"rollback.finished"' in session_text
    assert '"edited.txt"' in session_text
    assert '"created-this-turn.txt"' in session_text


def test_chat_controller_finish_skips_tracked_file_changed_externally(
    tmp_path,
):
    callback_reached = Event()
    release_callback = Event()

    def event_runner(prompt, project_root, **kwargs):
        factory = kwargs["event_factory"]
        created_result = write_file(
            project_root,
            "concurrent.txt",
            "created by active turn\n",
        )
        yield factory.create(
            EventType.TOOL_FINISHED,
            {
                "tool_name": "write_file",
                "call_id": "create-concurrent",
                "ok": True,
                "result": created_result,
            },
        )
        yield factory.create(
            EventType.ASSISTANT_MESSAGE_COMPLETED,
            {"text": "This result should be cancelled."},
        )

    controller = TextualChatController(
        tmp_path,
        AppConfig(),
        DenyApprovalProvider(),
        event_runner=event_runner,
    )

    def observe(event):
        if event.payload.get("call_id") == "create-concurrent":
            (tmp_path / "concurrent.txt").write_text(
                "changed outside LunarForge\n",
                encoding="utf-8",
            )
            callback_reached.set()
            assert release_callback.wait(2)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            controller.send_turn,
            "Create a tracked file.",
            event_callback=observe,
        )
        assert callback_reached.wait(1)
        assert controller.request_active_turn_finish() is True
        release_callback.set()
        with pytest.raises(ChatTurnCancelled) as cancelled:
            future.result(timeout=3)

    summary = cancelled.value.summary
    assert summary.complete is False
    assert summary.removed_files == ()
    assert summary.skipped_files == (
        "concurrent.txt (changed after LunarForge's write)",
    )
    assert (tmp_path / "concurrent.txt").read_text(encoding="utf-8") == (
        "changed outside LunarForge\n"
    )


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


def test_textual_renderer_derives_phase_aware_public_progress():
    factory = EventFactory(
        session_id="session_phases",
        turn_id="turn_phases",
    )
    renderer = TextualEventRenderer()

    turn = renderer.handle(
        factory.create(
            EventType.TURN_STARTED,
            {"request": "Implement a pricing page."},
        )
    )
    generic = renderer.handle(
        factory.create(
            EventType.STATUS_UPDATED,
            {"message": "Working..."},
        )
    )
    planner = renderer.handle(
        factory.create(
            EventType.MODEL_CALL_STARTED,
            {"phase": "planner"},
        )
    )
    writer = renderer.handle(
        factory.create(
            EventType.TOOL_STARTED,
            {"tool_name": "write_file"},
        )
    )
    validation = renderer.handle(
        factory.create(EventType.VALIDATION_STARTED, {})
    )
    browser = renderer.handle(
        factory.create(EventType.BROWSER_STARTED, {})
    )
    git = renderer.handle(
        factory.create(EventType.GIT_PROPOSAL, {})
    )
    external = renderer.handle(
        factory.create(
            EventType.TOOL_STARTED,
            {"tool_name": "web_design.review_files"},
        )
    )
    compaction = renderer.handle(
        factory.create(EventType.MEMORY_COMPACTION_STARTED, {})
    )
    cancelled = renderer.handle(
        factory.create(EventType.TURN_CANCELLED, {})
    )
    rollback = renderer.handle(
        factory.create(
            EventType.ROLLBACK_FINISHED,
            {
                "restored_files": ["one.txt"],
                "removed_files": ["two.txt"],
            },
        )
    )
    waiting = renderer.handle(
        factory.create(
            EventType.PERMISSION_REQUESTED,
            {
                "command": "python --version",
                "tool_name": "run_command",
            },
        )
    )
    resolved = renderer.handle(
        factory.create(
            EventType.PERMISSION_RESOLVED,
            {"approved": True},
        )
    )

    assert turn.status == (
        "Planning how to implement the requested feature..."
    )
    assert generic is None
    assert planner.status == "Planning the implementation..."
    assert writer.status == "Applying project changes..."
    assert validation.status == "Running validation..."
    assert browser.status == "Checking the app in a browser..."
    assert git.status == "Preparing Git commit..."
    assert external.status == "Running external tool review..."
    assert compaction.status == "Compacting conversation context..."
    assert cancelled.status == "Finishing current task..."
    assert rollback.status == "Finishing current task..."
    assert rollback.tool_text == "Revoked 2 tracked file change(s)"
    assert waiting.status == "Waiting for approval"
    assert waiting.tool_text == "Command: python --version"
    assert waiting.approval_state == "requested"
    assert resolved.status is None
    assert resolved.approval_state == "approved"


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
