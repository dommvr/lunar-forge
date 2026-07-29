"""Versioned, bounded, and redacted public agent events."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = 1
REDACTED = "[REDACTED]"
MAX_EVENT_STRING_CHARACTERS = 50_000
MAX_EVENT_PAYLOAD_CHARACTERS = 100_000
MAX_EVENT_COLLECTION_ITEMS = 200
MAX_EVENT_NESTING = 20
MAX_EVENT_IDENTIFIER_CHARACTERS = 200

_STRING_TRUNCATION_MARKER = "\n...[event value truncated]"
_COLLECTION_TRUNCATION_MARKER = "[event collection truncated]"
_UNSUPPORTED_VALUE_MARKER = "[unsupported event value: {type_name}]"
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|authorization|private[_-]?key)\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)([^\s,;]+)")
_API_KEY_PATTERN = re.compile(
    r"(?i)\b(?:sk-(?:ant-)?|gh[pousr]_|github_pat_)[a-z0-9_-]{8,}\b"
)
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
        "chainofthought",
        "hiddenreasoning",
        "privatereasoning",
        "reasoning",
        "reasoningcontent",
        "reasoningtext",
    }
)
_SENSITIVE_SUFFIXES = (
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
)
_SECRET_ENVIRONMENT_MARKERS = (
    "APIKEY",
    "ACCESSTOKEN",
    "REFRESHTOKEN",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTHORIZATION",
    "COOKIE",
    "PRIVATEKEY",
)


class EventType(str, Enum):
    """Stable public event names for the first event-stream phase."""

    SESSION_STARTED = "session.started"
    SESSION_RESUMED = "session.resumed"
    TURN_STARTED = "turn.started"
    TURN_FINISHED = "turn.finished"
    TURN_CANCELLED = "turn.cancelled"
    STATUS_UPDATED = "status.updated"
    ASSISTANT_MESSAGE_DELTA = "assistant.message.delta"
    ASSISTANT_MESSAGE_COMPLETED = "assistant.message.completed"
    MODEL_CALL_STARTED = "model.call.started"
    MODEL_CALL_FINISHED = "model.call.finished"
    MODEL_USAGE = "model.usage"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    TOOL_FAILED = "tool.failed"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_RESOLVED = "permission.resolved"
    VALIDATION_STARTED = "validation.started"
    VALIDATION_FINISHED = "validation.finished"
    BROWSER_STARTED = "browser.started"
    BROWSER_FINISHED = "browser.finished"
    CHECKPOINT_CREATED = "checkpoint.created"
    GIT_PROPOSAL = "git.proposal"
    GIT_COMMIT_CREATED = "git.commit.created"
    GIT_COMMIT_SKIPPED = "git.commit.skipped"
    MEMORY_COMPACTION_STARTED = "memory.compaction.started"
    MEMORY_COMPACTION_FINISHED = "memory.compaction.finished"
    ROLLBACK_STARTED = "rollback.started"
    ROLLBACK_FINISHED = "rollback.finished"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One JSON-serializable event in the public LunarForge protocol."""

    schema_version: int
    event_id: str
    session_id: str
    turn_id: str
    timestamp: str
    type: str | EventType
    payload: Mapping[str, Any]
    sequence: int = 0
    parent_event_id: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValueError(
                f"Unsupported event schema_version: {self.schema_version!r}."
            )
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("Event sequence must be an integer.")
        if self.sequence < 0:
            raise ValueError("Event sequence must be non-negative.")
        if not isinstance(self.payload, Mapping):
            raise TypeError("Event payload must be a mapping.")

        event_type = (
            self.type.value if isinstance(self.type, EventType) else str(self.type)
        )
        if not event_type.strip():
            raise ValueError("Event type must be a non-empty string.")

        object.__setattr__(self, "type", _safe_envelope_text(event_type, "type"))
        object.__setattr__(
            self,
            "event_id",
            _safe_envelope_text(self.event_id, "event_id"),
        )
        object.__setattr__(
            self,
            "session_id",
            _safe_envelope_text(self.session_id, "session_id"),
        )
        object.__setattr__(
            self,
            "turn_id",
            _safe_envelope_text(self.turn_id, "turn_id"),
        )
        object.__setattr__(
            self,
            "timestamp",
            _safe_envelope_text(self.timestamp, "timestamp"),
        )
        if self.parent_event_id is not None:
            object.__setattr__(
                self,
                "parent_event_id",
                _safe_envelope_text(
                    self.parent_event_id,
                    "parent_event_id",
                ),
            )
        object.__setattr__(
            self,
            "payload",
            sanitize_event_payload(self.payload),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached plain-dictionary representation."""
        record: dict[str, Any] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "type": self.type,
            "payload": dict(self.payload),
        }
        if self.parent_event_id is not None:
            record["parent_event_id"] = self.parent_event_id
        return json.loads(
            json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )

    def to_json(self) -> str:
        """Serialize the event to compact JSON."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> AgentEvent:
        """Deserialize and re-sanitize a mapping."""
        if not isinstance(record, Mapping):
            raise TypeError("Event record must be a mapping.")
        required = (
            "schema_version",
            "event_id",
            "session_id",
            "turn_id",
            "timestamp",
            "type",
            "payload",
        )
        missing = [name for name in required if name not in record]
        if missing:
            raise ValueError(
                f"Event record is missing required fields: {', '.join(missing)}."
            )
        return cls(
            schema_version=record["schema_version"],
            event_id=record["event_id"],
            session_id=record["session_id"],
            turn_id=record["turn_id"],
            sequence=record.get("sequence", 0),
            timestamp=record["timestamp"],
            type=record["type"],
            payload=record["payload"],
            parent_event_id=record.get("parent_event_id"),
        )

    @classmethod
    def from_json(cls, serialized: str) -> AgentEvent:
        """Deserialize and re-sanitize a JSON object."""
        try:
            record = json.loads(serialized)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Event JSON is invalid.") from exc
        return cls.from_dict(record)


@dataclass
class EventFactory:
    """Create ordered events for one session and turn."""

    session_id: str = field(default_factory=lambda: f"session_{uuid4().hex}")
    turn_id: str = field(default_factory=lambda: f"turn_{uuid4().hex}")
    environment: Mapping[str, str] | None = field(default=None, repr=False)
    timestamp_factory: Callable[[], str] = field(
        default=lambda: _timestamp(),
        repr=False,
    )
    _sequence: int = field(default=0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def begin_turn(self, turn_id: str | None = None) -> str:
        """Select a fresh turn while preserving session sequence ordering."""
        selected_turn_id = turn_id or f"turn_{uuid4().hex}"
        safe_turn_id = _safe_envelope_text(selected_turn_id, "turn_id")
        with self._lock:
            self.turn_id = safe_turn_id
        return safe_turn_id

    def create(
        self,
        event_type: EventType | str,
        payload: Mapping[str, Any] | None = None,
        *,
        parent_event_id: str | None = None,
        turn_id: str | None = None,
    ) -> AgentEvent:
        """Create the next event with a monotonic per-session sequence."""
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        safe_payload = sanitize_event_payload(
            payload or {},
            environment=self.environment,
        )
        return AgentEvent(
            schema_version=SCHEMA_VERSION,
            event_id=f"evt_{uuid4().hex}",
            session_id=self.session_id,
            turn_id=turn_id or self.turn_id,
            sequence=sequence,
            timestamp=self.timestamp_factory(),
            type=event_type,
            payload=safe_payload,
            parent_event_id=parent_event_id,
        )


def events_from_session_record(
    factory: EventFactory,
    legacy_event: str,
    data: Mapping[str, Any],
    *,
    parent_event_id: str | None = None,
) -> tuple[AgentEvent, ...]:
    """Adapt one already-sanitized legacy session record to public events."""
    if legacy_event == "permission.requested":
        payload = dict(data)
        payload.setdefault("request_id", data.get("id"))
        payload.setdefault("description", data.get("details"))
        return (
            factory.create(
                EventType.PERMISSION_REQUESTED,
                payload,
                parent_event_id=parent_event_id,
            ),
        )

    if legacy_event == "permission.resolved":
        payload = dict(data)
        payload.setdefault("allowed", data.get("approved"))
        return (
            factory.create(
                EventType.PERMISSION_RESOLVED,
                payload,
                parent_event_id=parent_event_id,
            ),
        )

    if legacy_event == "tool_schema_selection":
        return (
            factory.create(
                EventType.MODEL_CALL_STARTED,
                {
                    "step": data.get("step"),
                    "phase": data.get("phase"),
                    "role": data.get("role"),
                    "task_profile": data.get("task_profile"),
                    "tool_schema_count": data.get("exposed_tool_count"),
                    "tool_names": data.get("exposed_tool_names", []),
                },
            ),
        )

    if legacy_event == "model_usage":
        finished = factory.create(
            EventType.MODEL_CALL_FINISHED,
            {
                "step": data.get("step"),
                "phase": data.get("phase"),
                "role": data.get("role"),
                "model": data.get("model"),
                "exact_usage": data.get("exact") is True,
            },
        )
        usage = factory.create(
            EventType.MODEL_USAGE,
            dict(data),
            parent_event_id=finished.event_id,
        )
        return (finished, usage)

    if legacy_event == "tool_call":
        tool_name = _legacy_tool_name(data)
        started = factory.create(
            EventType.TOOL_STARTED,
            {
                "tool_name": tool_name,
                "provider_tool_name": data.get("model_tool_name"),
                "call_id": data.get("id"),
                "step": data.get("step"),
                "phase": data.get("phase"),
                "role": data.get("subagent") or data.get("role"),
                "args_preview": data.get("arguments", {}),
            },
        )
        events = [started]
        if _is_validation_tool(tool_name):
            events.append(
                factory.create(
                    EventType.VALIDATION_STARTED,
                    {
                        "tool_name": tool_name,
                        "call_id": data.get("id"),
                    },
                    parent_event_id=started.event_id,
                )
            )
        if _is_browser_tool(tool_name):
            events.append(
                factory.create(
                    EventType.BROWSER_STARTED,
                    {
                        "tool_name": tool_name,
                        "call_id": data.get("id"),
                    },
                    parent_event_id=started.event_id,
                )
            )
        return tuple(events)

    if legacy_event == "tool_result":
        tool_name = _legacy_tool_name(data)
        raw_result = data.get("result", {})
        result = (
            raw_result
            if isinstance(raw_result, Mapping)
            else {"value": raw_result}
        )
        succeeded = result.get("ok") is not False
        event_type = (
            EventType.TOOL_FINISHED if succeeded else EventType.TOOL_FAILED
        )
        finished = factory.create(
            event_type,
            {
                "tool_name": tool_name,
                "provider_tool_name": data.get("model_tool_name"),
                "call_id": data.get("id"),
                "step": data.get("step"),
                "phase": data.get("phase"),
                "role": data.get("subagent") or data.get("role"),
                "ok": result.get("ok"),
                "error": result.get("error"),
                "result": result,
            },
        )
        events = [finished]
        if _is_validation_tool(tool_name):
            events.append(
                factory.create(
                    EventType.VALIDATION_FINISHED,
                    {
                        "tool_name": tool_name,
                        "call_id": data.get("id"),
                        "ok": result.get("ok"),
                        "error": result.get("error"),
                    },
                    parent_event_id=finished.event_id,
                )
            )
        if _is_browser_tool(tool_name):
            events.append(
                factory.create(
                    EventType.BROWSER_FINISHED,
                    {
                        "tool_name": tool_name,
                        "call_id": data.get("id"),
                        "ok": result.get("ok"),
                        "error": result.get("error"),
                        "artifact_path": (
                            result.get("screenshot_path")
                            or result.get("artifact_path")
                        ),
                    },
                    parent_event_id=finished.event_id,
                )
            )
        checkpoint_path = result.get("checkpoint_path")
        if isinstance(checkpoint_path, str) and checkpoint_path:
            events.append(
                factory.create(
                    EventType.CHECKPOINT_CREATED,
                    {
                        "tool_name": tool_name,
                        "path": result.get("path"),
                        "checkpoint_path": checkpoint_path,
                    },
                    parent_event_id=finished.event_id,
                )
            )
        return tuple(events)

    if legacy_event == "git_commit_proposal":
        return (factory.create(EventType.GIT_PROPOSAL, dict(data)),)
    if legacy_event == "git_commit_created":
        return (factory.create(EventType.GIT_COMMIT_CREATED, dict(data)),)
    if legacy_event == "git_commit_skipped":
        return (factory.create(EventType.GIT_COMMIT_SKIPPED, dict(data)),)
    if legacy_event in {"error", "subagent_error"}:
        return (
            factory.create(
                EventType.ERROR,
                {
                    "source": data.get("source", legacy_event),
                    "error_type": data.get("error_type"),
                    "message": data.get("message") or data.get("error"),
                    "tool_name": _legacy_tool_name(data),
                },
            ),
        )
    return ()


def serialize_event(event: AgentEvent) -> str:
    """Serialize one event to compact JSON."""
    return event.to_json()


def deserialize_event(serialized: str) -> AgentEvent:
    """Deserialize one event from JSON."""
    return AgentEvent.from_json(serialized)


def sanitize_event_payload(
    payload: Mapping[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a bounded JSON-safe payload with secrets and reasoning removed."""
    if not isinstance(payload, Mapping):
        raise TypeError("Event payload must be a mapping.")
    selected_environment = os.environ if environment is None else environment
    environment_names, environment_values = _redaction_context(
        selected_environment
    )
    sanitized = _sanitize(
        payload,
        environment_names,
        environment_values,
    )
    if not isinstance(sanitized, dict):
        sanitized = {"value": sanitized}
    serialized = json.dumps(
        sanitized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if len(serialized) <= MAX_EVENT_PAYLOAD_CHARACTERS:
        return sanitized
    preview_limit = MAX_EVENT_PAYLOAD_CHARACTERS - 100
    return {
        "truncated": True,
        "preview": f"{serialized[:preview_limit]}...[event payload truncated]",
    }


def _sanitize(
    value: Any,
    environment_names: frozenset[str],
    environment_values: tuple[str, ...],
    depth: int = 0,
) -> Any:
    if depth >= MAX_EVENT_NESTING:
        return _COLLECTION_TRUNCATION_MARKER
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else str(value)
    if isinstance(value, str):
        return _redact_string(value, environment_values)
    if isinstance(value, Path):
        return _redact_string(str(value), environment_values)
    if isinstance(value, Enum):
        return _sanitize(
            value.value,
            environment_names,
            environment_values,
            depth + 1,
        )
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= MAX_EVENT_COLLECTION_ITEMS:
                sanitized["__lunar_forge_truncated__"] = True
                break
            key = _redact_string(str(raw_key), environment_values)
            if _is_sensitive_key(str(raw_key)) or str(raw_key) in environment_names:
                sanitized[key] = REDACTED
            else:
                sanitized[key] = _sanitize(
                    item,
                    environment_names,
                    environment_values,
                    depth + 1,
                )
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        sanitized_items: list[Any] = []
        for index, item in enumerate(value):
            if index >= MAX_EVENT_COLLECTION_ITEMS:
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
    return _UNSUPPORTED_VALUE_MARKER.format(type_name=type(value).__name__)


def _safe_envelope_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Event {field_name} must be a non-empty string.")
    safe = _redact_string(value, _redaction_context(os.environ)[1])
    if len(safe) > MAX_EVENT_IDENTIFIER_CHARACTERS:
        safe = safe[:MAX_EVENT_IDENTIFIER_CHARACTERS]
    return safe


def _legacy_tool_name(data: Mapping[str, Any]) -> str:
    for key in ("internal_tool_name", "name", "tool_name"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _is_validation_tool(tool_name: str) -> bool:
    return tool_name == "run_validation"


def _is_browser_tool(tool_name: str) -> bool:
    return tool_name in {
        "run_browser_validation",
        "run_managed_browser_validation",
    }


def _is_sensitive_key(key: str) -> bool:
    normalized = "".join(
        character for character in key.casefold() if character.isalnum()
    )
    if normalized in _SENSITIVE_KEYS:
        return True
    return normalized.endswith(_SENSITIVE_SUFFIXES)


def _redaction_context(
    environment: Mapping[str, str],
) -> tuple[frozenset[str], tuple[str, ...]]:
    secret_names = {
        name
        for name in environment
        if _is_secret_environment_name(name)
    }
    secret_values = {
        value
        for name, value in environment.items()
        if name in secret_names and value
    }
    return (
        frozenset(secret_names),
        tuple(sorted(secret_values, key=len, reverse=True)),
    )


def _is_secret_environment_name(name: str) -> bool:
    normalized = "".join(
        character for character in name.upper() if character.isalnum()
    )
    return any(marker in normalized for marker in _SECRET_ENVIRONMENT_MARKERS)


def _redact_string(value: str, environment_values: tuple[str, ...]) -> str:
    redacted = _ANSI_ESCAPE_PATTERN.sub("", value)
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
    if len(redacted) <= MAX_EVENT_STRING_CHARACTERS:
        return redacted
    keep = MAX_EVENT_STRING_CHARACTERS - len(_STRING_TRUNCATION_MARKER)
    return f"{redacted[:keep]}{_STRING_TRUNCATION_MARKER}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


__all__ = [
    "AgentEvent",
    "EventFactory",
    "EventType",
    "MAX_EVENT_COLLECTION_ITEMS",
    "MAX_EVENT_NESTING",
    "MAX_EVENT_PAYLOAD_CHARACTERS",
    "MAX_EVENT_STRING_CHARACTERS",
    "REDACTED",
    "SCHEMA_VERSION",
    "deserialize_event",
    "events_from_session_record",
    "sanitize_event_payload",
    "serialize_event",
]
