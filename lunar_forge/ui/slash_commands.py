"""Central parser and router for Textual slash commands."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lunar_forge.config import ALLOWED_REASONING_EFFORTS
from lunar_forge.ui.textual_state import (
    ALLOWED_PERMISSION_MODES,
    ALLOWED_RUNTIME_MODES,
    ChatSessionState,
    SessionConfigUpdate,
)
from lunar_forge.workflows.browser_validation import (
    DEFAULT_SERVER_STARTUP_TIMEOUT_MS,
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
)


_COMMAND_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_BOOLEAN_VALUES = {
    "1": True,
    "true": True,
    "yes": True,
    "on": True,
    "enable": True,
    "enabled": True,
    "0": False,
    "false": False,
    "no": False,
    "off": False,
    "disable": False,
    "disabled": False,
}
POPULAR_SLASH_COMMANDS = (
    "/help",
    "/status",
    "/compact",
    "/sessions",
    "/resume",
)
SLASH_COMMANDS = (
    "/help",
    "/status",
    "/clear",
    "/exit",
    "/finish",
    "/compact",
    "/project",
    "/sessions",
    "/resume",
    "/new",
    "/browser-setup",
    "/browser-validate",
    "/checkpoints",
    "/rollback",
    "/git status",
    "/git commit",
    "/plan",
    "/docker",
    "/allow-network",
    "/subagents",
    "/parallel-subagents",
    "/commit",
    "/commit-message",
    "/show-usage",
    "/reasoning-effort",
    "/runtime",
    "/permissions",
    "/mcp",
    "/mcp list",
    "/plugins",
    "/plugins list",
)
CONFIG_PERSISTENCE_SCOPES = ("session", "project", "global")


@dataclass(frozen=True, slots=True)
class SlashInvocation:
    """One parsed command with quotes removed and backslashes preserved."""

    name: str
    arguments: tuple[str, ...]
    raw: str


@dataclass(frozen=True, slots=True)
class SlashCommandForm:
    """Presentation-neutral request for a missing command argument."""

    command: str
    title: str
    prompt: str
    placeholder: str = ""
    choices: tuple[str, ...] = ()
    current_value: str | None = None
    config_backed: bool = False
    config_scopes: tuple[str, ...] = ()
    parse_arguments: bool = False
    argument_prefix: tuple[str, ...] = ()
    submit_label: str = "Apply to session"


@dataclass(frozen=True, slots=True)
class SlashPickerOption:
    """One bounded, presentation-neutral choice in a slash-command picker."""

    value: str
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class SlashCommandPicker:
    """Presentation-neutral picker supplied by a slash command."""

    command: str
    title: str
    prompt: str
    options: tuple[SlashPickerOption, ...]
    confirm_label: str = "Select"


@dataclass(frozen=True, slots=True)
class SlashActionRequest:
    """One JSON-friendly request for an existing LunarForge action."""

    name: str
    arguments: dict[str, object]
    display: str


@dataclass(frozen=True, slots=True)
class SlashConfirmation:
    """Presentation-neutral confirmation requested by a command."""

    action: str
    title: str
    message: str
    confirm_label: str


@dataclass(frozen=True, slots=True)
class SlashCommandResult:
    """Declarative result consumed by Textual without command-specific logic."""

    handled: bool
    message: str | None = None
    error: bool = False
    clear_transcript: bool = False
    exit_app: bool = False
    refresh_header: bool = False
    project_switched: bool = False
    form: SlashCommandForm | None = None
    picker: SlashCommandPicker | None = None
    confirmation: SlashConfirmation | None = None
    config_update: SessionConfigUpdate | None = None
    save_config_to_project: bool = False
    save_config_to_user: bool = False
    restored_transcript: tuple[tuple[str, str], ...] = ()
    new_project_prompt: str | None = None
    action: SlashActionRequest | None = None
    finish_task: bool = False


class SlashCommandParser:
    """Parse slash-prefixed input without damaging Windows paths."""

    def parse(self, value: str) -> SlashInvocation | None:
        stripped = value.strip()
        if not stripped.startswith("/"):
            return None
        lexer = shlex.shlex(stripped, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        lexer.escape = ""
        try:
            tokens = list(lexer)
        except ValueError as exc:
            raise ValueError(f"Invalid slash-command quoting: {exc}") from exc
        if not tokens:
            raise ValueError("Slash command must include a command name.")
        command_token = tokens[0]
        name = command_token[1:].casefold()
        if not name or not _COMMAND_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"Invalid slash-command name: {command_token!r}."
            )
        return SlashInvocation(
            name=name,
            arguments=tuple(tokens[1:]),
            raw=stripped,
        )


class SlashCommandRouter:
    """Validate and apply all supported chat commands in one place."""

    def __init__(
        self,
        state: ChatSessionState,
        *,
        status_provider: Callable[[], str] | None = None,
        project_switcher: Callable[[Path], None] | None = None,
        session_picker_provider: (
            Callable[[], SlashCommandPicker | None] | None
        ) = None,
        session_resumer: (
            Callable[[str], SlashCommandResult] | None
        ) = None,
        parser: SlashCommandParser | None = None,
    ) -> None:
        self.state = state
        self._status_provider = status_provider
        self._project_switcher = project_switcher
        self._session_picker_provider = session_picker_provider
        self._session_resumer = session_resumer
        self._parser = parser or SlashCommandParser()
        self._handlers = {
            "help": self._help,
            "status": self._status,
            "clear": self._clear,
            "exit": self._exit,
            "finish": self._finish,
            "compact": self._compact,
            "project": self._project,
            "sessions": self._sessions,
            "resume": self._resume,
            "new": self._new,
            "browser-setup": self._browser_setup,
            "browser-validate": self._browser_validate,
            "checkpoints": self._checkpoints,
            "rollback": self._rollback,
            "git": self._git,
            "plan": self._plan,
            "docker": self._docker,
            "allow-network": self._allow_network,
            "subagents": self._subagents,
            "parallel-subagents": self._parallel_subagents,
            "commit": self._commit,
            "commit-message": self._commit_message,
            "show-usage": self._show_usage,
            "reasoning-effort": self._reasoning_effort,
            "runtime": self._runtime,
            "permissions": self._permissions,
            "mcp": self._mcp,
            "plugins": self._plugins,
        }

    def route(
        self,
        value: str,
        *,
        active_turn: bool = False,
    ) -> SlashCommandResult:
        try:
            invocation = self._parser.parse(value)
        except ValueError as exc:
            return self._error(str(exc))
        if invocation is None:
            return SlashCommandResult(handled=False)
        if active_turn and invocation.name != "finish":
            return self._error(
                "A turn is already running. Use /finish to cancel it, or "
                "wait for it to complete."
            )
        handler = self._handlers.get(invocation.name)
        if handler is None:
            return self._error(
                f"Unknown command: /{invocation.name}. Use /help."
            )
        try:
            return handler(invocation.arguments)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._error(str(exc))

    def submit_form(
        self,
        form: SlashCommandForm,
        value: str,
    ) -> SlashCommandResult:
        normalized = value.strip()
        if not normalized:
            return self._error(f"{form.prompt} A value is required.")
        handler = self._handlers.get(form.command)
        if handler is None:
            return self._error(
                f"Unknown command form: /{form.command}."
            )
        arguments = (
            (*form.argument_prefix, *_split_argument_text(normalized))
            if form.parse_arguments
            else (normalized,)
        )
        try:
            return handler(arguments)
        except (OSError, RuntimeError, ValueError) as exc:
            return self._error(str(exc))

    def validate_form(
        self,
        form: SlashCommandForm,
        value: str,
    ) -> SlashCommandResult:
        """Validate a config form without changing active session state."""

        if not form.config_backed:
            return self._error(
                f"/{form.command} is not a config-backed selector."
            )
        previous_config = self.state.config
        try:
            return self.submit_form(form, value)
        finally:
            self.state.config = previous_config

    def submit_picker(
        self,
        picker: SlashCommandPicker,
        value: str,
    ) -> SlashCommandResult:
        normalized = value.strip()
        if not normalized:
            return self._error(f"{picker.prompt} A selection is required.")
        if normalized not in {
            option.value for option in picker.options
        }:
            return self._error(
                f"Invalid selection for /{picker.command}."
            )
        if picker.command == "resume":
            return self._resume((normalized,))
        return self._error(
            f"Unsupported picker command: /{picker.command}."
        )

    def confirm(
        self,
        confirmation: SlashConfirmation,
    ) -> SlashCommandResult:
        if confirmation.action == "clear_transcript":
            return SlashCommandResult(
                handled=True,
                message=(
                    "Transcript cleared; conversation context and session "
                    "logs were retained."
                ),
                clear_transcript=True,
            )
        return self._error(
            f"Unsupported confirmation action: {confirmation.action}."
        )

    def _help(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        self._require_no_arguments("help", arguments)
        return SlashCommandResult(
            handled=True,
            message=(
                "Chat commands:\n"
                "/help — show this command list\n"
                "/status — show active session state\n"
                "/clear — clear the visible transcript after confirmation\n"
                "/exit — close chat\n"
                "/finish — cancel the active task and revoke its changes\n"
                "/compact — compact older safe conversation context\n"
                "/project <path> — switch project and start fresh context\n"
                "/sessions — choose a resumable session for this project\n"
                "/resume [latest|session] — resume safe historical context\n"
                "/new <prompt> — run the existing new-project workflow\n"
                "/browser-setup — install optional browser support\n"
                "/browser-validate [options] — validate a loopback page\n"
                "/checkpoints — list project checkpoints\n"
                "/rollback <path> [checkpoint=<id>]\n"
                "/git status\n"
                "/git commit <message> [despite-failed-validation=true]\n"
                "/mcp list — run MCP diagnostics without enabling MCP\n"
                "/plugins list — run plugin diagnostics without enabling plugins\n"
                "/plan [on|off] — toggle or set plan permissions\n"
                "/docker [on|off] — toggle or set Docker runtime\n"
                "/allow-network [on|off]\n"
                "/subagents [on|off]\n"
                "/parallel-subagents [on|off]\n"
                "/commit [on|off]\n"
                "/commit-message <message>\n"
                "/show-usage [on|off]\n"
                "/reasoning-effort <low|medium|high|xhigh|max>\n"
                "/runtime <local|docker|no-command>\n"
                "/permissions <default|yes|no-command|plan|docker>\n"
                "/mcp [on|off]\n"
                "/plugins [on|off]\n"
                "Quote arguments containing spaces."
            ),
        )

    def _status(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        self._require_no_arguments("status", arguments)
        message = (
            self._status_provider()
            if self._status_provider is not None
            else self._state_status()
        )
        return SlashCommandResult(handled=True, message=message)

    def _clear(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        self._require_no_arguments("clear", arguments)
        return SlashCommandResult(
            handled=True,
            confirmation=SlashConfirmation(
                action="clear_transcript",
                title="Clear visible transcript?",
                message=(
                    "Conversation memory and session JSONL logs will be "
                    "retained. Only the visible transcript will be cleared."
                ),
                confirm_label="Clear",
            ),
        )

    def _exit(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        self._require_no_arguments("exit", arguments)
        return SlashCommandResult(handled=True, exit_app=True)

    def _finish(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        self._require_no_arguments("finish", arguments)
        return SlashCommandResult(handled=True, finish_task=True)

    def _compact(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        self._require_no_arguments("compact", arguments)
        return self._action(
            "memory.compact",
            {},
            "/compact",
        )

    def _project(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        if not arguments:
            return SlashCommandResult(
                handled=True,
                form=SlashCommandForm(
                    command="project",
                    title="Switch project",
                    prompt="Enter an existing project directory.",
                    placeholder="C:\\path\\to\\project",
                ),
            )
        if len(arguments) != 1:
            raise ValueError(
                "/project accepts one path. Quote paths containing spaces."
            )
        root = Path(arguments[0]).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"Project path does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"Project path is not a directory: {root}")
        if self._project_switcher is not None:
            self._project_switcher(root)
        self.state.set_project_root(root)
        logging_note = (
            "Session logging remains disabled in plan mode."
            if self.state.config.permissions.mode == "plan"
            else "Started a fresh conversation context and session log."
        )
        return SlashCommandResult(
            handled=True,
            message=(
                f"Switched to project: {root}\n"
                f"{logging_note}"
            ),
            clear_transcript=True,
            refresh_header=True,
            project_switched=True,
        )

    def _sessions(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        self._require_no_arguments("sessions", arguments)
        picker = self._session_picker()
        if picker is None:
            return SlashCommandResult(
                handled=True,
                message=(
                    "No compatible resumable sessions were found for the "
                    "current project."
                ),
            )
        return SlashCommandResult(handled=True, picker=picker)

    def _resume(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        if not arguments:
            picker = self._session_picker()
            if picker is None:
                return SlashCommandResult(
                    handled=True,
                    message=(
                        "No compatible resumable sessions were found for the "
                        "current project."
                    ),
                )
            return SlashCommandResult(handled=True, picker=picker)
        selector = self._one_argument("resume", arguments)
        if self._session_resumer is None:
            raise RuntimeError(
                "Session resume is unavailable in this chat controller."
            )
        return self._session_resumer(selector)

    def _new(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        if not arguments:
            return SlashCommandResult(
                handled=True,
                form=SlashCommandForm(
                    command="new",
                    title="Create a new project",
                    prompt=(
                        "Describe the project to create in the active project "
                        "directory."
                    ),
                    placeholder=(
                        "Build a small Python CLI that greets a named user"
                    ),
                ),
            )
        prompt = " ".join(arguments).strip()
        if not prompt:
            raise ValueError("New-project prompt must not be empty.")
        if len(prompt) > 50_000:
            raise ValueError(
                "New-project prompt must not exceed 50,000 characters."
            )
        return SlashCommandResult(
            handled=True,
            new_project_prompt=prompt,
        )

    def _session_picker(self) -> SlashCommandPicker | None:
        if self._session_picker_provider is None:
            raise RuntimeError(
                "Session listing is unavailable in this chat controller."
            )
        return self._session_picker_provider()

    def _browser_setup(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        self._require_no_arguments("browser-setup", arguments)
        return self._action(
            "browser-setup",
            {},
            "/browser-setup",
        )

    def _browser_validate(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        if not arguments:
            return SlashCommandResult(
                handled=True,
                form=SlashCommandForm(
                    command="browser-validate",
                    title="Browser validation",
                    prompt=(
                        "Enter browser options. Required: url. Optional: "
                        "serve, screenshot, no-screenshot, full-page, width, "
                        "height, startup-timeout-ms, and repeatable check.\n"
                        "Example: url=http://localhost:5173 "
                        "serve=\"npm run dev\" full-page=true width=1440 "
                        "check=#app"
                    ),
                    placeholder="url=http://localhost:5173 screenshot=true",
                    parse_arguments=True,
                    submit_label="Run validation",
                ),
            )
        options = _parse_browser_validation_arguments(arguments)
        return self._action(
            "browser-validate",
            options,
            _display_action("browser-validate", options),
        )

    def _checkpoints(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        self._require_no_arguments("checkpoints", arguments)
        return self._action("checkpoints", {}, "/checkpoints")

    def _rollback(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        if not arguments:
            return SlashCommandResult(
                handled=True,
                form=SlashCommandForm(
                    command="rollback",
                    title="Rollback a project file",
                    prompt=(
                        "Enter a project-relative path and optional exact "
                        "checkpoint ID. The latest checkpoint is used when "
                        "checkpoint is omitted."
                    ),
                    placeholder=(
                        "path=src/app.py "
                        "checkpoint=20260729T120000.000000Z"
                    ),
                    parse_arguments=True,
                    submit_label="Review rollback",
                ),
            )
        options = _parse_rollback_arguments(arguments)
        return self._action(
            "rollback",
            options,
            _display_action("rollback", options),
        )

    def _git(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        if not arguments:
            raise ValueError("/git expects status or commit.")
        subcommand = arguments[0].casefold()
        tail = arguments[1:]
        if subcommand == "status":
            self._require_no_arguments("git status", tail)
            return self._action("git.status", {}, "/git status")
        if subcommand != "commit":
            raise ValueError(
                f"Unknown /git command: {arguments[0]!r}. "
                "Use /git status or /git commit."
            )
        if not tail:
            return SlashCommandResult(
                handled=True,
                form=SlashCommandForm(
                    command="git",
                    title="Create a Git commit",
                    prompt=(
                        "Enter a concise commit message. If current-session "
                        "validation failed, an override must be explicit with "
                        "despite-failed-validation=true; commit approval is "
                        "still requested separately."
                    ),
                    placeholder=(
                        'message="Add pricing page" '
                        "despite-failed-validation=false"
                    ),
                    parse_arguments=True,
                    argument_prefix=("commit",),
                    submit_label="Review commit",
                ),
            )
        options = _parse_git_commit_arguments(tail)
        return self._action(
            "git.commit",
            options,
            _display_action("git commit", options),
        )

    def _plan(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        current = self.state.config.permissions.mode == "plan"
        if not arguments:
            return self._boolean_form(
                "plan",
                "Plan mode",
                "Choose plan permissions for future turns.",
                current,
            )
        values, scope = self._config_arguments("plan", arguments)
        enabled = self._optional_boolean("plan", values, current)
        mode = "plan" if enabled else "default"
        return self._scoped_config_result(
            "Plan mode",
            _on_off(enabled),
            scope,
            lambda: self.state.set_permission_mode(mode),
            refresh_header=True,
        )

    def _docker(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        current = self.state.config.runtime.mode == "docker"
        if not arguments:
            return self._boolean_form(
                "docker",
                "Docker runtime",
                "Choose Docker runtime for future turns.",
                current,
            )
        values, scope = self._config_arguments("docker", arguments)
        enabled = self._optional_boolean("docker", values, current)
        mode = "docker" if enabled else "local"
        return self._scoped_config_result(
            "Docker runtime",
            _on_off(enabled),
            scope,
            lambda: self.state.set_runtime_mode(mode),
            refresh_header=True,
        )

    def _allow_network(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        current = self.state.config.runtime.allow_network
        if not arguments:
            return self._boolean_form(
                "allow-network",
                "Network access",
                "Choose network access for future turns.",
                current,
            )
        values, scope = self._config_arguments(
            "allow-network",
            arguments,
        )
        enabled = self._optional_boolean(
            "allow-network",
            values,
            current,
        )
        return self._scoped_config_result(
            "Network access",
            _on_off(enabled),
            scope,
            lambda: self.state.set_allow_network(enabled),
        )

    def _subagents(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        current = self.state.config.subagents.enabled
        if not arguments:
            return self._boolean_form(
                "subagents",
                "Subagents",
                "Choose whether future turns use subagents.",
                current,
            )
        values, scope = self._config_arguments("subagents", arguments)
        enabled = self._optional_boolean("subagents", values, current)
        return self._scoped_config_result(
            "Subagents",
            _on_off(enabled),
            scope,
            lambda: self.state.set_subagents_enabled(enabled),
        )

    def _parallel_subagents(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        current = self.state.config.subagents.parallel
        if not arguments:
            return self._boolean_form(
                "parallel-subagents",
                "Parallel subagents",
                "Choose whether safe subagent phases run in parallel.",
                current,
            )
        values, scope = self._config_arguments(
            "parallel-subagents",
            arguments,
        )
        enabled = self._optional_boolean(
            "parallel-subagents",
            values,
            current,
        )
        suffix = " Subagents were also enabled." if enabled else ""
        return self._scoped_config_result(
            "Parallel subagents",
            _on_off(enabled),
            scope,
            lambda: self.state.set_parallel_subagents(enabled),
            message_suffix=suffix,
        )

    def _commit(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        enabled = self._optional_boolean(
            "commit",
            arguments,
            self.state.offer_commit,
        )
        self.state.offer_commit = enabled
        return SlashCommandResult(
            handled=True,
            message=f"Commit offering: {_on_off(enabled)} (session only).",
        )

    def _commit_message(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        if not arguments:
            return SlashCommandResult(
                handled=True,
                form=SlashCommandForm(
                    command="commit-message",
                    title="Default commit message",
                    prompt="Enter the message for the next commit flow.",
                    placeholder="Add pricing page",
                ),
            )
        message = " ".join(arguments).strip()
        if not message:
            raise ValueError("Commit message must not be empty.")
        if len(message) > 500:
            raise ValueError("Commit message must not exceed 500 characters.")
        self.state.commit_message = message
        return SlashCommandResult(
            handled=True,
            message="Default commit message set for the next commit flow.",
        )

    def _show_usage(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        enabled = self._optional_boolean(
            "show-usage",
            arguments,
            self.state.show_usage,
        )
        self.state.show_usage = enabled
        return SlashCommandResult(
            handled=True,
            message=f"Usage output: {_on_off(enabled)} (session only).",
        )

    def _reasoning_effort(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        if not arguments:
            return self._choice_form(
                "reasoning-effort",
                "Reasoning effort",
                "Choose the reasoning effort for future turns.",
                ALLOWED_REASONING_EFFORTS,
                self.state.config.model.reasoning.effort,
            )
        values, scope = self._config_arguments(
            "reasoning-effort",
            arguments,
        )
        value = self._one_argument("reasoning-effort", values)
        return self._scoped_config_result(
            "Reasoning effort",
            value.strip().lower(),
            scope,
            lambda: self.state.set_reasoning_effort(value),
            refresh_header=True,
        )

    def _runtime(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        if not arguments:
            return self._choice_form(
                "runtime",
                "Runtime mode",
                "Choose the runtime mode for future turns.",
                ALLOWED_RUNTIME_MODES,
                self.state.config.runtime.mode,
            )
        values, scope = self._config_arguments("runtime", arguments)
        value = self._one_argument("runtime", values)
        return self._scoped_config_result(
            "Runtime mode",
            value.strip().lower(),
            scope,
            lambda: self.state.set_runtime_mode(value),
            refresh_header=True,
        )

    def _permissions(
        self,
        arguments: tuple[str, ...],
    ) -> SlashCommandResult:
        if not arguments:
            return self._choice_form(
                "permissions",
                "Permission mode",
                "Choose the permission mode for future turns.",
                ALLOWED_PERMISSION_MODES,
                self.state.config.permissions.mode,
            )
        values, scope = self._config_arguments("permissions", arguments)
        value = self._one_argument("permissions", values)
        return self._scoped_config_result(
            "Permission mode",
            value.strip().lower(),
            scope,
            lambda: self.state.set_permission_mode(value),
            refresh_header=True,
        )

    def _mcp(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        current = self.state.config.mcp.enabled
        if len(arguments) == 1 and arguments[0].casefold() == "list":
            return self._action("mcp.list", {}, "/mcp list")
        if not arguments:
            return self._boolean_form(
                "mcp",
                "MCP integration",
                "Choose whether future turns enable configured MCP servers.",
                current,
            )
        values, scope = self._config_arguments("mcp", arguments)
        enabled = self._optional_boolean("mcp", values, current)
        return self._scoped_config_result(
            "MCP integration",
            _on_off(enabled),
            scope,
            lambda: self.state.set_mcp_enabled(enabled),
        )

    def _plugins(self, arguments: tuple[str, ...]) -> SlashCommandResult:
        current = self.state.config.plugins.enabled
        if len(arguments) == 1 and arguments[0].casefold() == "list":
            return self._action("plugins.list", {}, "/plugins list")
        if not arguments:
            return self._boolean_form(
                "plugins",
                "Plugin integration",
                "Choose whether future turns enable configured plugins.",
                current,
            )
        values, scope = self._config_arguments("plugins", arguments)
        enabled = self._optional_boolean("plugins", values, current)
        return self._scoped_config_result(
            "Plugin integration",
            _on_off(enabled),
            scope,
            lambda: self.state.set_plugins_enabled(enabled),
        )

    def _state_status(self) -> str:
        config = self.state.config
        return "\n".join(
            (
                f"Project: {self.state.project_root}",
                f"Model: {config.model.model}",
                f"Reasoning effort: {config.model.reasoning.effort}",
                f"Runtime mode: {config.runtime.mode}",
                f"Permission mode: {config.permissions.mode}",
                f"Network: {_on_off(config.runtime.allow_network)}",
                f"Subagents: {_on_off(config.subagents.enabled)}",
                (
                    "Parallel subagents: "
                    f"{_on_off(config.subagents.parallel)}"
                ),
                f"Commit offering: {_on_off(self.state.offer_commit)}",
                f"MCP: {_on_off(config.mcp.enabled)}",
                f"Plugins: {_on_off(config.plugins.enabled)}",
            )
        )

    def _choice_form(
        self,
        command: str,
        title: str,
        prompt: str,
        choices: tuple[str, ...],
        current: str,
    ) -> SlashCommandResult:
        return SlashCommandResult(
            handled=True,
            form=SlashCommandForm(
                command=command,
                title=title,
                prompt=(
                    f"{prompt}\nCurrent: {current}\n"
                    f"Choices: {', '.join(choices)}"
                ),
                placeholder=" | ".join(choices),
                choices=choices,
                current_value=current,
                config_backed=True,
                config_scopes=CONFIG_PERSISTENCE_SCOPES,
            ),
        )

    def _boolean_form(
        self,
        command: str,
        title: str,
        prompt: str,
        current: bool,
    ) -> SlashCommandResult:
        return self._choice_form(
            command,
            title,
            prompt,
            ("false", "true"),
            "true" if current else "false",
        )

    def _scoped_config_result(
        self,
        label: str,
        display_value: str,
        scope: str,
        update_factory: Callable[[], SessionConfigUpdate],
        *,
        refresh_header: bool = False,
        message_suffix: str = "",
    ) -> SlashCommandResult:
        previous_config = self.state.config
        update = update_factory()
        save_to_project = scope == "project"
        save_to_user = scope == "global"
        if save_to_project or save_to_user:
            self.state.config = previous_config
        scope_note = {
            "session": "session only",
            "project": "save to project requested",
            "global": "save to user config requested",
        }[scope]
        return SlashCommandResult(
            handled=True,
            message=(
                f"{label}: {display_value} ({scope_note})."
                f"{message_suffix}"
            ),
            refresh_header=refresh_header,
            config_update=update,
            save_config_to_project=save_to_project,
            save_config_to_user=save_to_user,
        )

    @staticmethod
    def _config_arguments(
        command: str,
        arguments: tuple[str, ...],
    ) -> tuple[tuple[str, ...], str]:
        values: list[str] = []
        scope: str | None = None
        for argument in arguments:
            if not argument.casefold().startswith("scope="):
                values.append(argument)
                continue
            if scope is not None:
                raise ValueError(f"/{command} accepts only one scope value.")
            scope = argument.split("=", 1)[1].strip().casefold()
            if scope not in CONFIG_PERSISTENCE_SCOPES:
                raise ValueError(
                    f"/{command} scope must be session, project, or global."
                )
        return tuple(values), scope or "session"

    @staticmethod
    def _action(
        name: str,
        arguments: dict[str, object],
        display: str,
    ) -> SlashCommandResult:
        return SlashCommandResult(
            handled=True,
            action=SlashActionRequest(
                name=name,
                arguments=arguments,
                display=display,
            ),
        )

    def _optional_boolean(
        self,
        command: str,
        arguments: tuple[str, ...],
        current: bool,
    ) -> bool:
        if not arguments:
            return not current
        value = self._one_argument(command, arguments).casefold()
        if value not in _BOOLEAN_VALUES:
            raise ValueError(
                f"/{command} expects on or off; received {arguments[0]!r}."
            )
        return _BOOLEAN_VALUES[value]

    @staticmethod
    def _one_argument(
        command: str,
        arguments: tuple[str, ...],
    ) -> str:
        if len(arguments) != 1:
            raise ValueError(
                f"/{command} expects exactly one argument."
            )
        return arguments[0]

    @staticmethod
    def _require_no_arguments(
        command: str,
        arguments: tuple[str, ...],
    ) -> None:
        if arguments:
            raise ValueError(f"/{command} does not accept arguments.")

    @staticmethod
    def _error(message: str) -> SlashCommandResult:
        return SlashCommandResult(
            handled=True,
            message=f"Command error: {message}",
            error=True,
        )


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def slash_command_hints(
    value: str,
    *,
    limit: int = 5,
) -> tuple[str, ...]:
    """Return a small deterministic command list for slash autocomplete."""

    if limit < 1:
        raise ValueError("Slash hint limit must be at least 1.")
    normalized = value.strip().casefold()
    if not normalized.startswith("/"):
        return ()
    if normalized == "/":
        return POPULAR_SLASH_COMMANDS[:limit]
    return tuple(
        command
        for command in SLASH_COMMANDS
        if command.casefold().startswith(normalized)
    )[:limit]


def _split_argument_text(value: str) -> tuple[str, ...]:
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    try:
        return tuple(lexer)
    except ValueError as exc:
        raise ValueError(f"Invalid slash-command quoting: {exc}") from exc


def _parse_browser_validation_arguments(
    arguments: tuple[str, ...],
) -> dict[str, object]:
    allowed = {
        "url",
        "serve",
        "screenshot",
        "no-screenshot",
        "full-page",
        "width",
        "height",
        "startup-timeout-ms",
        "check",
    }
    values, positional = _named_arguments(arguments, allowed)
    if positional:
        if len(positional) != 1 or "url" in values:
            raise ValueError(
                "/browser-validate accepts one positional URL; use "
                "key=value for other options."
            )
        values["url"] = [positional[0]]
    url = _one_named_value(values, "url", required=True)
    serve = _one_named_value(values, "serve")
    screenshot_values = values.get("screenshot", [])
    no_screenshot_values = values.get("no-screenshot", [])
    if screenshot_values and no_screenshot_values:
        raise ValueError(
            "Use screenshot or no-screenshot, not both."
        )
    screenshot = True
    if screenshot_values:
        screenshot = _boolean_argument(
            "screenshot",
            screenshot_values[-1],
        )
    if no_screenshot_values:
        screenshot = not _boolean_argument(
            "no-screenshot",
            no_screenshot_values[-1],
        )
    full_page = _optional_named_boolean(values, "full-page", False)
    width = _optional_named_integer(
        values,
        "width",
        DEFAULT_VIEWPORT_WIDTH,
    )
    height = _optional_named_integer(
        values,
        "height",
        DEFAULT_VIEWPORT_HEIGHT,
    )
    startup_timeout_ms = _optional_named_integer(
        values,
        "startup-timeout-ms",
        DEFAULT_SERVER_STARTUP_TIMEOUT_MS,
    )
    checks = tuple(values.get("check", ()))
    return {
        "url": url,
        "serve": serve,
        "screenshot": screenshot,
        "full_page": full_page,
        "width": width,
        "height": height,
        "startup_timeout_ms": startup_timeout_ms,
        "checks": checks,
    }


def _parse_git_commit_arguments(
    arguments: tuple[str, ...],
) -> dict[str, object]:
    values, positional = _named_arguments(
        arguments,
        {"message", "despite-failed-validation"},
    )
    named_message = _one_named_value(values, "message")
    if named_message is not None and positional:
        raise ValueError(
            "Quote the complete message or use message=\"...\"."
        )
    message = named_message or " ".join(positional).strip()
    if not message:
        raise ValueError("Git commit message must not be empty.")
    if len(message) > 200:
        raise ValueError(
            "Git commit message must not exceed 200 characters."
        )
    override = _optional_named_boolean(
        values,
        "despite-failed-validation",
        False,
    )
    return {
        "message": message,
        "despite_failed_validation": override,
    }


def _parse_rollback_arguments(
    arguments: tuple[str, ...],
) -> dict[str, object]:
    values, positional = _named_arguments(
        arguments,
        {"path", "checkpoint"},
    )
    named_path = _one_named_value(values, "path")
    if named_path is not None and positional:
        raise ValueError(
            "/rollback accepts one path. Use path=\"...\" for paths "
            "containing spaces."
        )
    if len(positional) > 1:
        raise ValueError(
            "/rollback accepts one project-relative path."
        )
    path = named_path or (positional[0] if positional else None)
    if path is None or not path.strip():
        raise ValueError("Rollback path must not be empty.")
    checkpoint = _one_named_value(values, "checkpoint")
    return {
        "path": path,
        "checkpoint": checkpoint,
    }


def _named_arguments(
    arguments: tuple[str, ...],
    allowed: set[str],
) -> tuple[dict[str, list[str]], list[str]]:
    values: dict[str, list[str]] = {}
    positional: list[str] = []
    index = 0
    boolean_flags = {
        "screenshot",
        "no-screenshot",
        "full-page",
        "despite-failed-validation",
    }
    while index < len(arguments):
        raw = arguments[index]
        token = raw[2:] if raw.startswith("--") else raw
        if "=" in token:
            key, value = token.split("=", 1)
        elif token in boolean_flags:
            key, value = token, "true"
        elif raw.startswith("--") or token in allowed:
            key = token
            index += 1
            if index >= len(arguments):
                raise ValueError(f"Option {raw!r} requires a value.")
            value = arguments[index]
        else:
            positional.append(raw)
            index += 1
            continue
        normalized_key = key.strip().casefold()
        if normalized_key not in allowed:
            raise ValueError(f"Unknown option: {key!r}.")
        if not value.strip():
            raise ValueError(f"Option {key!r} requires a non-empty value.")
        values.setdefault(normalized_key, []).append(value.strip())
        index += 1
    duplicates = [
        key
        for key, items in values.items()
        if len(items) > 1 and key != "check"
    ]
    if duplicates:
        raise ValueError(
            f"Option {duplicates[0]!r} may only be provided once."
        )
    return values, positional


def _one_named_value(
    values: dict[str, list[str]],
    key: str,
    *,
    required: bool = False,
) -> str | None:
    items = values.get(key, [])
    if not items:
        if required:
            raise ValueError(f"Option {key!r} is required.")
        return None
    return items[0]


def _optional_named_boolean(
    values: dict[str, list[str]],
    key: str,
    default: bool,
) -> bool:
    items = values.get(key, [])
    return default if not items else _boolean_argument(key, items[0])


def _boolean_argument(key: str, value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized not in _BOOLEAN_VALUES:
        raise ValueError(
            f"Option {key!r} expects true or false; received {value!r}."
        )
    return _BOOLEAN_VALUES[normalized]


def _optional_named_integer(
    values: dict[str, list[str]],
    key: str,
    default: int,
) -> int:
    items = values.get(key, [])
    if not items:
        return default
    try:
        value = int(items[0])
    except ValueError as exc:
        raise ValueError(
            f"Option {key!r} expects an integer."
        ) from exc
    if value <= 0:
        raise ValueError(f"Option {key!r} must be positive.")
    return value


def _display_action(name: str, arguments: dict[str, object]) -> str:
    keys = ", ".join(
        key for key, value in arguments.items() if value not in {None, (), False}
    )
    suffix = f" ({keys})" if keys else ""
    return f"/{name}{suffix}"


__all__ = [
    "CONFIG_PERSISTENCE_SCOPES",
    "POPULAR_SLASH_COMMANDS",
    "SLASH_COMMANDS",
    "SlashActionRequest",
    "SlashCommandForm",
    "SlashCommandPicker",
    "SlashCommandParser",
    "SlashCommandResult",
    "SlashCommandRouter",
    "SlashConfirmation",
    "SlashInvocation",
    "SlashPickerOption",
    "slash_command_hints",
]
