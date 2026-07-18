"""Permission and path-safety helpers."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


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


@dataclass
class PermissionManager:
    """Apply mode rules before a tool handler can mutate project state."""

    mode: str = "default"
    approval_callback: ApprovalCallback | None = None

    def authorize(
        self,
        permission: PermissionLevel,
        tool_name: str,
        arguments: Mapping[str, Any],
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
        if normalized_mode == "plan":
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
        if normalized_mode == "yes" and permission is PermissionLevel.WRITE:
            return PermissionDecision(allowed=True, reason="Auto-approved by yes mode.")

        request = PermissionRequest(
            tool_name=tool_name,
            permission=permission,
            description=_describe_request(tool_name, arguments),
        )
        callback = self.approval_callback or prompt_for_approval
        try:
            response = callback(request)
        except Exception:
            return PermissionDecision(
                allowed=False,
                reason="Permission prompt failed; the action was not run.",
            )
        if isinstance(response, PermissionDecision):
            return response
        if response is True:
            return PermissionDecision(allowed=True, reason="Approved by user.")
        return PermissionDecision(allowed=False, reason="Denied by user.")


def prompt_for_approval(request: PermissionRequest) -> PermissionDecision:
    """Ask for interactive approval without echoing file contents."""
    try:
        answer = input(f"{request.description} Allow? [y/N] ").strip().lower()
    except (EOFError, OSError, KeyboardInterrupt):
        return PermissionDecision(
            allowed=False,
            reason="Approval was unavailable or cancelled.",
        )
    if answer in {"y", "yes"}:
        return PermissionDecision(allowed=True, reason="Approved by user.")
    return PermissionDecision(allowed=False, reason="Denied by user.")


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
    if tool_name == "run_command":
        command = arguments.get("command")
        if isinstance(command, str):
            preview = _redact_command_preview(" ".join(command.split()))
            if len(preview) > 200:
                preview = f"{preview[:197]}..."
            return f"Run command: {preview}."
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


def _redact_command_preview(command: str) -> str:
    redacted = _DISPLAY_SECRET_OPTION.sub(r"\1[REDACTED]", command)
    redacted = _DISPLAY_SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", redacted)
    return _DISPLAY_API_KEY.sub("[REDACTED]", redacted)
