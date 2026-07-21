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
MAX_LOG_STRING_CHARACTERS = 50_000
MAX_LOG_RECORD_CHARACTERS = 200_000
MAX_LOG_COLLECTION_ITEMS = 200
MAX_LOG_NESTING = 20
MAX_SESSION_LIST_ENTRIES = 200
MAX_SESSION_FILE_BYTES = 5_000_000
MAX_RESUME_EVENTS = 1_000
MAX_RESUME_MESSAGES = 100
MAX_RESUME_CONTEXT_CHARACTERS = 50_000
MAX_SUMMARY_PREVIEW_CHARACTERS = 500
_STRING_TRUNCATION_MARKER = "\n...[session value truncated]"
_COLLECTION_TRUNCATION_MARKER = "[session collection truncated]"
_HISTORY_TRUNCATION_MARKER = "\n...[historical context truncated]"

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
                allow_nan=False,
                separators=(",", ":"),
            )
            if len(serialized) > MAX_LOG_RECORD_CHARACTERS:
                record["data"] = {
                    "truncated": True,
                    "preview": _redact_string(
                        serialized,
                        self._environment_values,
                    ),
                }
                serialized = json.dumps(
                    record,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            with safe_log_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(f"{serialized}\n")
        except Exception:
            self.last_error = SESSION_ERROR
            return False

        self.last_error = None
        return True


@dataclass(frozen=True)
class LoadedSession:
    """A validated, redacted session ready for summary or continuation."""

    project_root: Path
    path: Path
    safe_display_path: str
    events: tuple[dict[str, Any], ...]
    messages: tuple[dict[str, str], ...]

    @property
    def relative_path(self) -> str:
        return self.path.relative_to(self.project_root).as_posix()


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
    names, values = _redaction_context(environment)
    return SessionLogger(
        project_root=root,
        path=session_path,
        _environment_names=names,
        _environment_values=values,
    )


def create_session(
    project_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> SessionLogger:
    """Compatibility-friendly alias for creating a session logger."""
    return create_session_logger(project_root, environ)


def list_session_files(project_root: str | Path) -> dict[str, object]:
    """List session filenames and sizes without reading JSONL contents."""
    try:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Project root is not a directory: {root}")
        sessions_directory = safe_path(root, ".agent/sessions")
        if not sessions_directory.exists():
            return {
                "ok": True,
                "message": "No sessions found.",
                "sessions": [],
            }
        if not sessions_directory.is_dir():
            raise NotADirectoryError(".agent/sessions is not a directory.")

        sessions: list[dict[str, object]] = []
        truncated = False
        for entry in sorted(
            sessions_directory.iterdir(),
            key=lambda item: item.name,
            reverse=True,
        ):
            safe_entry = safe_path(root, entry)
            if not safe_entry.is_file() or safe_entry.suffix != ".jsonl":
                continue
            if len(sessions) >= MAX_SESSION_LIST_ENTRIES:
                truncated = True
                break
            sessions.append(
                {
                    "name": safe_entry.name,
                    "path": safe_entry.relative_to(root).as_posix(),
                    "size": safe_entry.stat().st_size,
                }
            )
        return {
            "ok": True,
            "message": (
                f"Found {len(sessions)} session file(s)"
                f"{' (list truncated).' if truncated else '.'}"
            ),
            "sessions": sessions,
            "truncated": truncated,
        }
    except (OSError, PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "sessions": []}


def resolve_session_file(
    project_root: str | Path,
    session_id_or_file: str | Path,
) -> Path:
    """Resolve an ID or filename to a direct project-local session JSONL file."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {root}")

    sessions_directory = safe_path(root, ".agent/sessions")
    if not sessions_directory.is_dir():
        raise FileNotFoundError("No sessions were found under .agent/sessions.")

    identifier = str(session_id_or_file).strip()
    if not identifier:
        raise ValueError("Session ID or filename must not be empty.")
    requested = Path(identifier).expanduser()

    if requested.is_absolute() or requested.parent != Path("."):
        if not requested.suffix:
            requested = requested.with_suffix(".jsonl")
        candidate = safe_path(root, requested)
        return _validate_session_file(root, sessions_directory, candidate)

    direct_name = identifier if identifier.lower().endswith(".jsonl") else (
        f"{identifier}.jsonl"
    )
    direct = safe_path(root, sessions_directory / direct_name)
    if direct.exists():
        return _validate_session_file(root, sessions_directory, direct)

    normalized_id = identifier.removesuffix(".jsonl").casefold()
    matches: list[Path] = []
    for entry in sessions_directory.iterdir():
        try:
            safe_entry = safe_path(root, entry)
        except PermissionError:
            continue
        if not safe_entry.is_file() or safe_entry.suffix.lower() != ".jsonl":
            continue
        stem = safe_entry.stem.casefold()
        if stem == normalized_id or stem.endswith(f"-{normalized_id}"):
            matches.append(safe_entry)

    if not matches:
        raise FileNotFoundError("Session was not found under .agent/sessions.")
    if len(matches) > 1:
        raise ValueError("Session ID is ambiguous; provide the complete filename.")
    return _validate_session_file(root, sessions_directory, matches[0])


def load_session(
    project_root: str | Path,
    session_id_or_file: str | Path,
    environ: Mapping[str, str] | None = None,
) -> LoadedSession:
    """Load and redact bounded historical events without replaying any action."""
    root = Path(project_root).expanduser().resolve()
    session_path = resolve_session_file(root, session_id_or_file)
    if session_path.stat().st_size > MAX_SESSION_FILE_BYTES:
        raise ValueError(
            f"Session file exceeds the {MAX_SESSION_FILE_BYTES}-byte load limit."
        )

    environment = os.environ if environ is None else environ
    environment_names, environment_values = _redaction_context(environment)
    events: list[dict[str, Any]] = []
    with session_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            if len(events) >= MAX_RESUME_EVENTS:
                raise ValueError(
                    f"Session contains more than {MAX_RESUME_EVENTS} events."
                )
            try:
                raw_event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Session contains invalid JSON on line {line_number}."
                ) from exc
            events.append(
                _sanitize_loaded_event(
                    raw_event,
                    line_number=line_number,
                    environment_names=environment_names,
                    environment_values=environment_values,
                )
            )

    if not events:
        raise ValueError("Session file contains no events.")
    return LoadedSession(
        project_root=root,
        path=session_path,
        safe_display_path=_redact_string(
            session_path.relative_to(root).as_posix(),
            environment_values,
        ),
        events=tuple(events),
        messages=tuple(_reconstruct_messages(events)),
    )


def summarize_session(session: LoadedSession) -> dict[str, Any]:
    """Return a bounded, redacted, JSON-serializable session summary."""
    counts = {
        "user_prompts": 0,
        "assistant_messages": 0,
        "tool_calls": 0,
        "tool_results": 0,
        "permission_denials": 0,
        "errors": 0,
    }
    last_user_prompt: str | None = None
    last_assistant_message: str | None = None
    for event in session.events:
        event_name = event["event"]
        data = event["data"]
        if event_name == "user_prompt":
            counts["user_prompts"] += 1
            last_user_prompt = _summary_preview(data.get("prompt"))
        elif event_name == "assistant_message":
            counts["assistant_messages"] += 1
            text = _summary_preview(data.get("text"))
            if text:
                last_assistant_message = text
        elif event_name == "tool_call":
            counts["tool_calls"] += 1
        elif event_name == "tool_result":
            counts["tool_results"] += 1
        elif event_name == "permission_denial":
            counts["permission_denials"] += 1
        elif event_name == "error":
            counts["errors"] += 1

    return {
        "session": session.safe_display_path,
        "event_count": len(session.events),
        "first_timestamp": session.events[0]["timestamp"],
        "last_timestamp": session.events[-1]["timestamp"],
        **counts,
        "historical_context_messages": len(session.messages),
        "last_user_prompt": last_user_prompt,
        "last_assistant_message": last_assistant_message,
    }


def format_session_summary(session: LoadedSession) -> str:
    """Format a safe summary without invoking config, models, or tools."""
    summary = summarize_session(session)
    lines = [
        f"Session: {summary['session']}",
        f"Events: {summary['event_count']}",
        f"Time range: {summary['first_timestamp']} to {summary['last_timestamp']}",
        f"User prompts: {summary['user_prompts']}",
        f"Assistant messages: {summary['assistant_messages']}",
        f"Tool calls: {summary['tool_calls']}",
        f"Historical tool results: {summary['tool_results']}",
        f"Permission denials: {summary['permission_denials']}",
        f"Errors: {summary['errors']}",
    ]
    if summary["last_user_prompt"]:
        lines.append(f"Last user prompt: {summary['last_user_prompt']}")
    if summary["last_assistant_message"]:
        lines.append(
            f"Last assistant message: {summary['last_assistant_message']}"
        )
    lines.append("Historical tool results are context only and are never replayed.")
    return "\n".join(lines)


def _validate_session_file(
    root: Path,
    sessions_directory: Path,
    candidate: Path,
) -> Path:
    safe_candidate = safe_path(root, candidate)
    if safe_candidate.parent != sessions_directory:
        raise PermissionError(
            "Session file must be directly inside .agent/sessions."
        )
    if safe_candidate.suffix.lower() != ".jsonl":
        raise ValueError("Session file must use the .jsonl extension.")
    if not safe_candidate.is_file():
        raise FileNotFoundError("Session was not found under .agent/sessions.")
    return safe_candidate


def _sanitize_loaded_event(
    raw_event: Any,
    *,
    line_number: int,
    environment_names: frozenset[str],
    environment_values: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(raw_event, Mapping):
        raise ValueError(f"Session event on line {line_number} must be an object.")
    event_name = raw_event.get("event")
    if not isinstance(event_name, str) or not event_name.strip():
        raise ValueError(
            f"Session event on line {line_number} has no valid event name."
        )
    raw_data = raw_event.get("data", {})
    if not isinstance(raw_data, Mapping):
        raw_data = {"value": raw_data}
    timestamp = raw_event.get("timestamp", "unknown")
    return {
        "timestamp": _redact_string(str(timestamp), environment_values),
        "event": _redact_string(event_name, environment_values),
        "data": _sanitize(
            raw_data,
            environment_names,
            environment_values,
        ),
    }


def _reconstruct_messages(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for event in events:
        message = _historical_message(event)
        if message is not None:
            candidates.append(message)

    selected: list[dict[str, str]] = []
    remaining_characters = MAX_RESUME_CONTEXT_CHARACTERS
    for message in reversed(candidates):
        if len(selected) >= MAX_RESUME_MESSAGES or remaining_characters <= 0:
            break
        content = message["content"]
        if len(content) > remaining_characters:
            keep = max(
                0,
                remaining_characters - len(_HISTORY_TRUNCATION_MARKER),
            )
            content = f"{content[:keep]}{_HISTORY_TRUNCATION_MARKER}"
        selected.append({"role": message["role"], "content": content})
        remaining_characters -= len(content)
    selected.reverse()
    return selected


def _historical_message(event: Mapping[str, Any]) -> dict[str, str] | None:
    event_name = str(event["event"])
    data = event["data"]
    if event_name == "user_prompt":
        prompt = str(data.get("prompt", "")).strip()
        if prompt:
            return {
                "role": "user",
                "content": f"[Historical user prompt]\n{prompt}",
            }
    elif event_name == "assistant_message":
        text = str(data.get("text", "")).strip()
        if text:
            return {
                "role": "assistant",
                "content": f"[Historical assistant message]\n{text}",
            }
    elif event_name == "tool_call":
        return {
            "role": "user",
            "content": (
                "[Historical tool call; do not execute or replay]\n"
                f"Tool: {data.get('name', 'unknown')}\n"
                f"Arguments: {_context_json(data.get('arguments', {}))}"
            ),
        }
    elif event_name == "tool_result":
        return {
            "role": "user",
            "content": (
                "[Historical tool result; context only, never replay]\n"
                f"Tool: {data.get('name', 'unknown')}\n"
                f"Result: {_context_json(data.get('result', {}))}"
            ),
        }
    elif event_name == "permission_denial":
        return {
            "role": "user",
            "content": (
                "[Historical permission denial; context only]\n"
                f"Tool: {data.get('name', 'unknown')}\n"
                f"Reason: {data.get('reason', 'Permission denied.')}"
            ),
        }
    elif event_name == "error":
        return {
            "role": "user",
            "content": (
                "[Historical error; context only]\n"
                f"{data.get('message', 'Unknown error.')}"
            ),
        }
    return None


def _context_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _summary_preview(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= MAX_SUMMARY_PREVIEW_CHARACTERS:
        return text
    return f"{text[:MAX_SUMMARY_PREVIEW_CHARACTERS]}...[truncated]"


def _redaction_context(
    environment: Mapping[str, str],
) -> tuple[frozenset[str], tuple[str, ...]]:
    values = tuple(
        sorted(
            {value for value in environment.values() if value},
            key=len,
            reverse=True,
        )
    )
    return frozenset(environment), values


def _sanitize(
    value: Any,
    environment_names: frozenset[str],
    environment_values: tuple[str, ...],
    depth: int = 0,
) -> Any:
    if depth >= MAX_LOG_NESTING:
        return _COLLECTION_TRUNCATION_MARKER
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_string(value, environment_values)
    if isinstance(value, Path):
        return _redact_string(str(value), environment_values)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= MAX_LOG_COLLECTION_ITEMS:
                sanitized["__lunar_forge_truncated__"] = True
                break
            key = str(raw_key)
            safe_key = _redact_string(key, environment_values)
            if _is_sensitive_key(key) or key in environment_names:
                sanitized[safe_key] = REDACTED
            else:
                sanitized[safe_key] = _sanitize(
                    item,
                    environment_names,
                    environment_values,
                    depth + 1,
                )
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        sanitized_items: list[Any] = []
        for index, item in enumerate(value):
            if index >= MAX_LOG_COLLECTION_ITEMS:
                sanitized_items.append(_COLLECTION_TRUNCATION_MARKER)
                break
            sanitized_items.append(
                _sanitize(
                    item,
                    environment_names,
                    environment_values,
                    depth + 1,
                )
            )
        return sanitized_items
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
    redacted = _API_KEY_PATTERN.sub(REDACTED, redacted)
    if len(redacted) <= MAX_LOG_STRING_CHARACTERS:
        return redacted
    keep = MAX_LOG_STRING_CHARACTERS - len(_STRING_TRUNCATION_MARKER)
    return f"{redacted[:keep]}{_STRING_TRUNCATION_MARKER}"


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
    "MAX_LOG_STRING_CHARACTERS",
    "LoadedSession",
    "Session",
    "SessionLogger",
    "create_session",
    "create_session_logger",
    "format_session_summary",
    "load_session",
    "list_session_files",
    "resolve_session_file",
    "summarize_session",
]
