"""Dependency-free state, rendering, and approval helpers for Textual chat."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any

from lunar_forge.agent import run_agent_events
from lunar_forge.approvals import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
)
from lunar_forge.config import AppConfig
from lunar_forge.events import AgentEvent, EventFactory, EventType
from lunar_forge.model_clients import ModelClient
from lunar_forge.runtime.sessions import (
    LoadedSession,
    SessionLogger,
    create_session_logger,
    project_fingerprint,
)


MAX_TEXTUAL_TOOL_LINE_CHARACTERS = 1_000
MAX_TEXTUAL_STATUS_CHARACTERS = 500
MAX_TEXTUAL_TRANSCRIPT_CHARACTERS = 50_000
_SESSION_NOTE_PATTERN = re.compile(r"\n\nSession log: [^\n]+\Z")

AgentEventCallback = Callable[[AgentEvent], None]
AgentEventRunner = Callable[..., Iterable[AgentEvent]]
ApprovalNotifier = Callable[[ApprovalRequest], None]


@dataclass(frozen=True, slots=True)
class TextualRenderUpdate:
    """One plain-text UI update derived from a public agent event."""

    transcript_role: str | None = None
    transcript_text: str | None = None
    status: str | None = None
    tool_text: str | None = None


class TextualEventRenderer:
    """Convert public events to bounded, markup-free Textual view updates."""

    def handle(self, event: AgentEvent) -> TextualRenderUpdate | None:
        payload = event.payload
        if event.type == EventType.SESSION_STARTED.value:
            return TextualRenderUpdate(status="Session started")
        if event.type == EventType.SESSION_RESUMED.value:
            source_session_id = _first_text(
                payload,
                "source_session_id",
                "source_session",
            )
            return TextualRenderUpdate(
                status=_bounded_text(
                    (
                        f"Session resumed from {source_session_id}"
                        if source_session_id
                        else "Session resumed"
                    ),
                    MAX_TEXTUAL_STATUS_CHARACTERS,
                )
            )
        if event.type == EventType.TURN_STARTED.value:
            return TextualRenderUpdate(status="Working...")
        if event.type == EventType.STATUS_UPDATED.value:
            return TextualRenderUpdate(
                status=_bounded_text(
                    _first_text(payload, "message", "status", "state")
                    or "Working...",
                    MAX_TEXTUAL_STATUS_CHARACTERS,
                )
            )
        if event.type == EventType.TOOL_STARTED.value:
            return TextualRenderUpdate(
                tool_text=_bounded_text(
                    f"{_tool_name(payload)} · started",
                    MAX_TEXTUAL_TOOL_LINE_CHARACTERS,
                )
            )
        if event.type == EventType.TOOL_FINISHED.value:
            return TextualRenderUpdate(
                tool_text=_bounded_text(
                    f"{_tool_name(payload)} · completed",
                    MAX_TEXTUAL_TOOL_LINE_CHARACTERS,
                )
            )
        if event.type == EventType.TOOL_FAILED.value:
            error = _first_text(payload, "error", "message")
            suffix = f" · {error}" if error else ""
            return TextualRenderUpdate(
                tool_text=_bounded_text(
                    f"{_tool_name(payload)} · failed{suffix}",
                    MAX_TEXTUAL_TOOL_LINE_CHARACTERS,
                )
            )
        if event.type == EventType.PERMISSION_REQUESTED.value:
            return TextualRenderUpdate(status="Approval required")
        if event.type == EventType.PERMISSION_RESOLVED.value:
            approved = payload.get("approved") is True
            return TextualRenderUpdate(
                status="Approval granted" if approved else "Approval denied"
            )
        if event.type == EventType.ASSISTANT_MESSAGE_COMPLETED.value:
            text = _first_text(payload, "text", "message") or ""
            return TextualRenderUpdate(
                transcript_role="assistant",
                transcript_text=_bounded_text(
                    _conversation_text(text),
                    MAX_TEXTUAL_TRANSCRIPT_CHARACTERS,
                ),
                status="Ready",
            )
        if event.type == EventType.ERROR.value:
            message = _first_text(payload, "message", "error")
            return TextualRenderUpdate(
                transcript_role="error",
                transcript_text=_bounded_text(
                    message or "Unknown agent error.",
                    MAX_TEXTUAL_TRANSCRIPT_CHARACTERS,
                ),
                status="Turn failed",
            )
        if event.type == EventType.TURN_FINISHED.value:
            if payload.get("status") == "failed":
                return TextualRenderUpdate(status="Turn failed")
            return TextualRenderUpdate(status="Ready")
        return None


@dataclass(slots=True)
class _PendingApproval:
    request: ApprovalRequest
    ready: Event
    decision: ApprovalDecision | None = None


class TextualApprovalBridge:
    """Block an agent worker while the Textual thread resolves one request."""

    def __init__(
        self,
        notifier: ApprovalNotifier,
        *,
        wait_timeout_seconds: float | None = None,
    ) -> None:
        self._notifier = notifier
        self._wait_timeout_seconds = wait_timeout_seconds
        self._lock = Lock()
        self._pending: _PendingApproval | None = None

    @property
    def pending_request(self) -> ApprovalRequest | None:
        with self._lock:
            return self._pending.request if self._pending is not None else None

    def resolve(self, request: ApprovalRequest) -> ApprovalDecision:
        pending = _PendingApproval(request=request, ready=Event())
        with self._lock:
            if self._pending is not None:
                return ApprovalDecision.create(
                    request.id,
                    approved=False,
                    reason="Another Textual approval is already pending.",
                    source="textual",
                )
            self._pending = pending
        try:
            self._notifier(request)
        except Exception:
            self._finish_pending(pending)
            return ApprovalDecision.create(
                request.id,
                approved=False,
                reason="The Textual approval prompt could not be displayed.",
                source="textual",
            )

        ready = pending.ready.wait(self._wait_timeout_seconds)
        if not ready:
            self._finish_pending(pending)
            return ApprovalDecision.create(
                request.id,
                approved=False,
                reason="The Textual approval request timed out.",
                source="textual",
            )
        decision = pending.decision
        if decision is None:
            return ApprovalDecision.create(
                request.id,
                approved=False,
                reason="The Textual approval request was cancelled.",
                source="textual",
            )
        return decision

    def approve(self, reason: str = "Approved in Textual UI.") -> bool:
        return self._decide(approved=True, reason=reason)

    def deny(self, reason: str = "Denied in Textual UI.") -> bool:
        return self._decide(approved=False, reason=reason)

    def cancel_pending(self) -> bool:
        return self._decide(
            approved=False,
            reason="Textual chat closed before approval was resolved.",
        )

    def _decide(self, *, approved: bool, reason: str) -> bool:
        with self._lock:
            pending = self._pending
            if pending is None:
                return False
            pending.decision = ApprovalDecision.create(
                pending.request.id,
                approved=approved,
                reason=reason,
                source="textual",
            )
            self._pending = None
            pending.ready.set()
        return True

    def _finish_pending(self, pending: _PendingApproval) -> None:
        with self._lock:
            if self._pending is pending:
                self._pending = None


@dataclass(frozen=True, slots=True)
class SlashCommandResult:
    handled: bool
    message: str | None = None
    clear_transcript: bool = False
    exit_app: bool = False


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    final_text: str
    events: tuple[AgentEvent, ...]
    session_id: str
    turn_id: str


class TextualChatController:
    """Synchronous multi-turn controller used from a Textual thread worker."""

    def __init__(
        self,
        project_root: str | Path,
        config: AppConfig,
        approval_provider: ApprovalProvider,
        *,
        previous_session: LoadedSession | None = None,
        model_client: ModelClient | None = None,
        event_runner: AgentEventRunner = run_agent_events,
        session_logger: SessionLogger | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        if not self.project_root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {self.project_root}"
            )
        if (
            previous_session is not None
            and previous_session.project_root.resolve() != self.project_root
        ):
            raise ValueError(
                "Resumed session belongs to another project and cannot be "
                "opened in this chat."
            )
        if previous_session is not None and len(previous_session.messages) <= 1:
            raise ValueError(
                "Session is incompatible with chat resume: it contains no "
                "safe conversation context."
            )
        self.config = config
        self.approval_provider = approval_provider
        self.model_client = model_client
        self._event_runner = event_runner
        self._turn_lock = Lock()
        self._messages: list[dict[str, str]] = [
            dict(message) for message in (
                previous_session.messages if previous_session is not None else ()
            )
        ]
        self._resumed_from_path = (
            previous_session.relative_path
            if previous_session is not None
            else None
        )
        self._resumed_from_session_id = (
            previous_session.session_id
            if previous_session is not None
            else None
        )
        self._resumed_context_messages = (
            len(previous_session.messages)
            if previous_session is not None
            else 0
        )
        self._session_started = False
        self.turn_count = 0
        self.session_logger = (
            None
            if config.permissions.mode.strip().lower() == "plan"
            else (
                session_logger
                if session_logger is not None
                else create_session_logger(self.project_root)
            )
        )
        if (
            self.session_logger is not None
            and self.session_logger.project_root.resolve() != self.project_root
        ):
            raise ValueError(
                "Chat session logger belongs to another project."
            )
        self.event_factory = EventFactory(
            session_id=_chat_session_id(self.session_logger),
        )
        if self.session_logger is not None:
            self.session_logger.log(
                "session_started",
                session_id=self.session_id,
                project_fingerprint=project_fingerprint(self.project_root),
                runtime_mode=self.config.runtime.mode,
                permission_mode=self.config.permissions.mode,
                resumed_session_id=self._resumed_from_session_id,
                resumed_session=self._resumed_from_path,
            )

    @property
    def session_id(self) -> str:
        return self.event_factory.session_id

    @property
    def session_path(self) -> str:
        if self.session_logger is None:
            return "disabled in plan mode"
        return self.session_logger.relative_path

    @property
    def resumed_session_id(self) -> str | None:
        return self._resumed_from_session_id

    @property
    def resumed_session_path(self) -> str | None:
        return self._resumed_from_path

    @property
    def resume_notice(self) -> str | None:
        if self._resumed_from_session_id is None:
            return None
        return (
            f"Resumed safe conversation context from "
            f"{self._resumed_from_session_id}. Historical tool calls were not "
            "replayed, and prior approvals were not reused."
        )

    @property
    def conversation_messages(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(message) for message in self._messages)

    @property
    def footer_text(self) -> str:
        return (
            f"project={self.project_root} | "
            f"model={self.config.model.model} | "
            f"reasoning={self.config.model.reasoning.effort} | "
            f"runtime={self.config.runtime.mode} | "
            f"permissions={self.config.permissions.mode} | "
            f"session={self.session_id}"
        )

    def status_text(self) -> str:
        lines = [
            f"Project: {self.project_root}",
            f"Model: {self.config.model.model}",
            f"Reasoning effort: {self.config.model.reasoning.effort}",
            f"Runtime mode: {self.config.runtime.mode}",
            f"Permission mode: {self.config.permissions.mode}",
            f"Session: {self.session_id}",
            f"Session log: {self.session_path}",
            f"Completed turns: {self.turn_count}",
        ]
        if self._resumed_from_session_id is not None:
            lines.extend(
                (
                    f"Resumed from session: {self._resumed_from_session_id}",
                    f"Resumed source log: {self._resumed_from_path}",
                    (
                        "Historical context messages: "
                        f"{self._resumed_context_messages}"
                    ),
                )
            )
        return "\n".join(lines)

    def handle_slash_command(self, value: str) -> SlashCommandResult:
        command = value.strip().casefold()
        if not command.startswith("/"):
            return SlashCommandResult(handled=False)
        if command == "/help":
            return SlashCommandResult(
                handled=True,
                message=(
                    "Commands: /help, /status, /clear, /exit"
                ),
            )
        if command == "/status":
            return SlashCommandResult(
                handled=True,
                message=self.status_text(),
            )
        if command == "/clear":
            return SlashCommandResult(
                handled=True,
                message="Transcript cleared; conversation context is retained.",
                clear_transcript=True,
            )
        if command == "/exit":
            return SlashCommandResult(handled=True, exit_app=True)
        return SlashCommandResult(
            handled=True,
            message=f"Unknown command: {value.strip()}. Use /help.",
        )

    def send_turn(
        self,
        prompt: str,
        *,
        event_callback: AgentEventCallback | None = None,
    ) -> ChatTurnResult:
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("Chat prompt must not be empty.")
        if normalized_prompt.startswith("/"):
            raise ValueError("Slash commands must be handled before agent turns.")

        with self._turn_lock:
            turn_id = self.event_factory.begin_turn()
            prior_messages = tuple(self._messages)
            collected: list[AgentEvent] = []
            final_text: str | None = None
            try:
                events = self._event_runner(
                    normalized_prompt,
                    self.project_root,
                    config=self.config,
                    mode=self.config.permissions.mode,
                    model_client=self.model_client,
                    approval_provider=self.approval_provider,
                    resume_messages=prior_messages,
                    resumed_from=(
                        self._resumed_from_path
                        if not self._session_started
                        else None
                    ),
                    event_factory=self.event_factory,
                    session_logger=self.session_logger,
                    emit_session_started=not self._session_started,
                )
                for event in events:
                    collected.append(event)
                    if event_callback is not None:
                        event_callback(event)
                    if (
                        event.type
                        == EventType.ASSISTANT_MESSAGE_COMPLETED.value
                    ):
                        text = event.payload.get("text")
                        if isinstance(text, str):
                            final_text = _conversation_text(text)
            except Exception:
                self._messages.append(
                    {"role": "user", "content": normalized_prompt}
                )
                self._session_started = True
                raise

            if final_text is None:
                self._messages.append(
                    {"role": "user", "content": normalized_prompt}
                )
                self._session_started = True
                raise RuntimeError(
                    "Agent turn completed without a final assistant event."
                )

            self._messages.extend(
                (
                    {"role": "user", "content": normalized_prompt},
                    {"role": "assistant", "content": final_text},
                )
            )
            self._session_started = True
            self.turn_count += 1
            return ChatTurnResult(
                final_text=final_text,
                events=tuple(collected),
                session_id=self.session_id,
                turn_id=turn_id,
            )


def _chat_session_id(session: SessionLogger | None) -> str:
    if session is None:
        return EventFactory().session_id
    return f"session_{session.path.stem}"


def _conversation_text(text: str) -> str:
    return _SESSION_NOTE_PATTERN.sub("", text).strip()


def _tool_name(payload: Mapping[str, Any]) -> str:
    return _first_text(
        payload,
        "tool_name",
        "internal_tool_name",
        "name",
    ) or "unknown"


def _first_text(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _bounded_text(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    marker = "\n...[Textual display truncated]"
    return f"{value[: maximum - len(marker)]}{marker}"


__all__ = [
    "ChatTurnResult",
    "MAX_TEXTUAL_STATUS_CHARACTERS",
    "MAX_TEXTUAL_TOOL_LINE_CHARACTERS",
    "MAX_TEXTUAL_TRANSCRIPT_CHARACTERS",
    "SlashCommandResult",
    "TextualApprovalBridge",
    "TextualChatController",
    "TextualEventRenderer",
    "TextualRenderUpdate",
]
