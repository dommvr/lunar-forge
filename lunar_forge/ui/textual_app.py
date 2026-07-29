"""Optional Textual terminal chat application.

This module is imported lazily by the ``lunar-forge chat`` command so Textual
never becomes a core runtime dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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
)
from lunar_forge.ui.textual_state import ProjectConfigSaveResult
from lunar_forge.ui.textual_widgets import (
    MAX_TEXTUAL_TRANSCRIPT_CHARACTERS,
    TextualApprovalBridge,
    TextualChatController,
    TextualEventRenderer,
    TextualRenderUpdate,
)


MAX_CHAT_INPUT_CHARACTERS = 50_000


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
            "shift+enter",
            "insert_newline",
            "New line",
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

    def action_insert_newline(self) -> None:
        if len(self.text) >= MAX_CHAT_INPUT_CHARACTERS:
            self.post_message(
                self.LimitReached(
                    self,
                    original_characters=len(self.text) + 1,
                    accepted_characters=len(self.text),
                )
            )
            return
        if result := self._replace_via_keyboard("\n", *self.selection):
            self.move_cursor(result.end_location)

    async def _on_paste(self, event: events.Paste) -> None:
        """Preserve pasted newlines while bounding the inserted text."""
        if self.read_only:
            return
        available = max(
            0,
            MAX_CHAT_INPUT_CHARACTERS - len(self.text),
        )
        accepted = event.text[:available]
        if accepted:
            if result := self._replace_via_keyboard(
                accepted,
                *self.selection,
            ):
                self.move_cursor(result.end_location)
                self.focus()
        if len(accepted) < len(event.text):
            self.post_message(
                self.LimitReached(
                    self,
                    original_characters=len(event.text),
                    accepted_characters=len(accepted),
                )
            )


@dataclass(slots=True)
class _ProgressState:
    sentence: str
    started_at: float
    detail: str | None = None
    last_rendered_second: int = -1


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
    }

    #top-card {
        height: auto;
        max-height: 8;
        border: round $primary;
        padding: 0 1;
        margin-bottom: 1;
    }

    #transcript {
        height: 1fr;
        min-height: 8;
        padding: 0 1;
    }

    #approval-panel {
        height: auto;
        max-height: 10;
        border: heavy $warning;
        padding: 0 1;
    }

    #approval-actions {
        height: 3;
        align-horizontal: right;
    }

    #approval-actions Button {
        margin-left: 1;
    }

    #slash-popup {
        height: auto;
        max-height: 12;
        border: heavy $accent;
        padding: 0 1;
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

    #chat-input {
        height: 5;
        min-height: 3;
        max-height: 10;
        border: round $accent;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        project_root: str | Path,
        config: AppConfig,
        *,
        previous_session: LoadedSession | None = None,
        model_client: ModelClient | None = None,
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
        )
        self._event_renderer = TextualEventRenderer()
        self._turn_running = False
        self._transcript_entries: list[str] = []
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
        parts = list(self._transcript_entries)
        progress = self._format_progress()
        if progress is not None:
            parts.append(progress)
        return "\n\n".join(parts)

    def compose(self) -> ComposeResult:
        top_card = Static(self.top_card_text, id="top-card")
        top_card.border_title = self.top_card_title
        yield top_card
        yield RichLog(
            id="transcript",
            wrap=True,
            markup=False,
            highlight=False,
            max_lines=1_000,
        )
        with Vertical(id="approval-panel"):
            yield Label("Approval required", id="approval-title")
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
        yield ChatInput(
            placeholder=(
                "Message LunarForge · Enter sends · Shift+Enter adds a line"
            ),
            id="chat-input",
            soft_wrap=True,
            show_line_numbers=False,
            compact=True,
        )

    def on_mount(self) -> None:
        self.query_one("#approval-panel", Vertical).display = False
        self.query_one("#slash-popup", Vertical).display = False
        if self.controller.resume_notice is not None:
            self._write_transcript(
                "system",
                self.controller.resume_notice,
            )
        self.set_interval(0.25, self._refresh_progress_timer)
        self.query_one("#chat-input", ChatInput).focus()

    @on(ChatInput.Submitted, "#chat-input")
    def submit_chat_input(self, event: ChatInput.Submitted) -> None:
        raw_value = event.value
        value = raw_value.strip()
        event.input.load_text("")
        if not value:
            return

        if value.startswith("/"):
            slash = self.controller.handle_slash_command(value)
            if slash.handled:
                self._apply_slash_result(slash)
                return

        if self._turn_running:
            self._write_transcript(
                "system",
                "A turn is already running; wait for it to finish.",
            )
            return

        self._turn_running = True
        self._write_transcript("user", value)
        self._begin_progress("Working on your request.")
        self.query_one("#chat-input", ChatInput).disabled = True
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
        self._submit_slash_popup(save_to_project=False)

    @on(Button.Pressed, "#slash-save")
    def save_slash_popup(self, event: Button.Pressed) -> None:
        self._submit_slash_popup(save_to_project=True)

    @on(Input.Submitted, "#slash-value")
    def submit_slash_popup_value(self, event: Input.Submitted) -> None:
        self._submit_slash_popup(save_to_project=False)

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
        self._update_progress(
            sentence="Waiting for approval.",
            detail=progress_detail,
        )

    def _hide_approval(self) -> None:
        self.query_one("#approval-panel", Vertical).display = False
        self.query_one("#approval-details", Static).update("")
        self._update_progress(sentence="Continuing your request.")

    def _apply_slash_result(self, result: SlashCommandResult) -> None:
        if result.save_config_to_project:
            if result.config_update is None:
                self._write_transcript(
                    "error",
                    "This command did not provide a project config update.",
                )
                return
            self._config_save_in_progress = True
            self.query_one("#chat-input", ChatInput).disabled = True
            self.save_project_config(result)
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

    def _show_slash_popup(self, result: SlashCommandResult) -> None:
        self._pending_slash_result = result
        popup = self.query_one("#slash-popup", Vertical)
        title = self.query_one("#slash-title", Label)
        details = self.query_one("#slash-details", Static)
        value_input = self.query_one("#slash-value", Input)
        choice_input = self.query_one("#slash-choice", Select)
        apply_button = self.query_one("#slash-apply", Button)
        save_button = self.query_one("#slash-save", Button)
        if result.confirmation is not None:
            title.update(result.confirmation.title)
            details.update(result.confirmation.message)
            value_input.display = False
            choice_input.display = False
            apply_button.label = result.confirmation.confirm_label
            save_button.display = False
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
        popup.display = True
        if choice_input.display:
            choice_input.focus()
        elif value_input.display:
            value_input.focus()
        else:
            apply_button.focus()

    def _hide_slash_popup(self) -> None:
        self._pending_slash_result = None
        self.query_one("#slash-popup", Vertical).display = False
        self.query_one("#slash-value", Input).value = ""
        self.query_one("#slash-choice", Select).display = False
        self.query_one("#chat-input", ChatInput).focus()

    def _submit_slash_popup(self, *, save_to_project: bool) -> None:
        pending = self._pending_slash_result
        if pending is None:
            return
        if save_to_project:
            self._start_project_config_save(pending)
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

    def _start_project_config_save(
        self,
        pending: SlashCommandResult,
    ) -> None:
        if pending.form is None or not pending.form.config_backed:
            self._write_transcript(
                "error",
                "This command cannot be saved to project config.",
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
        self._config_save_in_progress = True
        self.query_one("#chat-input", ChatInput).disabled = True
        self.save_project_config(result)

    @work(thread=True)
    def save_project_config(self, result: SlashCommandResult) -> None:
        try:
            saved = self.controller.save_project_config_update(
                result.config_update
            )
        except (OSError, ValueError) as exc:
            self.call_from_thread(
                self._finish_project_config_save,
                result,
                None,
                str(exc),
            )
            return
        self.call_from_thread(
            self._finish_project_config_save,
            result,
            saved,
            None,
        )

    def _finish_project_config_save(
        self,
        result: SlashCommandResult,
        saved: ProjectConfigSaveResult | None,
        error: str | None,
    ) -> None:
        self._config_save_in_progress = False
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.disabled = False
        chat_input.focus()
        if error is not None or saved is None:
            self._write_transcript(
                "error",
                f"Could not save project config: {error or 'unknown error'}",
            )
            return
        if result.message is not None:
            result = replace(
                result,
                message=result.message.replace(" (session only)", ""),
                save_config_to_project=False,
            )
        else:
            result = replace(result, save_config_to_project=False)
        self._apply_slash_result(result)
        relative_path = saved.path.relative_to(
            self.controller.project_root
        ).as_posix()
        message = f"Saved setting to {relative_path}."
        if saved.checkpoint_path is not None:
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
        self._begin_progress("Running LunarForge action.")
        self.query_one("#chat-input", ChatInput).disabled = True
        self.run_slash_action_command(request)

    @work(thread=True, exclusive=True)
    def run_slash_action_command(
        self,
        request: SlashActionRequest,
    ) -> None:
        try:
            outcome = self.controller.run_slash_action(request)
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
        self._begin_progress("Creating the starter project.")
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
        top_card.border_title = self.top_card_title

    def _handle_agent_event(self, event: AgentEvent) -> None:
        update = self._event_renderer.handle(event)
        if update is None:
            return
        self._apply_render_update(update)

    def _apply_render_update(self, update: TextualRenderUpdate) -> None:
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
        if update.status and not terminal_role:
            if update.status in {"Ready", "Turn failed"}:
                self._end_progress()
            else:
                self._update_progress(sentence=update.status)
        if update.tool_text:
            self._update_progress(detail=update.tool_text)

    def _write_transcript(self, role: str, text: str) -> None:
        label = {
            "user": "You",
            "assistant": "LunarForge",
            "error": "Error",
            "system": "System",
        }.get(role, role.title())
        safe_text = _bounded_display_text(text)
        self._transcript_entries.append(f"{label}\n{safe_text}")
        self._render_transcript()

    def _render_transcript(self) -> None:
        transcript = self.query_one("#transcript", RichLog)
        transcript.clear()
        parts = list(self._transcript_entries)
        progress = self._format_progress()
        if progress is not None:
            parts.append(progress)
        for index, part in enumerate(parts):
            if index:
                transcript.write("")
            transcript.write(part)

    def _begin_progress(self, sentence: str) -> None:
        self._progress = _ProgressState(
            sentence=_progress_sentence(sentence),
            started_at=monotonic(),
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
            self._progress = _ProgressState(
                sentence="Working on your request.",
                started_at=monotonic(),
            )
        if sentence:
            self._progress.sentence = _progress_sentence(sentence)
        if detail:
            self._progress.detail = _bounded_display_text(
                detail,
                maximum=1_000,
            )
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
        lines = [self._progress.sentence]
        if self._progress.detail:
            lines.append(self._progress.detail)
        lines.append(f"Elapsed: {_elapsed_clock(elapsed)}")
        return "\n".join(lines)

    def _refresh_progress_timer(self) -> None:
        if self._progress is None:
            return
        elapsed_seconds = int(
            max(0.0, monotonic() - self._progress.started_at)
        )
        if elapsed_seconds == self._progress.last_rendered_second:
            return
        self._progress.last_rendered_second = elapsed_seconds
        self._render_transcript()

    def _show_turn_error(self, message: str) -> None:
        self._end_progress()
        self._write_transcript("error", message)

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


def _progress_sentence(value: str) -> str:
    sentence = _bounded_display_text(value.strip(), maximum=500)
    if not sentence:
        return "Working on your request."
    if sentence.endswith(("...", ".", "!", "?")):
        return sentence
    return f"{sentence}."


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
    "format_elapsed_time",
    "run_textual_chat",
]
