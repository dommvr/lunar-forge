"""Optional Textual terminal chat application.

This module is imported lazily by the ``lunar-forge chat`` command so Textual
never becomes a core runtime dependency.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import (
    Button,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TextArea,
)

from lunar_forge import __version__
from lunar_forge.approvals import ApprovalRequest, TextualApprovalProvider
from lunar_forge.config import AppConfig
from lunar_forge.events import AgentEvent
from lunar_forge.model_clients import ModelClient
from lunar_forge.runtime.sessions import LoadedSession
from lunar_forge.ui.slash_commands import (
    SlashActionRequest,
    SlashCommandResult,
    slash_command_hints,
)
from lunar_forge.ui.textual_state import ProjectConfigSaveResult
from lunar_forge.ui.textual_widgets import (
    MAX_TEXTUAL_TRANSCRIPT_CHARACTERS,
    ChatTurnCancelled,
    TextualApprovalBridge,
    TextualChatController,
    TextualEventRenderer,
    TextualRenderUpdate,
)


MAX_CHAT_INPUT_CHARACTERS = 50_000
PROGRESS_REFRESH_SECONDS = 0.25
ELLIPSIS_TICK_SECONDS = 2.0 / 3.0
TRANSCRIPT_LABEL_STYLES = {
    "user": "bold cyan",
    "assistant": "bold green",
    "error": "bold red",
    "system": "dim",
}


class ChatInput(TextArea):
    """Bounded multiline input with chat-oriented submit behavior."""

    BINDINGS = [
        Binding(
            "enter",
            "submit",
            "Send",
            show=False,
            priority=True,
        ),
        Binding(
            "ctrl+v",
            "paste",
            "Paste",
            show=False,
            priority=True,
        ),
        Binding(
            "escape",
            "dismiss_hints",
            "Dismiss hints",
            show=False,
            priority=True,
        ),
    ]

    class Submitted(Message):
        """The user submitted the current chat input."""

        def __init__(self, input_widget: ChatInput, value: str) -> None:
            super().__init__()
            self.input = input_widget
            self.value = value

        @property
        def control(self) -> ChatInput:
            return self.input

    class LimitReached(Message):
        """A paste or submission exceeded the safe input bound."""

        def __init__(
            self,
            input_widget: ChatInput,
            *,
            original_characters: int,
            accepted_characters: int,
        ) -> None:
            super().__init__()
            self.input = input_widget
            self.original_characters = original_characters
            self.accepted_characters = accepted_characters

        @property
        def control(self) -> ChatInput:
            return self.input

    class DismissHints(Message):
        """The user dismissed slash-command hints."""

        def __init__(self, input_widget: ChatInput) -> None:
            super().__init__()
            self.input = input_widget

        @property
        def control(self) -> ChatInput:
            return self.input

    class ClipboardUnavailable(Message):
        """Neither Textual nor the OS exposed clipboard text."""

        def __init__(self, input_widget: ChatInput) -> None:
            super().__init__()
            self.input = input_widget

        @property
        def control(self) -> ChatInput:
            return self.input

    def action_submit(self) -> None:
        value = self.text
        if len(value) > MAX_CHAT_INPUT_CHARACTERS:
            original_characters = len(value)
            value = value[:MAX_CHAT_INPUT_CHARACTERS]
            self.load_text(value)
            self.post_message(
                self.LimitReached(
                    self,
                    original_characters=original_characters,
                    accepted_characters=len(value),
                )
            )
        if value.strip():
            self.post_message(self.Submitted(self, value))

    def action_paste(self) -> None:
        """Paste Textual's clipboard without submitting the chat input."""

        clipboard_text = self.app.clipboard or _read_system_clipboard_text()
        if clipboard_text:
            self._insert_paste_text(clipboard_text)
            return
        self.post_message(self.ClipboardUnavailable(self))

    def action_dismiss_hints(self) -> None:
        self.post_message(self.DismissHints(self))

    async def _on_key(self, event: events.Key) -> None:
        """Make unmodified Enter submission deterministic."""

        if event.key in {"enter", "return"}:
            event.stop()
            event.prevent_default()
            self.action_submit()
            return
        await super()._on_key(event)

    async def _on_paste(self, event: events.Paste) -> None:
        """Preserve pasted newlines while bounding the inserted text."""
        event.stop()
        event.prevent_default()
        self._insert_paste_text(event.text)

    def _insert_paste_text(self, pasted_text: str) -> None:
        if self.read_only or not pasted_text:
            return
        available = max(
            0,
            MAX_CHAT_INPUT_CHARACTERS - len(self.text),
        )
        accepted = pasted_text[:available]
        if accepted:
            if result := self._replace_via_keyboard(
                accepted,
                *self.selection,
            ):
                self.move_cursor(result.end_location)
                self.focus()
        if len(accepted) < len(pasted_text):
            self.post_message(
                self.LimitReached(
                    self,
                    original_characters=len(pasted_text),
                    accepted_characters=len(accepted),
                )
            )


@dataclass(slots=True)
class _ProgressState:
    sentence: str
    started_at: float
    detail: str | None = None
    last_rendered_second: int = -1
    animate_ellipsis: bool = False
    ellipsis_frame: int = 0
    waiting_for_approval: bool = False
    suspended_sentence: str | None = None
    suspended_detail: str | None = None
    suspended_animate_ellipsis: bool = False
    suspended_ellipsis_frame: int = 0


@dataclass(frozen=True, slots=True)
class _TranscriptEntry:
    role: str
    label: str
    text: str
    label_style: str

    @property
    def plain_text(self) -> str:
        return f"{self.label}:\n{self.text}"

    def renderable(self) -> Text:
        rendered = Text()
        rendered.append(f"{self.label}:", style=self.label_style)
        rendered.append("\n")
        rendered.append(self.text)
        return rendered


class LunarForgeTextualApp(App[None]):
    """A deliberately small, single-screen continuous chat UI."""

    TITLE = "LunarForge Chat"
    BINDINGS = [
        ("ctrl+c", "quit_chat", "Quit"),
        ("ctrl+l", "clear_transcript", "Clear"),
    ]
    CSS = """
    Screen {
        layout: vertical;
        padding: 0 1;
    }

    #chat-frame {
        height: 1fr;
        border: round $accent;
    }

    #top-card {
        height: auto;
        max-height: 8;
        border-bottom: solid $accent;
        padding: 0 1;
    }

    #transcript {
        height: 1fr;
        min-height: 8;
        padding: 0 1;
    }

    #chat-frame.slash-open #transcript {
        min-height: 2;
    }

    #approval-panel {
        height: 12;
        min-height: 7;
        max-height: 40%;
        border: heavy $warning;
        padding: 0 1;
    }

    #approval-title {
        height: 1;
    }

    #approval-details-scroll {
        height: 1fr;
        scrollbar-size-vertical: 1;
    }

    #approval-details {
        height: auto;
    }

    #approval-actions {
        height: 3;
        min-height: 3;
        align-horizontal: right;
    }

    #approval-actions Button {
        margin-left: 1;
    }

    #slash-popup {
        height: auto;
        max-height: 14;
        border: heavy $accent;
        padding: 0 1;
    }

    #slash-popup.long-session-list {
        max-height: 11;
    }

    #slash-popup.long-session-list #slash-details-scroll {
        height: 3;
        max-height: 3;
    }

    #slash-details-scroll {
        height: auto;
        max-height: 6;
        scrollbar-size-vertical: 1;
    }

    #slash-details {
        height: auto;
    }

    #slash-value {
        margin-top: 1;
    }

    #slash-choice {
        margin-top: 1;
    }

    #slash-actions {
        height: 3;
        align-horizontal: right;
    }

    #slash-actions Button {
        margin-left: 1;
    }

    #slash-hints {
        height: auto;
        max-height: 8;
        border: round $secondary;
        padding: 0 1;
    }

    #input-area {
        height: auto;
        min-height: 4;
        border-top: solid $accent;
        padding: 0 1;
    }

    #chat-input {
        height: 4;
        min-height: 3;
        max-height: 10;
        border: none;
    }
    """

    def __init__(
        self,
        project_root: str | Path,
        config: AppConfig,
        *,
        previous_session: LoadedSession | None = None,
        model_client: ModelClient | None = None,
        user_home: str | Path | None = None,
    ) -> None:
        super().__init__()
        self._approval_bridge = TextualApprovalBridge(
            self._notify_approval_from_worker
        )
        self.controller = TextualChatController(
            project_root,
            config,
            TextualApprovalProvider(self._approval_bridge.resolve),
            previous_session=previous_session,
            model_client=model_client,
            user_home=user_home,
        )
        self._event_renderer = TextualEventRenderer()
        self._turn_running = False
        self._transcript_entries: list[_TranscriptEntry] = []
        self._progress: _ProgressState | None = None
        self._pending_slash_result: SlashCommandResult | None = None
        self._config_save_in_progress = False

    @property
    def top_card_title(self) -> str:
        return f"LunarForge v{__version__}"

    @property
    def top_card_text(self) -> str:
        greeting = (
            "Let’s get back to building."
            if self.controller.resumed_session_id is not None
            else "What are we building today?"
        )
        return "\n".join(
            (
                greeting,
                "",
                f"Project: {self.controller.project_root}",
                f"Model: {self.controller.config.model.model}",
                (
                    "Reasoning effort: "
                    f"{self.controller.config.model.reasoning.effort}"
                ),
                (
                    f"Mode: {self.controller.config.runtime.mode} · "
                    f"permissions={self.controller.config.permissions.mode}"
                ),
            )
        )

    @property
    def progress_text(self) -> str | None:
        return self._format_progress()

    @property
    def transcript_plain_text(self) -> str:
        parts = [entry.plain_text for entry in self._transcript_entries]
        progress = self._format_progress()
        if progress is not None:
            parts.append(progress)
        return "\n\n".join(parts)

    def compose(self) -> ComposeResult:
        frame = Vertical(id="chat-frame")
        frame.border_title = self.top_card_title
        with frame:
            yield Static(self.top_card_text, id="top-card")
            yield RichLog(
                id="transcript",
                wrap=True,
                markup=False,
                highlight=False,
                max_lines=1_000,
            )
            with Vertical(id="approval-panel"):
                yield Label("Approval required", id="approval-title")
                with VerticalScroll(
                    id="approval-details-scroll",
                    can_focus=True,
                ):
                    yield Static("", id="approval-details")
                with Horizontal(id="approval-actions"):
                    yield Button(
                        "Deny",
                        id="approval-deny",
                        variant="error",
                    )
                    yield Button(
                        "Approve",
                        id="approval-approve",
                        variant="success",
                    )
            with Vertical(id="slash-popup"):
                yield Label("", id="slash-title")
                with VerticalScroll(
                    id="slash-details-scroll",
                    can_focus=True,
                ):
                    yield Static("", id="slash-details")
                yield Input("", id="slash-value")
                yield Select(
                    (("Select", ""),),
                    id="slash-choice",
                    allow_blank=False,
                    compact=True,
                )
                with Horizontal(id="slash-actions"):
                    yield Button("Cancel", id="slash-cancel")
                    yield Button(
                        "Apply to session",
                        id="slash-apply",
                        variant="primary",
                    )
                    yield Button(
                        "Save to project",
                        id="slash-save",
                        variant="success",
                    )
                    yield Button(
                        "Save to user",
                        id="slash-save-user",
                        variant="success",
                    )
            with Vertical(id="input-area"):
                yield Static("", id="slash-hints")
                yield ChatInput(
                    placeholder=(
                        "Message LunarForge · Enter sends · "
                        "multiline paste supported"
                    ),
                    id="chat-input",
                    soft_wrap=True,
                    show_line_numbers=False,
                    compact=True,
                )

    def on_mount(self) -> None:
        self.query_one("#approval-panel", Vertical).display = False
        self.query_one("#slash-popup", Vertical).display = False
        self.query_one("#slash-hints", Static).display = False
        if self.controller.resume_notice is not None:
            self._write_transcript(
                "system",
                self.controller.resume_notice,
            )
        self.set_interval(
            PROGRESS_REFRESH_SECONDS,
            self._refresh_progress_timer,
        )
        self.set_interval(
            ELLIPSIS_TICK_SECONDS,
            self._refresh_progress_ellipsis,
        )
        self.query_one("#chat-input", ChatInput).focus()

    @on(ChatInput.Submitted, "#chat-input")
    def submit_chat_input(self, event: ChatInput.Submitted) -> None:
        raw_value = event.value
        value = raw_value.strip()
        self._hide_slash_hints()
        if not value:
            return

        if value.startswith("/"):
            slash = self.controller.handle_slash_command(
                value,
                active_turn=self._turn_running,
            )
            if slash.handled:
                event.input.load_text("")
                self._apply_slash_result(slash)
                return

        if self._turn_running:
            self._write_transcript(
                "system",
                "A turn is already running. Use /finish to cancel it, or "
                "wait for it to complete.",
            )
            return

        event.input.load_text("")
        self._turn_running = True
        self._write_transcript("user", value)
        self._begin_progress(_initial_progress_for_prompt(value))
        self.run_agent_turn(value)

    @on(ChatInput.LimitReached, "#chat-input")
    def show_input_limit_warning(
        self,
        event: ChatInput.LimitReached,
    ) -> None:
        self._write_transcript(
            "system",
            (
                "Input was bounded to "
                f"{event.accepted_characters:,} characters "
                f"(received {event.original_characters:,})."
            ),
        )

    @on(ChatInput.ClipboardUnavailable, "#chat-input")
    def show_clipboard_unavailable(
        self,
        event: ChatInput.ClipboardUnavailable,
    ) -> None:
        self._write_transcript(
            "system",
            (
                "Clipboard text was unavailable. Use your terminal's paste "
                "action, then press Enter when the input is ready."
            ),
        )

    @on(ChatInput.Changed, "#chat-input")
    def update_slash_hints(self, event: ChatInput.Changed) -> None:
        self._update_slash_hints(event.text_area.text)

    @on(ChatInput.DismissHints, "#chat-input")
    def dismiss_slash_hints(self, event: ChatInput.DismissHints) -> None:
        self._hide_slash_hints()

    @work(thread=True, exclusive=True)
    def run_agent_turn(self, prompt: str) -> None:
        saw_error = False

        def forward(event: AgentEvent) -> None:
            nonlocal saw_error
            if event.type == "error":
                saw_error = True
            self.call_from_thread(self._handle_agent_event, event)

        try:
            self.controller.send_turn(prompt, event_callback=forward)
        except ChatTurnCancelled as exc:
            self.call_from_thread(
                self._show_cancelled_turn,
                exc.summary.display_text(),
            )
        except Exception as exc:
            if not saw_error:
                self.call_from_thread(self._show_turn_error, str(exc))
        finally:
            self.call_from_thread(self._finish_turn)

    @on(Button.Pressed, "#approval-approve")
    def approve_pending_request(self, event: Button.Pressed) -> None:
        if self._approval_bridge.approve():
            self._hide_approval()

    @on(Button.Pressed, "#approval-deny")
    def deny_pending_request(self, event: Button.Pressed) -> None:
        if self._approval_bridge.deny():
            self._hide_approval()

    @on(Button.Pressed, "#slash-cancel")
    def cancel_slash_popup(self, event: Button.Pressed) -> None:
        self._hide_slash_popup()

    @on(Button.Pressed, "#slash-apply")
    def apply_slash_popup(self, event: Button.Pressed) -> None:
        self._submit_slash_popup(scope="session")

    @on(Button.Pressed, "#slash-save")
    def save_slash_popup(self, event: Button.Pressed) -> None:
        self._submit_slash_popup(scope="project")

    @on(Button.Pressed, "#slash-save-user")
    def save_slash_popup_to_user(self, event: Button.Pressed) -> None:
        self._submit_slash_popup(scope="global")

    @on(Input.Submitted, "#slash-value")
    def submit_slash_popup_value(self, event: Input.Submitted) -> None:
        self._submit_slash_popup(scope="session")

    def action_clear_transcript(self) -> None:
        self._transcript_entries.clear()
        self._progress = None
        self._render_transcript()

    def action_quit_chat(self) -> None:
        self._approval_bridge.cancel_pending()
        self.exit()

    def on_unmount(self) -> None:
        self._approval_bridge.cancel_pending()

    def _notify_approval_from_worker(self, request: ApprovalRequest) -> None:
        self.call_from_thread(self._show_approval, request)

    def _show_approval(self, request: ApprovalRequest) -> None:
        self._hide_slash_hints()
        self.query_one("#approval-title", Label).update(request.title)
        details = (
            f"{request.summary}\n"
            f"Risk: {request.risk} · Mode: {request.mode}\n"
            f"{request.details}"
        )
        if len(details) > 4_000:
            details = f"{details[:3_960]}\n...[approval details truncated]"
        self.query_one("#approval-details", Static).update(details)
        self.query_one("#approval-panel", Vertical).display = True
        if request.command:
            progress_detail = f"Command: {request.command}"
        elif request.tool_name:
            progress_detail = f"Tool: {request.tool_name}"
        elif request.file_path:
            progress_detail = f"File: {request.file_path}"
        else:
            progress_detail = f"Approval: {request.summary}"
        self._enter_approval_progress(progress_detail)

    def _hide_approval(self) -> None:
        self.query_one("#approval-panel", Vertical).display = False
        self.query_one("#approval-details", Static).update("")
        self._restore_progress_after_approval()

    def _apply_slash_result(self, result: SlashCommandResult) -> None:
        if result.finish_task:
            self._request_finish_task()
            return
        if result.save_config_to_project or result.save_config_to_user:
            if result.config_update is None:
                self._write_transcript(
                    "error",
                    "This command did not provide a config update.",
                )
                return
            self._config_save_in_progress = True
            self._begin_progress("Saving configuration...")
            self.query_one("#chat-input", ChatInput).disabled = True
            scope = "global" if result.save_config_to_user else "project"
            self.save_config(result, scope)
            return
        if (
            result.form is not None
            or result.picker is not None
            or result.confirmation is not None
        ):
            self._show_slash_popup(result)
            return
        if result.new_project_prompt is not None:
            self._start_new_project(result.new_project_prompt)
            return
        if result.action is not None:
            self._start_slash_action(result.action)
            return
        if result.clear_transcript:
            self.action_clear_transcript()
        for role, text in result.restored_transcript:
            self._write_transcript(role, text)
        if result.refresh_header:
            self._refresh_top_card()
        if result.message:
            self._write_transcript(
                "error" if result.error else "system",
                result.message,
            )
        if result.exit_app:
            self.action_quit_chat()

    def _request_finish_task(self) -> None:
        if (
            not self._turn_running
            or not self.controller.request_active_turn_finish()
        ):
            self._write_transcript("system", "No active task to finish.")
            return
        self._approval_bridge.cancel_pending()
        approval_panel = self.query_one("#approval-panel", Vertical)
        if approval_panel.display:
            self._hide_approval()
        self._update_progress(
            sentence="Finishing current task...",
            detail=(
                "Cancelling active work and revoking current-turn changes"
            ),
        )

    def _show_slash_popup(self, result: SlashCommandResult) -> None:
        self._hide_slash_hints()
        self._pending_slash_result = result
        popup = self.query_one("#slash-popup", Vertical)
        title = self.query_one("#slash-title", Label)
        details = self.query_one("#slash-details", Static)
        details_scroll = self.query_one(
            "#slash-details-scroll",
            VerticalScroll,
        )
        value_input = self.query_one("#slash-value", Input)
        choice_input = self.query_one("#slash-choice", Select)
        apply_button = self.query_one("#slash-apply", Button)
        save_button = self.query_one("#slash-save", Button)
        save_user_button = self.query_one("#slash-save-user", Button)
        if result.confirmation is not None:
            title.update(result.confirmation.title)
            details.update(result.confirmation.message)
            value_input.display = False
            choice_input.display = False
            apply_button.label = result.confirmation.confirm_label
            save_button.display = False
            save_user_button.display = False
            value_input.value = ""
        elif result.picker is not None:
            title.update(result.picker.title)
            details.update(result.picker.prompt)
            choice_input.set_options(
                tuple(
                    (option.label, option.value)
                    for option in result.picker.options
                )
            )
            choice_input.value = result.picker.options[0].value
            choice_input.display = True
            value_input.display = False
            value_input.value = ""
            apply_button.label = result.picker.confirm_label
            save_button.display = False
            save_user_button.display = False
        elif result.form is not None:
            title.update(result.form.title)
            details.update(result.form.prompt)
            value_input.placeholder = result.form.placeholder
            value_input.value = ""
            if result.form.choices:
                choice_input.set_options(
                    tuple((choice, choice) for choice in result.form.choices)
                )
                choice_input.value = result.form.current_value
                choice_input.display = True
                value_input.display = False
            else:
                choice_input.display = False
                value_input.display = True
            apply_button.label = result.form.submit_label
            save_button.display = result.form.config_backed
            save_user_button.display = result.form.config_backed
        popup.display = True
        frame = self.query_one("#chat-frame", Vertical)
        frame.add_class("slash-open")
        popup.set_class(
            (
                result.picker is not None
                and result.picker.command == "resume"
                and len(result.picker.options) > 6
            ),
            "long-session-list",
        )
        details_scroll.scroll_home(animate=False)
        if choice_input.display:
            choice_input.focus()
        elif value_input.display:
            value_input.focus()
        else:
            apply_button.focus()

    def _hide_slash_popup(self) -> None:
        self._pending_slash_result = None
        popup = self.query_one("#slash-popup", Vertical)
        popup.display = False
        popup.remove_class("long-session-list")
        self.query_one("#chat-frame", Vertical).remove_class("slash-open")
        self.query_one("#slash-value", Input).value = ""
        self.query_one("#slash-choice", Select).display = False
        self.query_one("#chat-input", ChatInput).focus()

    def _submit_slash_popup(self, *, scope: str) -> None:
        pending = self._pending_slash_result
        if pending is None:
            return
        if scope in {"project", "global"}:
            self._start_config_save(pending, scope=scope)
            return
        if pending.confirmation is not None:
            result = self.controller.confirm_slash_command(
                pending.confirmation
            )
        elif pending.picker is not None:
            result = self.controller.submit_slash_picker(
                pending.picker,
                self._slash_form_value(pending),
            )
        elif pending.form is not None:
            result = self.controller.submit_slash_form(
                pending.form,
                self._slash_form_value(pending),
            )
        else:
            return
        if result.error:
            if result.message:
                self._write_transcript("error", result.message)
            return
        self._hide_slash_popup()
        self._apply_slash_result(result)

    def _start_config_save(
        self,
        pending: SlashCommandResult,
        *,
        scope: str,
    ) -> None:
        if pending.form is None or not pending.form.config_backed:
            self._write_transcript(
                "error",
                "This command cannot be saved to persistent config.",
            )
            return
        result = self.controller.validate_slash_form(
            pending.form,
            self._slash_form_value(pending),
        )
        if result.error or result.config_update is None:
            self._write_transcript(
                "error",
                result.message or "The selected config value is invalid.",
            )
            return
        self._hide_slash_popup()
        result = replace(
            result,
            save_config_to_project=scope == "project",
            save_config_to_user=scope == "global",
        )
        self._config_save_in_progress = True
        self._begin_progress("Saving configuration...")
        self.query_one("#chat-input", ChatInput).disabled = True
        self.save_config(result, scope)

    @work(thread=True)
    def save_config(self, result: SlashCommandResult, scope: str) -> None:
        try:
            if scope == "global":
                saved = self.controller.save_user_config_update(
                    result.config_update
                )
            else:
                saved = self.controller.save_project_config_update(
                    result.config_update
                )
        except (OSError, ValueError) as exc:
            self.call_from_thread(
                self._finish_config_save,
                result,
                None,
                str(exc),
                scope,
            )
            return
        self.call_from_thread(
            self._finish_config_save,
            result,
            saved,
            None,
            scope,
        )

    def _finish_config_save(
        self,
        result: SlashCommandResult,
        saved: ProjectConfigSaveResult | None,
        error: str | None,
        scope: str,
    ) -> None:
        self._config_save_in_progress = False
        self._end_progress()
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.disabled = False
        chat_input.focus()
        if error is not None or saved is None:
            self._write_transcript(
                "error",
                (
                    f"Could not save {'user' if scope == 'global' else 'project'} "
                    f"config: {error or 'unknown error'}"
                ),
            )
            return
        if result.message is not None:
            message = result.message
            for marker in (
                " (session only)",
                " (save to project requested)",
                " (save to user config requested)",
            ):
                message = message.replace(marker, "")
            result = replace(
                result,
                message=message,
                save_config_to_project=False,
                save_config_to_user=False,
            )
        else:
            result = replace(
                result,
                save_config_to_project=False,
                save_config_to_user=False,
            )
        self._apply_slash_result(result)
        display_path = (
            "~/.lunar-forge/config.yaml"
            if scope == "global"
            else saved.path.relative_to(
                self.controller.project_root
            ).as_posix()
        )
        message = f"Saved setting to {display_path}."
        if scope == "project" and saved.checkpoint_path is not None:
            checkpoint = saved.checkpoint_path.relative_to(
                self.controller.project_root
            ).as_posix()
            message = f"{message}\nCheckpoint: {checkpoint}"
        self._write_transcript("system", message)

    def _slash_form_value(self, pending: SlashCommandResult) -> str:
        if pending.picker is not None or (
            pending.form is not None and pending.form.choices
        ):
            selected = self.query_one("#slash-choice", Select).value
            if selected is Select.NULL:
                return ""
            return str(selected)
        return self.query_one("#slash-value", Input).value

    def _start_slash_action(self, request: SlashActionRequest) -> None:
        if self._turn_running:
            self._write_transcript(
                "system",
                "A turn is already running; wait for it to finish.",
            )
            return
        self._turn_running = True
        self._write_transcript("user", request.display)
        self._begin_progress(_slash_action_progress_status(request.name))
        self.query_one("#chat-input", ChatInput).disabled = True
        self.run_slash_action_command(request)

    @work(thread=True, exclusive=True)
    def run_slash_action_command(
        self,
        request: SlashActionRequest,
    ) -> None:
        def forward(event: AgentEvent) -> None:
            self.call_from_thread(self._handle_agent_event, event)

        try:
            outcome = self.controller.run_slash_action(
                request,
                event_callback=forward,
            )
        except Exception as exc:
            self.call_from_thread(
                self._finish_slash_action,
                None,
                str(exc),
            )
            return
        self.call_from_thread(
            self._finish_slash_action,
            outcome,
            None,
        )

    def _finish_slash_action(
        self,
        outcome: dict[str, object] | None,
        error: str | None,
    ) -> None:
        elapsed = self._end_progress()
        if error is not None or outcome is None:
            self._write_transcript(
                "error",
                f"LunarForge action failed: {error or 'unknown error'}",
            )
        else:
            text = str(outcome.get("text", "")).rstrip()
            text = (
                f"{text}\n\nDone in {format_elapsed_time(elapsed)}."
            )
            self._write_transcript(
                "system" if outcome.get("ok") is True else "error",
                text,
            )
        self._turn_running = False
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.disabled = False
        chat_input.focus()

    def _start_new_project(self, prompt: str) -> None:
        if self._turn_running:
            self._write_transcript(
                "system",
                "A turn is already running; wait for it to finish.",
            )
            return
        self._turn_running = True
        self._write_transcript("user", f"/new {prompt}")
        self._begin_progress("Planning how to build the new project...")
        self.query_one("#chat-input", ChatInput).disabled = True
        self.run_new_project_command(prompt)

    @work(thread=True, exclusive=True)
    def run_new_project_command(self, prompt: str) -> None:
        try:
            outcome = self.controller.run_new_project_workflow(prompt)
        except Exception as exc:
            self.call_from_thread(
                self._finish_new_project,
                None,
                str(exc),
            )
            return
        self.call_from_thread(
            self._finish_new_project,
            outcome,
            None,
        )

    def _finish_new_project(
        self,
        outcome: dict[str, object] | None,
        error: str | None,
    ) -> None:
        elapsed = self._end_progress()
        if error is not None or outcome is None:
            self._write_transcript(
                "error",
                f"New-project workflow failed: {error or 'unknown error'}",
            )
        else:
            text = str(outcome.get("text", "")).rstrip()
            text = (
                f"{text}\n\nDone in {format_elapsed_time(elapsed)}."
            )
            self._write_transcript(
                "assistant" if outcome.get("ok") is True else "error",
                text,
            )
        self._turn_running = False
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.disabled = False
        chat_input.focus()

    def _refresh_top_card(self) -> None:
        top_card = self.query_one("#top-card", Static)
        top_card.update(self.top_card_text)
        self.query_one("#chat-frame", Vertical).border_title = (
            self.top_card_title
        )

    def _handle_agent_event(self, event: AgentEvent) -> None:
        update = self._event_renderer.handle(event)
        if update is None:
            return
        self._apply_render_update(update)

    def _apply_render_update(self, update: TextualRenderUpdate) -> None:
        if update.approval_state == "requested":
            self._enter_approval_progress(update.tool_text)
        elif update.approval_state in {"approved", "denied"}:
            self._restore_progress_after_approval()
        terminal_role = update.transcript_role in {"assistant", "error"}
        if update.transcript_text:
            text = update.transcript_text
            if update.transcript_role == "assistant":
                elapsed = self._end_progress()
                text = (
                    f"{text.rstrip()}\n\n"
                    f"Done in {format_elapsed_time(elapsed)}."
                )
            elif update.transcript_role == "error":
                self._end_progress()
            self._write_transcript(
                update.transcript_role or "system",
                text,
            )
        if (
            update.status
            and not terminal_role
            and update.approval_state is None
        ):
            if update.status in {"Ready", "Turn failed"}:
                self._end_progress()
            else:
                self._update_progress(sentence=update.status)
        if update.tool_text and update.approval_state is None:
            self._update_progress(detail=update.tool_text)

    def _write_transcript(self, role: str, text: str) -> None:
        label = {
            "user": "You",
            "assistant": "LunarForge",
            "error": "Error",
            "system": "System",
        }.get(role, role.title())
        safe_text = _bounded_display_text(text)
        self._transcript_entries.append(
            _TranscriptEntry(
                role=role,
                label=label,
                text=safe_text,
                label_style=TRANSCRIPT_LABEL_STYLES.get(role, "bold"),
            )
        )
        self._render_transcript()

    def _render_transcript(self) -> None:
        transcript = self.query_one("#transcript", RichLog)
        transcript.clear()
        progress = self._format_progress()
        for index, entry in enumerate(self._transcript_entries):
            if index:
                transcript.write("")
            transcript.write(entry.renderable())
        if progress is not None:
            if self._transcript_entries:
                transcript.write("")
            transcript.write(progress)

    def _update_slash_hints(self, value: str) -> None:
        hints = self.query_one("#slash-hints", Static)
        if (
            self.query_one("#approval-panel", Vertical).display
            or self.query_one("#slash-popup", Vertical).display
            or not value.strip().startswith("/")
        ):
            hints.display = False
            hints.update("")
            return
        matches = slash_command_hints(value)
        hints.update(
            "Common commands:\n" + "\n".join(matches)
            if matches
            else "No matching commands."
        )
        hints.display = True

    def _hide_slash_hints(self) -> None:
        hints = self.query_one("#slash-hints", Static)
        hints.display = False
        hints.update("")

    def _begin_progress(self, sentence: str) -> None:
        normalized, animate = _progress_sentence_parts(sentence)
        self._progress = _ProgressState(
            sentence=normalized,
            started_at=monotonic(),
            animate_ellipsis=animate,
        )
        self._render_transcript()

    def _update_progress(
        self,
        *,
        sentence: str | None = None,
        detail: str | None = None,
    ) -> None:
        if self._progress is None:
            if not self._turn_running:
                return
            normalized, animate = _progress_sentence_parts(
                "Working on your request..."
            )
            self._progress = _ProgressState(
                sentence=normalized,
                started_at=monotonic(),
                animate_ellipsis=animate,
            )
        if self._progress.waiting_for_approval:
            if sentence:
                normalized, animate = _progress_sentence_parts(sentence)
                self._progress.suspended_sentence = normalized
                self._progress.suspended_animate_ellipsis = animate
                self._progress.suspended_ellipsis_frame = 0
            if detail:
                self._progress.suspended_detail = _bounded_display_text(
                    detail,
                    maximum=1_000,
                )
            return
        if sentence:
            normalized, animate = _progress_sentence_parts(sentence)
            self._progress.sentence = normalized
            self._progress.animate_ellipsis = animate
            self._progress.ellipsis_frame = 0
        if detail:
            self._progress.detail = _bounded_display_text(
                detail,
                maximum=1_000,
            )
        self._render_transcript()

    def _enter_approval_progress(self, detail: str | None) -> None:
        if self._progress is None:
            fallback = (
                "Saving configuration..."
                if self._config_save_in_progress
                else "Working on your request..."
            )
            self._begin_progress(fallback)
        progress = self._progress
        if progress is None:
            return
        if not progress.waiting_for_approval:
            progress.suspended_sentence = progress.sentence
            progress.suspended_detail = progress.detail
            progress.suspended_animate_ellipsis = (
                progress.animate_ellipsis
            )
            progress.suspended_ellipsis_frame = progress.ellipsis_frame
        sentence, animate = _progress_sentence_parts(
            "Waiting for approval"
        )
        progress.sentence = sentence
        progress.animate_ellipsis = animate
        progress.ellipsis_frame = 0
        progress.waiting_for_approval = True
        if detail:
            progress.detail = _bounded_display_text(
                detail,
                maximum=1_000,
            )
        self._render_transcript()

    def _restore_progress_after_approval(self) -> None:
        progress = self._progress
        if progress is None or not progress.waiting_for_approval:
            return
        if progress.suspended_sentence is None:
            if not self._turn_running and not self._config_save_in_progress:
                self._end_progress()
                return
            sentence, animate = _progress_sentence_parts(
                "Working on your request..."
            )
            progress.sentence = sentence
            progress.animate_ellipsis = animate
            progress.ellipsis_frame = 0
            progress.detail = None
        else:
            progress.sentence = progress.suspended_sentence
            progress.detail = progress.suspended_detail
            progress.animate_ellipsis = (
                progress.suspended_animate_ellipsis
            )
            progress.ellipsis_frame = (
                progress.suspended_ellipsis_frame
            )
        progress.waiting_for_approval = False
        progress.suspended_sentence = None
        progress.suspended_detail = None
        progress.suspended_animate_ellipsis = False
        progress.suspended_ellipsis_frame = 0
        self._render_transcript()

    def _end_progress(self) -> float:
        progress = self._progress
        self._progress = None
        self._render_transcript()
        if progress is None:
            return 0.0
        return max(0.0, monotonic() - progress.started_at)

    def _format_progress(self) -> str | None:
        if self._progress is None:
            return None
        elapsed = max(0.0, monotonic() - self._progress.started_at)
        sentence = self._progress.sentence
        if self._progress.animate_ellipsis:
            sentence = (
                f"{sentence}"
                f"{'.' * (self._progress.ellipsis_frame + 1)}"
            )
        lines = [sentence]
        if self._progress.detail:
            lines.append(self._progress.detail)
        lines.append(f"Elapsed: {_elapsed_clock(elapsed)}")
        return "\n".join(lines)

    def _advance_progress_animation(
        self,
        *,
        render: bool = True,
    ) -> bool:
        if self._progress is None or not self._progress.animate_ellipsis:
            return False
        self._progress.ellipsis_frame = (
            self._progress.ellipsis_frame + 1
        ) % 3
        if render:
            self._render_transcript()
        return True

    def _refresh_progress_timer(self) -> None:
        if self._progress is None:
            return
        elapsed_seconds = int(
            max(0.0, monotonic() - self._progress.started_at)
        )
        elapsed_changed = (
            elapsed_seconds != self._progress.last_rendered_second
        )
        if not elapsed_changed:
            return
        self._progress.last_rendered_second = elapsed_seconds
        self._render_transcript()

    def _refresh_progress_ellipsis(self) -> None:
        self._advance_progress_animation()

    def _show_turn_error(self, message: str) -> None:
        self._end_progress()
        self._write_transcript("error", message)

    def _show_cancelled_turn(self, message: str) -> None:
        self._end_progress()
        self._write_transcript("system", message)

    def _finish_turn(self) -> None:
        self._turn_running = False
        if self._progress is not None:
            self._end_progress()
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.disabled = False
        chat_input.focus()


def _bounded_display_text(
    value: str,
    *,
    maximum: int = MAX_TEXTUAL_TRANSCRIPT_CHARACTERS,
) -> str:
    if len(value) <= maximum:
        return value
    marker = "\n...[Textual display truncated]"
    return f"{value[: maximum - len(marker)]}{marker}"


def _progress_sentence_parts(value: str) -> tuple[str, bool]:
    sentence = _bounded_display_text(value.strip(), maximum=500)
    if not sentence:
        return "Working on your request", True
    if sentence.endswith("..."):
        base = sentence[:-3].rstrip()
        return (base or "Working on your request"), True
    if sentence.endswith("…"):
        base = sentence[:-1].rstrip()
        return (base or "Working on your request"), True
    if sentence.endswith((".", "!", "?")):
        return sentence, False
    return f"{sentence}.", False


def _initial_progress_for_prompt(prompt: str) -> str:
    normalized = prompt.casefold()
    requests_changes = not any(
        marker in normalized
        for marker in (
            "do not edit",
            "don't edit",
            "no edits",
            "without editing",
            "read-only",
            "readonly",
        )
    ) and any(
        marker in normalized
        for marker in (
            "implement",
            "add ",
            "build ",
            "create ",
            "edit ",
            "fix ",
            "refactor ",
            "update ",
            "change ",
        )
    )
    if requests_changes:
        return "Planning how to implement the requested feature..."
    if "plan" in normalized:
        return "Planning the implementation..."
    return "Gathering information about the project..."


def _slash_action_progress_status(action: str) -> str:
    normalized = action.casefold()
    if normalized == "memory.compact":
        return "Compacting conversation context..."
    if normalized.startswith("browser"):
        return "Checking the app in a browser..."
    if normalized.startswith("git."):
        return "Preparing Git commit..."
    if normalized.startswith(("mcp.", "plugins.")):
        return "Running external tool review..."
    if normalized == "rollback":
        return "Applying project changes..."
    return "Working on your request..."


def _read_system_clipboard_text() -> str | None:
    """Best-effort dependency-free clipboard fallback for Windows."""

    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            if not handle:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                value = ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
            return value or None
        finally:
            user32.CloseClipboard()
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _elapsed_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3_600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def format_elapsed_time(seconds: float) -> str:
    """Return a compact human-readable elapsed duration."""
    total = max(0, int(round(seconds)))
    if total < 1:
        return "less than 1 second"
    hours, remainder = divmod(total, 3_600)
    minutes, remaining_seconds = divmod(remainder, 60)
    parts: list[str] = []
    for value, singular in (
        (hours, "hour"),
        (minutes, "minute"),
        (remaining_seconds, "second"),
    ):
        if value:
            suffix = singular if value == 1 else f"{singular}s"
            parts.append(f"{value} {suffix}")
    return " ".join(parts)


def run_textual_chat(
    project_root: str | Path,
    config: AppConfig,
    *,
    previous_session: LoadedSession | None = None,
    model_client: ModelClient | None = None,
) -> None:
    """Launch the optional Textual app."""
    LunarForgeTextualApp(
        project_root,
        config,
        previous_session=previous_session,
        model_client=model_client,
    ).run()


__all__ = [
    "ChatInput",
    "LunarForgeTextualApp",
    "MAX_CHAT_INPUT_CHARACTERS",
    "TRANSCRIPT_LABEL_STYLES",
    "format_elapsed_time",
    "run_textual_chat",
]
