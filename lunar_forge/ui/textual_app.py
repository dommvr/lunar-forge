"""Optional Textual terminal chat application.

This module is imported lazily by the ``lunar-forge chat`` command so Textual
never becomes a core runtime dependency.
"""

from __future__ import annotations

from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, RichLog, Static

from lunar_forge.approvals import ApprovalRequest, TextualApprovalProvider
from lunar_forge.config import AppConfig
from lunar_forge.events import AgentEvent
from lunar_forge.model_clients import ModelClient
from lunar_forge.runtime.sessions import LoadedSession
from lunar_forge.ui.textual_widgets import (
    TextualApprovalBridge,
    TextualChatController,
    TextualEventRenderer,
    TextualRenderUpdate,
)


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

    #title {
        height: 1;
        padding: 0 1;
        text-style: bold;
        background: $primary;
        color: $text;
    }

    #transcript {
        height: 1fr;
        min-height: 8;
        border: round $primary;
        padding: 0 1;
    }

    #activity-row {
        height: 7;
    }

    #activity-panel {
        width: 1fr;
        border: round $secondary;
        padding: 0 1;
    }

    #tool-log {
        width: 2fr;
        border: round $secondary;
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

    #chat-input {
        height: 3;
        border: round $accent;
    }

    #metadata-footer {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
        overflow: hidden;
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

    def compose(self) -> ComposeResult:
        yield Static("LunarForge continuous chat", id="title")
        yield RichLog(
            id="transcript",
            wrap=True,
            markup=False,
            highlight=False,
        )
        with Horizontal(id="activity-row"):
            with Vertical(id="activity-panel"):
                yield Label("Activity")
                yield Static("Ready", id="activity-status")
            with Vertical():
                yield Label("Tools")
                yield RichLog(
                    id="tool-log",
                    wrap=True,
                    markup=False,
                    highlight=False,
                    max_lines=100,
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
        yield Input(
            placeholder="Message LunarForge or type /help",
            id="chat-input",
        )
        yield Static(self.controller.footer_text, id="metadata-footer")

    def on_mount(self) -> None:
        self.query_one("#approval-panel", Vertical).display = False
        self._write_transcript(
            "system",
            "Ready. Type /help for chat commands.",
        )
        if self.controller.resume_notice is not None:
            self._write_transcript(
                "system",
                self.controller.resume_notice,
            )
        self.query_one("#chat-input", Input).focus()

    @on(Input.Submitted, "#chat-input")
    def submit_chat_input(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        event.input.value = ""
        if not value:
            return

        slash = self.controller.handle_slash_command(value)
        if slash.handled:
            if slash.clear_transcript:
                self.action_clear_transcript()
            if slash.message:
                self._write_transcript("system", slash.message)
            if slash.exit_app:
                self.action_quit_chat()
            return

        if self._turn_running:
            self._write_transcript(
                "system",
                "A turn is already running; wait for it to finish.",
            )
            return

        self._turn_running = True
        self._write_transcript("user", value)
        self.query_one("#chat-input", Input).disabled = True
        self._set_status("Working...")
        self.run_agent_turn(value)

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
                self.call_from_thread(
                    self._write_transcript,
                    "error",
                    str(exc),
                )
                self.call_from_thread(self._set_status, "Turn failed")
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

    def action_clear_transcript(self) -> None:
        self.query_one("#transcript", RichLog).clear()

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
        self._set_status("Approval required")

    def _hide_approval(self) -> None:
        self.query_one("#approval-panel", Vertical).display = False
        self.query_one("#approval-details", Static).update("")
        self._set_status("Working...")

    def _handle_agent_event(self, event: AgentEvent) -> None:
        update = self._event_renderer.handle(event)
        if update is None:
            return
        self._apply_render_update(update)

    def _apply_render_update(self, update: TextualRenderUpdate) -> None:
        if update.transcript_text:
            self._write_transcript(
                update.transcript_role or "system",
                update.transcript_text,
            )
        if update.status:
            self._set_status(update.status)
        if update.tool_text:
            self.query_one("#tool-log", RichLog).write(update.tool_text)

    def _write_transcript(self, role: str, text: str) -> None:
        label = {
            "user": "You",
            "assistant": "LunarForge",
            "error": "Error",
            "system": "System",
        }.get(role, role.title())
        self.query_one("#transcript", RichLog).write(f"{label}: {text}")

    def _set_status(self, status: str) -> None:
        self.query_one("#activity-status", Static).update(status)

    def _finish_turn(self) -> None:
        self._turn_running = False
        chat_input = self.query_one("#chat-input", Input)
        chat_input.disabled = False
        chat_input.focus()


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


__all__ = ["LunarForgeTextualApp", "run_textual_chat"]
