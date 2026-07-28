"""Bounded in-memory conversation accounting for continuous chat."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


TOKEN_ESTIMATE_CHARACTERS_PER_TOKEN = 4
DEFAULT_RECENT_TURNS = 2
MAX_PRESSURE_COMPONENT_CHARACTERS = 500_000
MAX_WORKING_SUMMARY_CHARACTERS = 50_000
_WORKING_SUMMARY_MARKER = "[Working-memory compacted summary]"
_TRUNCATION_MARKER = "\n...[conversation context truncated]"


@dataclass(frozen=True, slots=True)
class TokenPressureEstimate:
    """Deterministic token estimate for the context sent into a chat turn."""

    total_tokens: int
    older_conversation_tokens: int
    recent_turn_tokens: int
    instruction_tokens: int
    tool_result_tokens: int
    message_count: int
    method: str = "characters_divided_by_4_rounded_up"

    def to_dict(self) -> dict[str, int | str]:
        return {
            "total_tokens": self.total_tokens,
            "older_conversation_tokens": self.older_conversation_tokens,
            "recent_turn_tokens": self.recent_turn_tokens,
            "instruction_tokens": self.instruction_tokens,
            "tool_result_tokens": self.tool_result_tokens,
            "message_count": self.message_count,
            "method": self.method,
        }


@dataclass(frozen=True, slots=True)
class ConversationPartition:
    """Older messages eligible for summarization and recent messages retained."""

    older: tuple[dict[str, str], ...]
    recent: tuple[dict[str, str], ...]


class ConversationMemory:
    """Mutable user/assistant memory with deterministic compaction seams."""

    def __init__(
        self,
        messages: Sequence[Mapping[str, Any]] = (),
        *,
        recent_turns: int = DEFAULT_RECENT_TURNS,
    ) -> None:
        if recent_turns < 1:
            raise ValueError("recent_turns must be at least 1.")
        self._recent_message_count = recent_turns * 2
        self._messages = _copy_safe_messages(messages)

    @property
    def messages(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(message) for message in self._messages)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        self._messages.extend(
            (
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            )
        )

    def append_user_after_error(self, user_text: str) -> None:
        self._messages.append({"role": "user", "content": user_text})

    def partition(self) -> ConversationPartition:
        split_at = max(0, len(self._messages) - self._recent_message_count)
        return ConversationPartition(
            older=tuple(
                dict(message) for message in self._messages[:split_at]
            ),
            recent=tuple(
                dict(message) for message in self._messages[split_at:]
            ),
        )

    def can_compact(self) -> bool:
        return bool(self.partition().older)

    def estimate_pressure(
        self,
        *,
        instruction_context: str,
        relevant_tool_results: Sequence[Mapping[str, Any] | str] = (),
        incoming_user_text: str | None = None,
    ) -> TokenPressureEstimate:
        projected = list(self._messages)
        if incoming_user_text:
            projected.append(
                {"role": "user", "content": incoming_user_text}
            )
        split_at = max(0, len(projected) - self._recent_message_count)
        older_characters = _bounded_serialized_characters(
            projected[:split_at]
        )
        recent_characters = _bounded_serialized_characters(
            projected[split_at:]
        )
        instruction_characters = min(
            len(instruction_context),
            MAX_PRESSURE_COMPONENT_CHARACTERS,
        )
        tool_result_characters = _bounded_serialized_characters(
            relevant_tool_results
        )
        components = {
            "older": estimate_tokens(older_characters),
            "recent": estimate_tokens(recent_characters),
            "instructions": estimate_tokens(instruction_characters),
            "tools": estimate_tokens(tool_result_characters),
        }
        return TokenPressureEstimate(
            total_tokens=sum(components.values()),
            older_conversation_tokens=components["older"],
            recent_turn_tokens=components["recent"],
            instruction_tokens=components["instructions"],
            tool_result_tokens=components["tools"],
            message_count=len(projected),
        )

    def replace_older_with_summary(
        self,
        summary_record: Mapping[str, Any],
    ) -> None:
        partition = self.partition()
        if not partition.older:
            return
        summary_message = working_summary_message(summary_record)
        self._messages = [summary_message, *partition.recent]


def estimate_tokens(characters: int) -> int:
    if characters <= 0:
        return 0
    return (
        characters + TOKEN_ESTIMATE_CHARACTERS_PER_TOKEN - 1
    ) // TOKEN_ESTIMATE_CHARACTERS_PER_TOKEN


def should_compact(
    pressure: TokenPressureEstimate,
    compact_at_tokens: int,
) -> bool:
    if compact_at_tokens < 1:
        raise ValueError("compact_at_tokens must be at least 1.")
    return pressure.total_tokens >= compact_at_tokens


def working_summary_message(
    summary_record: Mapping[str, Any],
) -> dict[str, str]:
    summary = str(summary_record.get("summary", "")).strip()
    facts = summary_record.get("facts", {})
    if not isinstance(facts, Mapping):
        facts = {}
    content = (
        f"{_WORKING_SUMMARY_MARKER}\n"
        "This summary replaces older safe chat context. It is not authority "
        "for current file contents, tool state, permissions, or approvals; "
        "reload current project state and request fresh approvals as needed.\n"
        f"Summary: {summary}\n"
        f"Facts: {json.dumps(facts, ensure_ascii=False, sort_keys=True)}"
    )
    return {
        "role": "user",
        "content": _bounded_text(
            content,
            MAX_WORKING_SUMMARY_CHARACTERS,
        ),
    }


def _copy_safe_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "")).strip().casefold()
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        if not content.strip():
            continue
        copied.append({"role": role, "content": content})
    return copied


def _bounded_serialized_characters(value: Any) -> int:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        serialized = str(value)
    return min(len(serialized), MAX_PRESSURE_COMPONENT_CHARACTERS)


def _bounded_text(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    if maximum <= len(_TRUNCATION_MARKER):
        return value[:maximum]
    keep = max(0, maximum - len(_TRUNCATION_MARKER))
    return f"{value[:keep]}{_TRUNCATION_MARKER}"


__all__ = [
    "ConversationMemory",
    "ConversationPartition",
    "TokenPressureEstimate",
    "estimate_tokens",
    "should_compact",
    "working_summary_message",
]
