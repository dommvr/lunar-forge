"""Permission and path-safety helpers."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from lunar_forge.approvals import (
    ApprovalDecision as ProviderApprovalDecision,
    ApprovalProvider,
    ApprovalRequest as ProviderApprovalRequest,
    AutoApprovalProvider,
    CliApprovalProvider,
    DenyApprovalProvider,
)
from lunar_forge.events import sanitize_event_payload


class PermissionLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class PermissionRequest:
    """A mutation request shown to a user or test approval callback."""

    tool_name: str
    permission: PermissionLevel
    description: str


ApprovalCallback = Callable[
    [PermissionRequest],
    bool | PermissionDecision,
]
ApprovalEventCallback = Callable[[str, Mapping[str, Any]], None]


_DANGEROUS_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rm -rf", re.compile(r"\brm\s+-rf(?:\s|$)", re.IGNORECASE)),
    ("sudo", re.compile(r"\bsudo(?:\s|$)", re.IGNORECASE)),
    ("chmod -R", re.compile(r"\bchmod\s+-r(?:\s|$)", re.IGNORECASE)),
    ("chown -R", re.compile(r"\bchown\s+-r(?:\s|$)", re.IGNORECASE)),
    ("curl | sh", re.compile(r"\bcurl\b[\s\S]*\|\s*sh\b", re.IGNORECASE)),
    ("wget | sh", re.compile(r"\bwget\b[\s\S]*\|\s*sh\b", re.IGNORECASE)),
    ("ssh", re.compile(r"\bssh(?:\s|$)", re.IGNORECASE)),
    ("scp", re.compile(r"\bscp(?:\s|$)", re.IGNORECASE)),
    ("~/.ssh", re.compile(r"~[\\/]\.ssh(?:[\\/]|\b)", re.IGNORECASE)),
    (".env", re.compile(r"\.env(?:\b|$)", re.IGNORECASE)),
    (
        "raw docker run",
        re.compile(r"\bdocker\s+run\b", re.IGNORECASE),
    ),
    (
        "docker run --privileged",
        re.compile(r"\bdocker\s+run\b[\s\S]*--privileged\b", re.IGNORECASE),
    ),
    (
        "/var/run/docker.sock",
        re.compile(r"/var/run/docker\.sock(?:\b|$)", re.IGNORECASE),
    ),
)

_DISPLAY_SECRET_OPTION = re.compile(
    r"(?i)((?<!\w)--?(?:api[_-]?key|access[_-]?token|token|secret|password)"
    r"(?:\s*=\s*|\s+))([^\s]+)"
)
_DISPLAY_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*"
    r"\s*=\s*)([^\s]+)"
)
_DISPLAY_API_KEY = re.compile(
    r"(?i)\b(?:sk-(?:ant-)?|gh[pousr]_|github_pat_)[a-z0-9_-]{8,}\b"
)
_BARE_PYTHON_INTERPRETERS = frozenset(
    {"python", "python.exe", "py", "py.exe"}
)
_LOCAL_EXECUTION_WARNING = (
    "Local commands run as your user account on this machine. The project "
    "root is used as the working directory, but this is not OS-level "
    "isolation. Use Docker mode for untrusted projects or commands you have "
    "not reviewed."
)
_DOCKER_EXECUTION_NOTICE = (
    "This runs inside lunar-forge-sandbox with the project mounted at "
    "/workspace."
)
_LOCAL_COMMAND_TOOLS = frozenset({"run_command", "run_validation"})


def dangerous_command_reason(command: str) -> str | None:
    """Return the prohibited raw-command pattern, without parsing the command."""
    for label, pattern in _DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return label
    return None


def normalized_dangerous_command_reason(command: str) -> str | None:
    """Check a quote-normalized command after the mandatory raw check.

    This catches inputs such as ``rm '-rf'`` or ``s'u'do`` that resolve to a
    denylisted command after shell-style tokenization. Callers must still run
    :func:`dangerous_command_reason` on the untouched input first.
    """
    try:
        normalized = " ".join(shlex.split(command, posix=True))
    except ValueError:
        return None
    return dangerous_command_reason(normalized)


def is_bare_python_interpreter_command(command: str) -> bool:
    """Return whether a command only starts an interactive Python interpreter."""
    if not isinstance(command, str) or not command.strip():
        return False
    try:
        arguments = shlex.split(command, posix=True)
    except ValueError:
        return False
    if len(arguments) != 1:
        return False
    executable = arguments[0].replace("\\", "/").rsplit("/", 1)[-1]
    return executable.casefold() in _BARE_PYTHON_INTERPRETERS


def is_dependency_install_command(command: str) -> bool:
    """Return whether a command is a supported dependency-install shape."""
    arguments = _normalized_command_arguments(command)
    if not arguments:
        return False
    executable = _executable_name(arguments[0])
    tail = [argument.casefold() for argument in arguments[1:]]
    if executable == "npm":
        return bool(tail and tail[0] in {"install", "ci"})
    if executable in {"pnpm", "yarn", "poetry"}:
        return bool(tail and tail[0] == "install")
    if executable in {"pip", "pip3"}:
        return bool(tail and tail[0] == "install")
    if executable == "uv":
        return len(tail) >= 2 and tail[:2] == ["pip", "install"]
    if executable in {"python", "py"}:
        return len(tail) >= 3 and tail[:3] == ["-m", "pip", "install"]
    return False


def is_risky_allowed_command(command: str) -> bool:
    """Return whether an allowed command directly executes project code."""
    arguments = _normalized_command_arguments(command)
    if not arguments:
        return False
    executable = _executable_name(arguments[0])
    tail = [argument.casefold() for argument in arguments[1:]]
    if not tail:
        return False
    if executable in {"npm", "pnpm"}:
        return tail[0] == "run" or tail[0] in {"dev", "test"}
    if executable == "yarn":
        return tail[0] not in {
            "--version",
            "-v",
            "add",
            "cache",
            "config",
            "help",
            "init",
            "install",
            "remove",
            "set",
        }
    if executable in {"python", "py"}:
        script = _first_non_option_argument(tail)
        if script is not None and script.endswith(".py"):
            return True
        return len(tail) >= 2 and tail[:2] in (
            ["-m", "pytest"],
            ["-m", "unittest"],
        )
    if executable == "node":
        script = _first_non_option_argument(tail)
        return script is not None and script.endswith(
            (".js", ".cjs", ".mjs")
        )
    if executable == "flask":
        return tail[0] == "run"
    if executable == "fastapi":
        return tail[0] == "dev"
    if executable == "uvicorn":
        return any(
            ":" in argument
            for argument in tail
            if not argument.startswith("-")
        )
    return executable in {"pytest", "pytest.exe"}


@dataclass
class PermissionManager:
    """Apply mode rules before a tool handler can mutate project state."""

    mode: str = "default"
    approval_callback: ApprovalCallback | None = None
    runtime_mode: str = "local"
    project_trust: str = "trusted"
    approval_provider: ApprovalProvider | None = None
    approval_event_callback: ApprovalEventCallback | None = None
    _local_command_warning_shown: bool = field(
        default=False,
        init=False,
        repr=False,
    )

    def authorize(
        self,
        permission: PermissionLevel,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        plan_safe: bool = False,
    ) -> PermissionDecision:
        normalized_mode = self.mode.strip().lower()
        if permission is PermissionLevel.READ:
            return PermissionDecision(allowed=True)
        if permission is PermissionLevel.EXECUTE:
            command = arguments.get("command")
            if command is not None and not isinstance(command, str):
                return PermissionDecision(
                    allowed=False,
                    reason="Command must be a string.",
                )
            if isinstance(command, str):
                if (
                    tool_name == "run_command"
                    and is_bare_python_interpreter_command(command)
                ):
                    return PermissionDecision(
                        allowed=False,
                        reason=(
                            "Bare Python interpreter commands are not meaningful "
                            "checks. Use a module, script, or compile command."
                        ),
                    )
                dangerous_pattern = dangerous_command_reason(command)
                if dangerous_pattern is None:
                    dangerous_pattern = normalized_dangerous_command_reason(command)
                if dangerous_pattern is not None:
                    return PermissionDecision(
                        allowed=False,
                        reason=(
                            "Command blocked by safety policy: matched prohibited "
                            f"pattern {dangerous_pattern!r}."
                        ),
                    )
            elif tool_name == "run_command":
                return PermissionDecision(
                    allowed=False,
                    reason="Command must be a string.",
                )
        if normalized_mode == "plan" and not (
            permission is PermissionLevel.NETWORK and plan_safe
        ):
            return PermissionDecision(
                allowed=False,
                reason="Plan mode blocks write and execution tools.",
            )
        if (
            normalized_mode == "no-command"
            and permission is PermissionLevel.EXECUTE
        ):
            return PermissionDecision(
                allowed=False,
                reason="No-command mode blocks command execution.",
            )
        legacy_request = PermissionRequest(
            tool_name=tool_name,
            permission=permission,
            description=self._describe_request(tool_name, arguments),
        )
        request = self._structured_request(
            legacy_request,
            arguments,
            normalized_mode=normalized_mode,
        )
        provider = self._approval_provider(legacy_request)
        if normalized_mode == "yes":
            provider = AutoApprovalProvider(fallback=provider)
        self._emit_approval_event("permission.requested", request.to_dict())
        try:
            response = provider.request_approval(request)
            if not isinstance(response, ProviderApprovalDecision):
                raise TypeError(
                    "Approval providers must return ApprovalDecision."
                )
            if response.request_id != request.id:
                raise ValueError(
                    "Approval decision request_id did not match the request."
                )
        except Exception:
            response = ProviderApprovalDecision.create(
                request.id,
                approved=False,
                reason="Permission prompt failed; the action was not run.",
                source="deny",
            )
        decision_payload = response.to_dict()
        decision_payload.update(
            {
                "kind": request.kind,
                "tool_name": request.tool_name,
                "risk": request.risk,
                "mode": request.mode,
            }
        )
        self._emit_approval_event(
            "permission.resolved",
            decision_payload,
        )
        return PermissionDecision(
            allowed=response.approved,
            reason=response.reason,
        )

    def _approval_provider(
        self,
        legacy_request: PermissionRequest,
    ) -> ApprovalProvider:
        if self.approval_provider is not None:
            return self.approval_provider
        if self.approval_callback is not None:
            return _LegacyCallbackApprovalProvider(
                callback=self.approval_callback,
                request=legacy_request,
            )
        return DenyApprovalProvider()

    def _structured_request(
        self,
        legacy_request: PermissionRequest,
        arguments: Mapping[str, Any],
        *,
        normalized_mode: str,
    ) -> ProviderApprovalRequest:
        tool_name = legacy_request.tool_name
        command = arguments.get("command")
        command_preview = (
            _command_preview(command) if isinstance(command, str) else None
        )
        file_path = _request_file_path(arguments)
        kind = _approval_kind(tool_name, legacy_request.permission)
        mode = _approval_mode(
            kind,
            normalized_mode,
            self.runtime_mode,
        )
        risk = _approval_risk(
            kind,
            command if isinstance(command, str) else None,
            legacy_request.permission,
        )
        title = _approval_title(kind)
        summary = _approval_summary(
            kind,
            legacy_request.description,
            command_preview=command_preview,
            file_path=file_path,
            tool_name=tool_name,
            message=arguments.get("message"),
        )
        metadata: dict[str, Any] = {
            "permission": legacy_request.permission.value,
            "permission_mode": normalized_mode,
            "runtime_mode": self.runtime_mode.strip().lower(),
            "dependency_install": (
                isinstance(command, str)
                and is_dependency_install_command(command)
            ),
            "risky_command": (
                isinstance(command, str)
                and is_risky_allowed_command(command)
            ),
        }
        proposed_files = arguments.get("proposed_files")
        if isinstance(proposed_files, (list, tuple)):
            metadata["proposed_files"] = [
                str(path) for path in proposed_files[:100]
            ]
        message = arguments.get("message")
        if isinstance(message, str):
            metadata["message"] = message[:500]
        return ProviderApprovalRequest.create(
            kind=kind,
            title=title,
            summary=summary,
            details=legacy_request.description,
            risk=risk,
            mode=mode,
            default=False,
            command=command_preview,
            tool_name=tool_name,
            file_path=file_path,
            metadata=metadata,
        )

    def _emit_approval_event(
        self,
        event: str,
        payload: Mapping[str, Any],
    ) -> None:
        if self.approval_event_callback is None:
            return
        try:
            self.approval_event_callback(
                event,
                sanitize_event_payload(payload),
            )
        except Exception:
            # Approval telemetry must not alter the safety decision.
            return

    def _describe_request(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> str:
        if (
            tool_name not in _LOCAL_COMMAND_TOOLS
            or self.runtime_mode.strip().lower() not in {"local", "docker"}
        ):
            return _describe_request(tool_name, arguments)

        command = arguments.get("command")
        preview = _command_preview(command) if isinstance(command, str) else None
        if self.runtime_mode.strip().lower() == "docker":
            heading = (
                f"Run Docker command: {preview}"
                if preview is not None
                else "Run Docker validation commands"
            )
            return f"{heading}\n{_DOCKER_EXECUTION_NOTICE}"

        full_warning = (
            not self._local_command_warning_shown
            or self.project_trust.strip().lower() in {"untrusted", "unknown"}
            or (
                isinstance(command, str)
                and (
                    is_dependency_install_command(command)
                    or is_risky_allowed_command(command)
                )
            )
            or tool_name == "run_validation"
        )
        heading = (
            f"Run local command: {preview}"
            if preview is not None
            else "Run local validation commands"
        )
        if full_warning:
            self._local_command_warning_shown = True
            return f"{heading}\n\n{_LOCAL_EXECUTION_WARNING}"
        return f"{heading}."


def prompt_for_approval(request: PermissionRequest) -> PermissionDecision:
    """Compatibility adapter for callers that still use PermissionRequest."""
    provider_request = ProviderApprovalRequest.create(
        kind=_approval_kind(request.tool_name, request.permission),
        title=_approval_title(
            _approval_kind(request.tool_name, request.permission)
        ),
        summary=request.description.splitlines()[0].rstrip("."),
        details=request.description,
        risk="medium",
        mode="default",
        tool_name=request.tool_name,
    )
    decision = CliApprovalProvider().request_approval(provider_request)
    return PermissionDecision(
        allowed=decision.approved,
        reason=decision.reason,
    )


@dataclass(slots=True)
class _LegacyCallbackApprovalProvider:
    callback: ApprovalCallback
    request: PermissionRequest

    def request_approval(
        self,
        structured_request: ProviderApprovalRequest,
    ) -> ProviderApprovalDecision:
        response = self.callback(self.request)
        if isinstance(response, PermissionDecision):
            approved = response.allowed
            reason = response.reason
        else:
            approved = response is True
            reason = "Approved by user." if approved else "Denied by user."
        return ProviderApprovalDecision.create(
            structured_request.id,
            approved=approved,
            reason=reason,
            source="cli",
        )


def _approval_kind(
    tool_name: str,
    permission: PermissionLevel,
) -> str:
    if tool_name == "git_commit":
        return "git_commit"
    if tool_name == "run_managed_browser_validation":
        return "browser_server"
    if tool_name.startswith("mcp."):
        return "mcp_tool"
    if "." in tool_name:
        return "plugin_tool"
    if permission is PermissionLevel.WRITE:
        return "write"
    return "command"


def _approval_mode(
    kind: str,
    permission_mode: str,
    runtime_mode: str,
) -> str:
    if permission_mode in {"plan", "no-command"}:
        return permission_mode
    normalized_runtime = runtime_mode.strip().lower()
    if kind in {"command", "browser_server"} and normalized_runtime in {
        "local",
        "docker",
    }:
        return normalized_runtime
    return "default"


def _approval_risk(
    kind: str,
    command: str | None,
    permission: PermissionLevel,
) -> str:
    if command is not None and is_dependency_install_command(command):
        return "high"
    if kind in {"git_commit", "browser_server", "mcp_tool", "plugin_tool"}:
        return "high" if permission is PermissionLevel.EXECUTE else "medium"
    if command is not None and is_risky_allowed_command(command):
        return "high"
    if kind == "write":
        return "low"
    return "medium"


def _approval_title(kind: str) -> str:
    return {
        "command": "Run command",
        "write": "Change project files",
        "git_commit": "Create Git commit",
        "browser_server": "Start browser validation server",
        "mcp_tool": "Run MCP tool",
        "plugin_tool": "Run plugin tool",
    }[kind]


def _approval_summary(
    kind: str,
    description: str,
    *,
    command_preview: str | None,
    file_path: str | None,
    tool_name: str,
    message: Any,
) -> str:
    if kind == "git_commit" and isinstance(message, str) and message.strip():
        return f"Commit: {message.strip()[:500]}"
    if command_preview is not None:
        return command_preview
    if kind == "write" and file_path is not None:
        return f"{tool_name}: {file_path}"
    if kind in {"mcp_tool", "plugin_tool"}:
        return tool_name
    return description.splitlines()[0].rstrip(".")


def _request_file_path(arguments: Mapping[str, Any]) -> str | None:
    for key in ("path", "destination", "file_path"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value[:4_000]
    return None


def is_subpath(path: str | Path, root: str | Path) -> bool:
    resolved_path = Path(path).expanduser().resolve()
    resolved_root = Path(root).expanduser().resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def assert_within_root(path: str | Path, root: str | Path) -> Path:
    resolved_path = Path(path).expanduser().resolve()
    if not is_subpath(resolved_path, root):
        raise PermissionError(f"Path is outside project root: {resolved_path}")
    return resolved_path


def requires_approval(
    permission: PermissionLevel,
    auto_approved: set[PermissionLevel] | None = None,
) -> bool:
    allowed = auto_approved if auto_approved is not None else {PermissionLevel.READ}
    return permission not in allowed


def _describe_request(tool_name: str, arguments: Mapping[str, Any]) -> str:
    if tool_name == "git_commit":
        preview = arguments.get("preview")
        if isinstance(preview, str) and preview.strip():
            return preview.strip()
        return "Create the proposed Git commit."
    if tool_name in {"run_command", "run_managed_browser_validation"}:
        command = arguments.get("command")
        if isinstance(command, str):
            preview = _redact_command_preview(" ".join(command.split()))
            if len(preview) > 200:
                preview = f"{preview[:197]}..."
            if tool_name == "run_managed_browser_validation":
                return f"Start managed dev server: {preview}."
            return f"Run command: {preview}."
        if tool_name == "run_managed_browser_validation":
            return "Start managed dev server."
        return "Run command."
    if tool_name == "run_validation":
        return "Run detected validation commands."

    path = arguments.get("path")
    path_description = f" for {path}" if isinstance(path, str) and path else ""
    actions = {
        "create_dir": "Create directory",
        "write_file": "Write file",
        "edit_file": "Edit file",
    }
    action = actions.get(tool_name, f"Run {tool_name}")
    return f"{action}{path_description}."


def _command_preview(command: str) -> str:
    preview = _redact_command_preview(" ".join(command.split()))
    return f"{preview[:197]}..." if len(preview) > 200 else preview


def _normalized_command_arguments(command: str) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        return []
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def _executable_name(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _first_non_option_argument(arguments: list[str]) -> str | None:
    return next(
        (argument for argument in arguments if not argument.startswith("-")),
        None,
    )


def _redact_command_preview(command: str) -> str:
    redacted = _DISPLAY_SECRET_OPTION.sub(r"\1[REDACTED]", command)
    redacted = _DISPLAY_SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", redacted)
    return _DISPLAY_API_KEY.sub("[REDACTED]", redacted)
