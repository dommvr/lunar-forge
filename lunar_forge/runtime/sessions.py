"""Redacted JSONL session logging inside a target project."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from lunar_forge.tools.files import safe_path


REDACTED = "[REDACTED]"
SESSION_ERROR = "Session logging failed."

_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "credentials",
        "authorization",
        "cookie",
        "privatekey",
    }
)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|token|secret|password|"
    r"authorization)\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")
_API_KEY_PATTERN = re.compile(
    r"(?i)\b(?:sk-(?:ant-)?|gh[pousr]_|github_pat_)[a-z0-9_-]{8,}\b"
)


@dataclass
class SessionLogger:
    """Append sanitized events to one project-local JSONL file."""

    project_root: Path
    path: Path
    _environment_names: frozenset[str] = field(repr=False)
    _environment_values: tuple[str, ...] = field(repr=False)
    last_error: str | None = field(default=None, init=False)

    @property
    def relative_path(self) -> str:
        return self.path.relative_to(self.project_root).as_posix()

    def log(self, event: str, **data: Any) -> bool:
        """Append one redacted event, returning false instead of raising on failure."""
        try:
            if not isinstance(event, str) or not event.strip():
                raise ValueError("Event name must be a non-empty string.")
            safe_log_path = safe_path(self.project_root, self.path)
            if safe_log_path != self.path:
                raise PermissionError("Session path changed unexpectedly.")
            record = {
                "timestamp": _timestamp(),
                "event": _redact_string(event, self._environment_values),
                "data": _sanitize(
                    data,
                    self._environment_names,
                    self._environment_values,
                ),
            }
            serialized = json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            with safe_log_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(f"{serialized}\n")
        except Exception:
            self.last_error = SESSION_ERROR
            return False

        self.last_error = None
        return True


def create_session_logger(
    project_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> SessionLogger:
    """Create a unique session file beneath ``.agent/sessions``."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {root}")

    sessions_directory = safe_path(root, ".agent/sessions")
    sessions_directory.mkdir(parents=True, exist_ok=True)
    filename = f"{_filename_timestamp()}-{uuid4().hex[:8]}.jsonl"
    session_path = safe_path(root, sessions_directory / filename)
    session_path.touch(mode=0o600, exist_ok=False)

    environment = os.environ if environ is None else environ
    values = tuple(
        sorted(
            {value for value in environment.values() if value},
            key=len,
            reverse=True,
        )
    )
    return SessionLogger(
        project_root=root,
        path=session_path,
        _environment_names=frozenset(environment),
        _environment_values=values,
    )


def create_session(
    project_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> SessionLogger:
    """Compatibility-friendly alias for creating a session logger."""
    return create_session_logger(project_root, environ)


def _sanitize(
    value: Any,
    environment_names: frozenset[str],
    environment_values: tuple[str, ...],
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_string(value, environment_values)
    if isinstance(value, Path):
        return _redact_string(str(value), environment_values)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            safe_key = _redact_string(key, environment_values)
            if _is_sensitive_key(key) or key in environment_names:
                sanitized[safe_key] = REDACTED
            else:
                sanitized[safe_key] = _sanitize(
                    item,
                    environment_names,
                    environment_values,
                )
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            _sanitize(item, environment_names, environment_values)
            for item in value
        ]
    return _redact_string(str(value), environment_values)


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    sensitive_suffixes = (
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "credentials",
        "authorization",
        "cookie",
        "privatekey",
    )
    return normalized in _SENSITIVE_KEYS or normalized.endswith(sensitive_suffixes)


def _redact_string(value: str, environment_values: tuple[str, ...]) -> str:
    redacted = value
    for environment_value in environment_values:
        if redacted == environment_value:
            return REDACTED
        if len(environment_value) >= 4 and environment_value in redacted:
            redacted = redacted.replace(environment_value, REDACTED)
        elif environment_value in redacted:
            redacted = re.sub(
                rf"(?<!\w){re.escape(environment_value)}(?!\w)",
                REDACTED,
                redacted,
            )
    redacted = _ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _BEARER_PATTERN.sub(r"\1[REDACTED]", redacted)
    return _API_KEY_PATTERN.sub(REDACTED, redacted)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _filename_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


# Retained as an in-memory compatibility helper for earlier callers.
@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid4().hex)
    messages: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.messages.append(message)


__all__ = [
    "REDACTED",
    "SESSION_ERROR",
    "Session",
    "SessionLogger",
    "create_session",
    "create_session_logger",
]
