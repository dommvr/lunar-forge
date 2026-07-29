"""Dependency-free state, rendering, and approval helpers for Textual chat."""

from __future__ import annotations

import json
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
from lunar_forge.config import AppConfig, load_config
from lunar_forge.events import AgentEvent, EventFactory, EventType
from lunar_forge.instructions import load_project_instructions
from lunar_forge.mcp.config import load_mcp_config
from lunar_forge.mcp.client import build_mcp_diagnostic
from lunar_forge.model_clients import ModelClient, create_model_client
from lunar_forge.plugins.loader import load_plugin_config
from lunar_forge.plugins.registry import build_plugin_diagnostic
from lunar_forge.permissions import PermissionLevel, PermissionManager
from lunar_forge.runtime.checkpoints import (
    list_checkpoint_directories,
    preview_rollback_file,
    rollback_file,
)
from lunar_forge.runtime.compaction import (
    CompactionError,
    ConversationCompactor,
    relevant_tool_results,
)
from lunar_forge.runtime.conversation import (
    ConversationMemory,
    should_compact,
)
from lunar_forge.runtime.git import (
    create_git_commit,
    format_git_commit_result,
    format_git_status,
    git_status,
    list_changed_files,
)
from lunar_forge.runtime.sessions import (
    LoadedSession,
    SessionLogger,
    create_session_logger,
    list_session_files,
    load_session,
    project_fingerprint,
)
from lunar_forge.ui.slash_commands import (
    SlashActionRequest,
    SlashCommandForm,
    SlashCommandPicker,
    SlashCommandResult,
    SlashCommandRouter,
    SlashConfirmation,
    SlashPickerOption,
)
from lunar_forge.tools.files import safe_path
from lunar_forge.workflows.browser_validation import (
    BROWSER_SETUP_COMMANDS,
    run_browser_setup,
    run_browser_validation,
    run_managed_browser_validation,
)
from lunar_forge.ui.textual_state import (
    ChatSessionState,
    ProjectConfigSaveResult,
    SessionConfigUpdate,
    persist_project_config_update,
)
from lunar_forge.workflows.new_project import (
    format_new_project_result,
    run_new_project,
    select_template,
)


MAX_TEXTUAL_TOOL_LINE_CHARACTERS = 1_000
MAX_TEXTUAL_STATUS_CHARACTERS = 500
MAX_TEXTUAL_TRANSCRIPT_CHARACTERS = 50_000
MAX_CHAT_COMPACTION_EVENTS = 500
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
                    _tool_progress_text(payload, "started"),
                    MAX_TEXTUAL_TOOL_LINE_CHARACTERS,
                )
            )
        if event.type == EventType.TOOL_FINISHED.value:
            return TextualRenderUpdate(
                tool_text=_bounded_text(
                    _tool_progress_text(payload, "completed"),
                    MAX_TEXTUAL_TOOL_LINE_CHARACTERS,
                )
            )
        if event.type == EventType.TOOL_FAILED.value:
            error = _first_text(payload, "error", "message")
            suffix = f" · {error}" if error else ""
            return TextualRenderUpdate(
                tool_text=_bounded_text(
                    f"{_tool_progress_text(payload, 'failed')}{suffix}",
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
        if event.type == EventType.MEMORY_COMPACTION_STARTED.value:
            return TextualRenderUpdate(status="Compacting conversation...")
        if event.type == EventType.MEMORY_COMPACTION_FINISHED.value:
            if payload.get("status") == "failed":
                warning = _first_text(payload, "warning", "message")
                return TextualRenderUpdate(
                    transcript_role="system",
                    transcript_text=_bounded_text(
                        warning or (
                            "Working-memory compaction failed; continuing "
                            "with the existing safe context."
                        ),
                        MAX_TEXTUAL_TRANSCRIPT_CHARACTERS,
                    ),
                    status="Compaction warning",
                )
            return TextualRenderUpdate(status="Conversation compacted")
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
        self.session_state = ChatSessionState.create(project_root, config)
        self.project_root = self.session_state.project_root
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
        self.approval_provider = approval_provider
        self.model_client = model_client
        self._event_runner = event_runner
        self._turn_lock = Lock()
        self._memory = ConversationMemory(
            previous_session.messages if previous_session is not None else ()
        )
        self._resumed_from_path = (
            previous_session.safe_display_path
            if previous_session is not None
            else None
        )
        self._resumed_from_session_id = (
            _safe_loaded_session_id(previous_session)
            if previous_session is not None
            else None
        )
        self._resumed_context_messages = (
            len(previous_session.messages)
            if previous_session is not None
            else 0
        )
        self._session_started = False
        self._session_events: list[AgentEvent] = []
        self._active_tool_calls: set[str] = set()
        self._pending_approvals: set[str] = set()
        self._changed_files: set[str] = set()
        self._validation_status: str | None = None
        self._validation_error: str | None = None
        self._compactor: ConversationCompactor | None = None
        self._compaction_count = 0
        self._last_compaction_summary_path: str | None = None
        self._last_compaction_warning: str | None = None
        self._session_picker_selections: dict[str, str] = {}
        self._seed_compaction_facts = (
            dict(previous_session.compacted_summaries[-1].get("facts", {}))
            if (
                previous_session is not None
                and previous_session.compacted_summaries
                and isinstance(
                    previous_session.compacted_summaries[-1].get("facts"),
                    Mapping,
                )
            )
            else {}
        )
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
            self._log_session_started()
        self.slash_router = SlashCommandRouter(
            self.session_state,
            status_provider=self.status_text,
            project_switcher=self._switch_project,
            session_picker_provider=self._session_picker,
            session_resumer=self._resume_session,
        )

    @property
    def config(self) -> AppConfig:
        return self.session_state.config

    @property
    def session_id(self) -> str:
        return self.event_factory.session_id

    @property
    def session_path(self) -> str:
        if self.session_logger is None:
            if self.config.permissions.mode.strip().lower() == "plan":
                return "disabled in plan mode"
            return "will start on the next turn"
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
        return self._memory.messages

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
            f"Network: {_on_off(self.config.runtime.allow_network)}",
            f"Subagents: {_on_off(self.config.subagents.enabled)}",
            (
                "Parallel subagents: "
                f"{_on_off(self.config.subagents.parallel)}"
            ),
            f"Commit offering: {_on_off(self.session_state.offer_commit)}",
            (
                "Default commit message: "
                f"{'set' if self.session_state.commit_message else 'not set'}"
            ),
            f"Usage output: {_on_off(self.session_state.show_usage)}",
            f"MCP: {_on_off(self.config.mcp.enabled)}",
            f"Plugins: {_on_off(self.config.plugins.enabled)}",
            f"Session: {self.session_id}",
            f"Session log: {self.session_path}",
            f"Completed turns: {self.turn_count}",
            f"Compactions: {self._compaction_count}",
            (
                "Compaction status: "
                f"{'warning' if self._last_compaction_warning else 'idle'}"
            ),
        ]
        if self._last_compaction_summary_path is not None:
            lines.append(
                f"Latest compacted summary: "
                f"{self._last_compaction_summary_path}"
            )
        if self._last_compaction_warning is not None:
            lines.append(
                f"Compaction warning: {self._last_compaction_warning}"
            )
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
        previous_config = self.config
        result = self.slash_router.route(value)
        if self.config is not previous_config:
            self._compactor = None
        return result

    def submit_slash_form(
        self,
        form: SlashCommandForm,
        value: str,
    ) -> SlashCommandResult:
        previous_config = self.config
        result = self.slash_router.submit_form(form, value)
        if self.config is not previous_config:
            self._compactor = None
        return result

    def submit_slash_picker(
        self,
        picker: SlashCommandPicker,
        value: str,
    ) -> SlashCommandResult:
        return self.slash_router.submit_picker(picker, value)

    def validate_slash_form(
        self,
        form: SlashCommandForm,
        value: str,
    ) -> SlashCommandResult:
        return self.slash_router.validate_form(form, value)

    def confirm_slash_command(
        self,
        confirmation: SlashConfirmation,
    ) -> SlashCommandResult:
        return self.slash_router.confirm(confirmation)

    def save_project_config_update(
        self,
        update: SessionConfigUpdate,
    ) -> ProjectConfigSaveResult:
        result = persist_project_config_update(
            self.project_root,
            update,
            config=self.config,
            approval_provider=self.approval_provider,
            approval_event_callback=self._log_config_approval_event,
        )
        self.session_state.apply_config_update(update)
        self._compactor = None
        return result

    def run_new_project_workflow(self, prompt: str) -> dict[str, Any]:
        """Run the existing starter workflow with current chat safety state."""

        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("New-project prompt must not be empty.")
        if len(normalized_prompt) > 50_000:
            raise ValueError(
                "New-project prompt must not exceed 50,000 characters."
            )
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError(
                "Cannot create a project while an agent turn is running."
            )
        try:
            self._ensure_session_logger()
            result = run_new_project(
                normalized_prompt,
                self.project_root,
                mode=self.config.permissions.mode,
                approval_provider=self.approval_provider,
                template=select_template(normalized_prompt),
                runtime_mode=self.config.runtime.mode,
                allow_network=self.config.runtime.allow_network,
                subagents_enabled=self.config.subagents.enabled,
                subagents_parallel=self.config.subagents.parallel,
                session_logger=self.session_logger,
            )
            formatted = format_new_project_result(result)
            for path in result.get("changed_files", []):
                if isinstance(path, str) and path:
                    self._changed_files.add(path)
            validation = result.get("validation", [])
            if isinstance(validation, list) and validation:
                self._validation_status = (
                    "failed"
                    if result.get("ok") is not True
                    else "passed"
                )
            self._memory.append_turn(normalized_prompt, formatted)
            self.turn_count += 1
            self._compactor = None
            return {
                "ok": result.get("ok") is True,
                "text": formatted,
                "result": result,
            }
        finally:
            self._turn_lock.release()

    def run_slash_action(
        self,
        request: SlashActionRequest,
    ) -> dict[str, Any]:
        """Run one existing LunarForge action without starting an agent turn."""

        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError(
                "Cannot run an action while an agent turn is running."
            )
        try:
            self._ensure_session_logger()
            if self.session_logger is not None:
                self.session_logger.log(
                    "slash_action_started",
                    action=request.name,
                    arguments=request.arguments,
                )
            try:
                outcome = self._execute_slash_action(request)
            except Exception as exc:
                if self.session_logger is not None:
                    self.session_logger.log(
                        "slash_action_failed",
                        action=request.name,
                        error=str(exc),
                    )
                raise
            if self.session_logger is not None:
                self.session_logger.log(
                    "slash_action_finished",
                    action=request.name,
                    ok=outcome.get("ok") is True,
                    result=outcome.get("result", {}),
                )
            return outcome
        finally:
            self._turn_lock.release()

    def _execute_slash_action(
        self,
        request: SlashActionRequest,
    ) -> dict[str, Any]:
        arguments = request.arguments
        action = request.name
        if action == "browser-setup":
            result = run_browser_setup(
                self.project_root,
                permission_mode=self.config.permissions.mode,
                runtime_mode=self.config.runtime.mode,
                approval_provider=self.approval_provider,
                approval_event_callback=self._log_config_approval_event,
            )
            return _action_outcome(
                result,
                (
                    "Browser setup commands:\n"
                    + "\n".join(f"- {item}" for item in BROWSER_SETUP_COMMANDS)
                    + "\n\nBrowser setup result:\n"
                    + _json_text(result)
                ),
            )
        if action == "browser-validate":
            result = self._run_browser_action(arguments)
            return _action_outcome(
                result,
                f"Browser validation result:\n{_json_text(result)}",
            )
        if action == "checkpoints":
            result = list_checkpoint_directories(self.project_root)
            return _action_outcome(
                result,
                _format_checkpoint_listing(result),
            )
        if action == "rollback":
            return self._run_rollback_action(arguments)
        if action == "git.status":
            result = git_status(
                self.project_root,
                mode=_git_execution_mode(self.config),
            )
            return _action_outcome(result, format_git_status(result))
        if action == "git.commit":
            return self._run_git_commit_action(arguments)
        if action == "mcp.list":
            result = build_mcp_diagnostic(
                self.project_root,
                globally_enabled=self.config.mcp.enabled,
            )
            return _action_outcome(
                result,
                f"MCP diagnostics:\n{_json_text(result)}",
            )
        if action == "plugins.list":
            result = build_plugin_diagnostic(
                self.project_root,
                globally_enabled=self.config.plugins.enabled,
            )
            return _action_outcome(
                result,
                f"Plugin diagnostics:\n{_json_text(result)}",
            )
        raise ValueError(f"Unsupported slash action: {action}.")

    def _run_browser_action(
        self,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        permission_mode = self.config.permissions.mode.strip().lower()
        runtime_mode = self.config.runtime.mode.strip().lower()
        if permission_mode == "plan":
            return {
                "ok": False,
                "status": "failed",
                "error": "Plan mode blocks browser validation actions.",
            }
        if permission_mode == "no-command" or runtime_mode == "no-command":
            return {
                "ok": False,
                "status": "failed",
                "error": "No-command mode blocks browser validation actions.",
            }
        url = str(arguments["url"])
        serve = arguments.get("serve")
        common = {
            "screenshot": arguments.get("screenshot") is True,
            "checks": list(arguments.get("checks", ())),
            "full_page": arguments.get("full_page") is True,
            "width": int(arguments["width"]),
            "height": int(arguments["height"]),
            "project_root": self.project_root,
        }
        if serve is None:
            return run_browser_validation(url, **common)
        if runtime_mode != "local":
            return {
                "ok": False,
                "status": "failed",
                "error": (
                    "Managed browser servers require local runtime mode. "
                    "Switch with /runtime local; direct URL validation remains "
                    "available without starting a server."
                ),
            }
        return run_managed_browser_validation(
            str(serve),
            url,
            startup_timeout_ms=int(arguments["startup_timeout_ms"]),
            permission_mode=permission_mode,
            runtime_mode=runtime_mode,
            approval_provider=self.approval_provider,
            approval_event_callback=self._log_config_approval_event,
            **common,
        )

    def _run_rollback_action(
        self,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        path = str(arguments["path"])
        checkpoint = arguments.get("checkpoint")
        checkpoint_id = str(checkpoint) if checkpoint is not None else None

        # Reject path escapes and invalid checkpoint selectors before asking.
        safe_path(self.project_root, path)
        preview = preview_rollback_file(
            self.project_root,
            path,
            checkpoint_id=checkpoint_id,
        )
        if preview.get("ok") is not True:
            return _action_outcome(
                preview,
                f"Rollback failed: {preview.get('error', 'Unknown error.')}",
            )
        selected_checkpoint = Path(
            str(preview["checkpoint_path"])
        ).parts[2]
        decision = PermissionManager(
            mode=self.config.permissions.mode,
            approval_provider=self.approval_provider,
            approval_event_callback=self._log_config_approval_event,
        ).authorize(
            PermissionLevel.WRITE,
            "rollback_file",
            {
                "path": str(preview["path"]),
                "checkpoint": selected_checkpoint,
            },
        )
        if not decision.allowed:
            result = {
                **preview,
                "ok": False,
                "permission_denied": True,
                "error": decision.reason or "Rollback approval was denied.",
            }
            return _action_outcome(
                result,
                f"Rollback not performed: {result['error']}",
            )
        result = rollback_file(
            self.project_root,
            path,
            checkpoint_id=selected_checkpoint,
        )
        if result.get("ok") is True:
            self._changed_files.add(str(result["path"]))
            if self._validation_status != "failed":
                self._validation_status = None
                self._validation_error = None
            text = (
                f"Restored {result['path']} from "
                f"{result['checkpoint_path']}."
            )
            previous = result.get("previous_state_checkpoint")
            if previous:
                text = f"{text}\nSaved the replaced state to {previous}."
        else:
            text = f"Rollback failed: {result.get('error', 'Unknown error.')}"
        return _action_outcome(result, text)

    def _run_git_commit_action(
        self,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        message = str(arguments["message"])
        override = arguments.get("despite_failed_validation") is True
        context = _commit_validation_context(
            self._validation_status,
            self._validation_error,
            override=override,
        )
        if self._validation_status == "failed" and not override:
            result = {
                "ok": False,
                "result_code": "validation_failed",
                "approval_requested": False,
                "error": (
                    "Current-session validation failed. Normal commit is "
                    "blocked. Re-run validation successfully, or explicitly "
                    "use despite-failed-validation=true."
                ),
            }
            return _action_outcome(
                result,
                f"{context}\n\nGit commit not created: {result['error']}",
            )

        changed = list_changed_files(
            self.project_root,
            source="both",
            session_files=tuple(sorted(self._changed_files)),
            mode=_git_execution_mode(self.config),
        )
        if changed.get("ok") is not True:
            result = {
                **changed,
                "ok": False,
                "result_code": "proposal_failed",
            }
            return _action_outcome(
                result,
                f"{context}\n\n{format_git_commit_result(result)}",
            )
        candidates = tuple(
            str(path) for path in changed.get("commit_candidates", ())
        )
        result = create_git_commit(
            self.project_root,
            message,
            session_files=candidates,
            proposed_files_label=(
                "Current chat changes (proposed for commit):"
                if self._changed_files
                else "Explicitly requested current files (proposed for commit):"
            ),
            mode=_git_execution_mode(self.config),
            approval_provider=self.approval_provider,
            approval_event_callback=self._log_config_approval_event,
            approval_context=context,
        )
        if result.get("ok") is True:
            for path in result.get("committed_files", ()):
                self._changed_files.discard(str(path))
        return _action_outcome(
            result,
            f"{context}\n\n{format_git_commit_result(result)}",
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
            self._ensure_session_logger()
            turn_id = self.event_factory.begin_turn()
            collected: list[AgentEvent] = []
            final_text: str | None = None
            try:
                self._maybe_compact(
                    normalized_prompt,
                    collected=collected,
                    event_callback=event_callback,
                )
                prior_messages = self._memory.messages
                live_event_ids: set[str] = set()
                live_event_lock = Lock()

                def forward_live_event(event: AgentEvent) -> None:
                    with live_event_lock:
                        live_event_ids.add(event.event_id)
                    if event_callback is not None:
                        event_callback(event)

                runner_arguments = {
                    "config": self.config,
                    "mode": self.config.permissions.mode,
                    "model_client": self.model_client,
                    "approval_provider": self.approval_provider,
                    "resume_messages": prior_messages,
                    "resumed_from": (
                        self._resumed_from_path
                        if not self._session_started
                        else None
                    ),
                    "event_factory": self.event_factory,
                    "session_logger": self.session_logger,
                    "emit_session_started": not self._session_started,
                    "offer_commit": self.session_state.offer_commit,
                    "commit_message": self.session_state.commit_message,
                    "show_usage": self.session_state.show_usage,
                }
                if self._event_runner is run_agent_events:
                    runner_arguments["live_event_callback"] = (
                        forward_live_event
                    )
                events = self._event_runner(
                    normalized_prompt,
                    self.project_root,
                    **runner_arguments,
                )
                try:
                    for event in events:
                        self._record_public_event(event)
                        collected.append(event)
                        with live_event_lock:
                            already_forwarded = (
                                event.event_id in live_event_ids
                            )
                        if event_callback is not None and not already_forwarded:
                            event_callback(event)
                        if (
                            event.type
                            == EventType.ASSISTANT_MESSAGE_COMPLETED.value
                        ):
                            text = event.payload.get("text")
                            if isinstance(text, str):
                                final_text = _conversation_text(text)
                finally:
                    if self.session_logger is not None:
                        self.session_logger.set_event_callback(None)
            except Exception:
                self._memory.append_user_after_error(normalized_prompt)
                self._session_started = True
                raise

            if final_text is None:
                self._memory.append_user_after_error(normalized_prompt)
                self._session_started = True
                raise RuntimeError(
                    "Agent turn completed without a final assistant event."
                )

            self._memory.append_turn(normalized_prompt, final_text)
            self._session_started = True
            self.turn_count += 1
            if (
                self.session_state.offer_commit
                and self.session_state.commit_message is not None
            ):
                self.session_state.commit_message = None
            return ChatTurnResult(
                final_text=final_text,
                events=tuple(collected),
                session_id=self.session_id,
                turn_id=turn_id,
            )

    def _switch_project(self, project_root: Path) -> None:
        if self._turn_lock.locked():
            raise RuntimeError(
                "Cannot switch projects while an agent turn is running."
            )
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {root}"
            )

        # Validate all project-scoped declarative inputs before changing state.
        # Plugin loading remains lazy; parsing its config here neither imports
        # plugin code nor grants any plugin permission.
        config = load_config(root)
        load_project_instructions(root)
        load_mcp_config(root)
        load_plugin_config(root)
        session_logger = (
            None
            if config.permissions.mode.strip().lower() == "plan"
            else create_session_logger(root)
        )

        self.session_state.project_root = root
        self.session_state.config = config
        self.session_state.offer_commit = False
        self.session_state.commit_message = None
        self.session_state.show_usage = False
        self.project_root = root
        self._memory = ConversationMemory()
        self._resumed_from_path = None
        self._resumed_from_session_id = None
        self._resumed_context_messages = 0
        self._session_started = False
        self._session_events.clear()
        self._active_tool_calls.clear()
        self._pending_approvals.clear()
        self._changed_files.clear()
        self._validation_status = None
        self._validation_error = None
        self._compactor = None
        self._compaction_count = 0
        self._last_compaction_summary_path = None
        self._last_compaction_warning = None
        self._session_picker_selections.clear()
        self._seed_compaction_facts = {}
        self.turn_count = 0
        self.session_logger = session_logger
        self.event_factory = EventFactory(
            session_id=_chat_session_id(self.session_logger),
        )
        if self.session_logger is not None:
            self._log_session_started()

    def _session_picker(self) -> SlashCommandPicker | None:
        result = list_session_files(self.project_root)
        if result.get("ok") is not True:
            raise ValueError(
                str(result.get("error", "Could not list sessions."))
            )
        current_path = (
            self.session_logger.path.resolve()
            if self.session_logger is not None
            else None
        )
        options: list[SlashPickerOption] = []
        selections: dict[str, str] = {}
        for raw_item in result.get("sessions", []):
            if not isinstance(raw_item, Mapping):
                continue
            selector = raw_item.get("name")
            if not isinstance(selector, str) or not selector:
                continue
            try:
                loaded = load_session(
                    self.project_root,
                    selector,
                    require_resumable=True,
                )
            except (OSError, ValueError):
                continue
            if (
                current_path is not None
                and loaded.path.resolve() == current_path
            ):
                continue
            options.append(
                SlashPickerOption(
                    value=f"session-choice-{len(options) + 1}",
                    label=_safe_loaded_session_id(loaded),
                    description=(
                        f"{loaded.safe_display_path} · "
                        f"{len(loaded.messages) - 1} safe context message(s)"
                    ),
                )
            )
            selections[options[-1].value] = selector
        if not options:
            self._session_picker_selections.clear()
            return None
        self._session_picker_selections = selections
        lines = [
            "Select a compatible session for this project.",
            "Historical tools will not run and approvals will not carry over.",
        ]
        lines.extend(
            f"{index}. {option.label} — {option.description}"
            for index, option in enumerate(options, start=1)
        )
        return SlashCommandPicker(
            command="resume",
            title="Resume a session",
            prompt="\n".join(lines),
            options=tuple(options),
            confirm_label="Resume",
        )

    def _resume_session(self, selector: str) -> SlashCommandResult:
        if self._turn_lock.locked():
            raise RuntimeError(
                "Cannot resume a session while an agent turn is running."
            )
        resolved_selector = self._session_picker_selections.get(
            selector,
            selector,
        )
        if selector.strip().casefold() == "latest":
            picker = self._session_picker()
            if picker is None:
                raise ValueError(
                    "No compatible resumable sessions were found for the "
                    "current project."
                )
            resolved_selector = self._session_picker_selections[
                picker.options[0].value
            ]
        previous = load_session(
            self.project_root,
            resolved_selector,
            require_resumable=True,
        )
        session_logger = (
            None
            if self.config.permissions.mode.strip().lower() == "plan"
            else create_session_logger(self.project_root)
        )

        self._memory = ConversationMemory(previous.messages)
        self._resumed_from_path = previous.safe_display_path
        self._resumed_from_session_id = _safe_loaded_session_id(previous)
        self._resumed_context_messages = len(previous.messages)
        self._session_started = False
        self._session_events.clear()
        self._active_tool_calls.clear()
        self._pending_approvals.clear()
        self._changed_files.clear()
        self._validation_status = None
        self._validation_error = None
        self._compactor = None
        self._compaction_count = 0
        self._last_compaction_summary_path = None
        self._last_compaction_warning = None
        self._session_picker_selections.clear()
        self._seed_compaction_facts = (
            dict(previous.compacted_summaries[-1].get("facts", {}))
            if (
                previous.compacted_summaries
                and isinstance(
                    previous.compacted_summaries[-1].get("facts"),
                    Mapping,
                )
            )
            else {}
        )
        self.turn_count = 0
        self.session_logger = session_logger
        self.event_factory = EventFactory(
            session_id=_chat_session_id(self.session_logger),
        )
        if self.session_logger is not None:
            self._log_session_started()

        return SlashCommandResult(
            handled=True,
            message=(
                f"Resumed safe conversation context from "
                f"{_safe_loaded_session_id(previous)}.\n"
                "Historical tool calls were not replayed and prior approvals "
                "were not reused."
            ),
            clear_transcript=True,
            refresh_header=True,
            restored_transcript=_visible_resumed_transcript(previous),
        )

    def _ensure_session_logger(self) -> None:
        if (
            self.session_logger is not None
            or self.config.permissions.mode.strip().lower() == "plan"
        ):
            return
        self.session_logger = create_session_logger(self.project_root)
        self.event_factory = EventFactory(
            session_id=_chat_session_id(self.session_logger),
        )
        self._session_started = False
        self._log_session_started()

    def _log_session_started(self) -> None:
        if self.session_logger is None:
            return
        self.session_logger.log(
            "session_started",
            session_id=self.session_id,
            project_fingerprint=project_fingerprint(self.project_root),
            runtime_mode=self.config.runtime.mode,
            permission_mode=self.config.permissions.mode,
            resumed_session_id=self._resumed_from_session_id,
            resumed_session=self._resumed_from_path,
        )

    def _log_config_approval_event(
        self,
        event: str,
        payload: Mapping[str, Any],
    ) -> None:
        if self.session_logger is None:
            return
        self.session_logger.log(event, **dict(payload))

    def _maybe_compact(
        self,
        incoming_prompt: str,
        *,
        collected: list[AgentEvent],
        event_callback: AgentEventCallback | None,
    ) -> None:
        if self.session_logger is None:
            return
        try:
            instruction_context = load_project_instructions(self.project_root)
        except Exception as exc:
            instruction_context = (
                "Current project instructions could not be loaded for "
                f"compaction: {exc}"
            )
        pressure = self._memory.estimate_pressure(
            instruction_context=instruction_context,
            relevant_tool_results=relevant_tool_results(
                self._session_events
            ),
            incoming_user_text=incoming_prompt,
        )
        if not should_compact(
            pressure,
            self.config.ui.chat.compact_at_tokens,
        ):
            return
        if not self._memory.can_compact():
            return
        if self._active_tool_calls or self._pending_approvals:
            return

        started = self.event_factory.create(
            EventType.MEMORY_COMPACTION_STARTED,
            {
                **pressure.to_dict(),
                "compact_at_tokens": (
                    self.config.ui.chat.compact_at_tokens
                ),
                "compact_to_tokens": (
                    self.config.ui.chat.compact_to_tokens
                ),
                "messages_before": self._memory.message_count,
            },
        )
        self._publish_controller_event(
            started,
            collected=collected,
            event_callback=event_callback,
        )
        self.session_logger.log(
            EventType.MEMORY_COMPACTION_STARTED.value,
            **dict(started.payload),
        )
        source_event_count = self.session_logger.record_count - 1

        try:
            result = self._get_compactor().maybe_compact(
                self._memory,
                incoming_user_text=incoming_prompt,
                instruction_context=instruction_context,
                events=self._session_events,
                source_session_event_count=source_event_count,
            )
            if not result.compacted or result.summary_path is None:
                raise CompactionError(result.reason)
        except Exception as exc:
            warning = (
                "Working-memory compaction failed; continuing with the "
                f"existing safe context. {exc}"
            )
            self._last_compaction_warning = warning
            finished = self.event_factory.create(
                EventType.MEMORY_COMPACTION_FINISHED,
                {
                    "status": "failed",
                    "warning": warning,
                    "messages_before": self._memory.message_count,
                    "messages_after": self._memory.message_count,
                },
                parent_event_id=started.event_id,
            )
            self.session_logger.log(
                EventType.MEMORY_COMPACTION_FINISHED.value,
                **dict(finished.payload),
            )
            self._publish_controller_event(
                finished,
                collected=collected,
                event_callback=event_callback,
            )
            return

        if result.model_usage is not None:
            self.session_logger.log(
                "model_usage",
                **dict(result.model_usage),
            )
        self._compaction_count += 1
        self._last_compaction_summary_path = result.summary_path
        self._last_compaction_warning = None
        # Older public events are now represented by the persisted summary.
        # Keep only events observed after this boundary so their tool-result
        # pressure is not counted again on every subsequent turn.
        self._session_events.clear()
        finished = self.event_factory.create(
            EventType.MEMORY_COMPACTION_FINISHED,
            {
                "status": "completed",
                "summary_path": result.summary_path,
                "messages_before": result.messages_before,
                "messages_after": result.messages_after,
                "estimated_tokens_before": (
                    result.pressure.total_tokens
                ),
                "compact_to_tokens": (
                    self.config.ui.chat.compact_to_tokens
                ),
            },
            parent_event_id=started.event_id,
        )
        self.session_logger.log(
            EventType.MEMORY_COMPACTION_FINISHED.value,
            **dict(finished.payload),
        )
        self._publish_controller_event(
            finished,
            collected=collected,
            event_callback=event_callback,
        )

    def _get_compactor(self) -> ConversationCompactor:
        if self._compactor is None:
            selected_model = (
                self.model_client
                if self.model_client is not None
                else create_model_client(self.config.model)
            )
            self._compactor = ConversationCompactor(
                self.project_root,
                self.session_id,
                self.config,
                selected_model,
                seed_facts=self._seed_compaction_facts,
            )
        return self._compactor

    def _publish_controller_event(
        self,
        event: AgentEvent,
        *,
        collected: list[AgentEvent],
        event_callback: AgentEventCallback | None,
    ) -> None:
        self._record_public_event(event)
        collected.append(event)
        if event_callback is not None:
            event_callback(event)

    def _record_public_event(self, event: AgentEvent) -> None:
        self._session_events.append(event)
        if len(self._session_events) > MAX_CHAT_COMPACTION_EVENTS:
            del self._session_events[
                : len(self._session_events) - MAX_CHAT_COMPACTION_EVENTS
            ]
        identifier = _operation_identifier(event)
        if event.type == EventType.TOOL_STARTED.value:
            self._active_tool_calls.add(identifier)
        elif event.type in {
            EventType.TOOL_FINISHED.value,
            EventType.TOOL_FAILED.value,
        }:
            self._active_tool_calls.discard(identifier)
        elif event.type == EventType.PERMISSION_REQUESTED.value:
            self._pending_approvals.add(identifier)
        elif event.type == EventType.PERMISSION_RESOLVED.value:
            self._pending_approvals.discard(identifier)
        elif event.type == EventType.VALIDATION_FINISHED.value:
            self._validation_status = (
                "passed" if event.payload.get("ok") is True else "failed"
            )
            error = event.payload.get("error")
            self._validation_error = (
                str(error) if isinstance(error, str) and error else None
            )
        elif event.type == EventType.TOOL_FINISHED.value:
            tool_name = _tool_name(event.payload)
            if tool_name not in {
                "create_dir",
                "write_file",
                "edit_file",
                "replace_lines",
                "insert_lines",
            }:
                return
            result = event.payload.get("result")
            if not isinstance(result, Mapping) or result.get("ok") is not True:
                return
            path = result.get("path")
            if isinstance(path, str) and path:
                self._changed_files.add(path)
            if self._validation_status != "failed":
                self._validation_status = None
                self._validation_error = None


def _action_outcome(
    result: Mapping[str, Any],
    text: str,
) -> dict[str, Any]:
    return {
        "ok": result.get("ok") is True,
        "text": _bounded_text(
            text,
            MAX_TEXTUAL_TRANSCRIPT_CHARACTERS,
        ),
        "result": dict(result),
    }


def _json_text(result: Mapping[str, Any]) -> str:
    return json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
        default=str,
    )


def _format_checkpoint_listing(result: Mapping[str, Any]) -> str:
    if result.get("ok") is not True:
        return (
            "Checkpoint listing failed: "
            f"{result.get('error', 'Unknown error.')}"
        )
    checkpoints = result.get("checkpoints", [])
    if not isinstance(checkpoints, list) or not checkpoints:
        return "No checkpoints found under .agent/checkpoints."
    lines = ["Checkpoints (newest first):"]
    for checkpoint in checkpoints:
        if isinstance(checkpoint, Mapping):
            lines.append(
                f"- {checkpoint.get('id')}  {checkpoint.get('path')}"
            )
    if result.get("truncated") is True:
        lines.append("- ... additional checkpoint directories omitted")
    return "\n".join(lines)


def _git_execution_mode(config: AppConfig) -> str:
    if config.runtime.mode.strip().lower() == "no-command":
        return "no-command"
    return config.permissions.mode.strip().lower() or "default"


def _commit_validation_context(
    status: str | None,
    error: str | None,
    *,
    override: bool,
) -> str:
    lines = ["Validation results before commit approval:"]
    if status == "passed":
        lines.append("- Passed (latest current-session validation event).")
    elif status == "failed":
        detail = f": {error}" if error else "."
        lines.append(
            "- Failed (latest current-session validation event)"
            f"{detail}"
        )
        if override:
            lines.append(
                "- The user explicitly requested a commit despite failed "
                "validation; Git commit approval is still required."
            )
    else:
        lines.append("- Not run in the current chat session.")
    return "\n".join(lines)


def _visible_resumed_transcript(
    session: LoadedSession,
) -> tuple[tuple[str, str], ...]:
    """Return only safe conversational history suitable for display."""

    prefixes = (
        "[Historical user prompt]\n",
        "[Historical assistant message]\n",
        "[Historical recent turn retained after compaction]\n",
    )
    candidates: list[tuple[str, str]] = []
    for message in session.messages:
        role = message.get("role")
        content = message.get("content", "")
        if role not in {"user", "assistant"}:
            continue
        prefix = next(
            (
                candidate
                for candidate in prefixes
                if content.startswith(candidate)
            ),
            None,
        )
        if prefix is None:
            continue
        text = content[len(prefix) :].strip()
        if not text:
            continue
        candidates.append((role, text))

    visible: list[tuple[str, str]] = []
    used_characters = 0
    for role, text in reversed(candidates[-40:]):
        remaining = MAX_TEXTUAL_TRANSCRIPT_CHARACTERS - used_characters
        if remaining <= 0:
            break
        bounded = _bounded_text(text, remaining)
        visible.insert(0, (role, bounded))
        used_characters += len(bounded)
    return tuple(visible)


def _safe_loaded_session_id(session: LoadedSession) -> str:
    safe_stem = Path(session.safe_display_path).stem
    if safe_stem.startswith("session_"):
        return safe_stem
    return f"session_{safe_stem}"


def _chat_session_id(session: SessionLogger | None) -> str:
    if session is None:
        return EventFactory().session_id
    return f"session_{session.path.stem}"


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def _conversation_text(text: str) -> str:
    return _SESSION_NOTE_PATTERN.sub("", text).strip()


def _tool_name(payload: Mapping[str, Any]) -> str:
    return _first_text(
        payload,
        "tool_name",
        "internal_tool_name",
        "name",
    ) or "unknown"


def _tool_progress_text(
    payload: Mapping[str, Any],
    state: str,
) -> str:
    tool_name = _tool_name(payload)
    command: str | None = None
    for key in ("args_preview", "result"):
        candidate = payload.get(key)
        if isinstance(candidate, Mapping):
            command = _first_text(candidate, "command")
            if command:
                break
    if command and tool_name in {
        "run_command",
        "run_validation",
        "run_managed_browser_validation",
    }:
        return f"Command: {command} · {state}"
    return f"Tool: {tool_name} · {state}"


def _first_text(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _operation_identifier(event: AgentEvent) -> str:
    for key in ("call_id", "request_id", "id"):
        value = event.payload.get(key)
        if isinstance(value, str) and value:
            return value
    return event.event_id


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
