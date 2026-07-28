"""Plain-text rendering for the current one-shot CLI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lunar_forge.events import AgentEvent, EventType
from lunar_forge.runtime.sessions import format_model_usage_totals


@dataclass
class ConsoleRenderer:
    """Render public events without making agent or permission decisions."""

    show_status: bool = True
    show_tool_details: bool = True
    show_usage: bool = True
    _output: str = field(default="", init=False, repr=False)
    _delta_text_by_turn: dict[str, str] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @classmethod
    def one_shot(cls) -> ConsoleRenderer:
        """Return the quiet renderer used by the existing one-shot CLI."""
        return cls(
            show_status=False,
            show_tool_details=False,
            show_usage=False,
        )

    def handle(self, event: AgentEvent) -> str | None:
        """Render one event and append it to this renderer's output."""
        rendered = self.render_event(event)
        if rendered is None or rendered == "":
            return rendered
        inline = event.type == EventType.ASSISTANT_MESSAGE_DELTA.value
        self._append(rendered, inline=inline)
        if inline:
            self._delta_text_by_turn[event.turn_id] = (
                self._delta_text_by_turn.get(event.turn_id, "") + rendered
            )
        return rendered

    def consume(self, events: Iterable[AgentEvent]) -> str:
        """Consume events and return one plain-text CLI response."""
        self._output = ""
        self._delta_text_by_turn.clear()
        for event in events:
            self.handle(event)
        return self._output.rstrip("\n")

    def render_event(self, event: AgentEvent) -> str | None:
        """Return the plain-text representation for one event."""
        payload = event.payload
        if event.type == EventType.STATUS_UPDATED.value:
            if not self.show_status:
                return None
            return _first_text(payload, "message", "status", "state")

        if event.type == EventType.ASSISTANT_MESSAGE_DELTA.value:
            return _first_text(payload, "delta", "text")

        if event.type == EventType.ASSISTANT_MESSAGE_COMPLETED.value:
            text = _first_text(payload, "text", "message") or ""
            streamed = self._delta_text_by_turn.get(event.turn_id, "")
            if streamed and text == streamed:
                return None
            if streamed and text.startswith(streamed):
                return text[len(streamed) :]
            return text

        if event.type == EventType.TURN_FINISHED.value:
            return _first_text(payload, "final_text")

        if event.type == EventType.TOOL_STARTED.value:
            if not self.show_tool_details:
                return None
            name = _tool_name(payload)
            return f"Tool: {name} (started)"

        if event.type == EventType.TOOL_FINISHED.value:
            if not self.show_tool_details:
                return None
            name = _tool_name(payload)
            return f"Tool: {name} (completed)"

        if event.type == EventType.TOOL_FAILED.value:
            if not self.show_tool_details:
                return None
            name = _tool_name(payload)
            message = _first_text(payload, "error", "message")
            suffix = f": {message}" if message else ""
            return f"Tool: {name} (failed){suffix}"

        if event.type == EventType.MODEL_USAGE.value:
            if not self.show_usage:
                return None
            return _format_usage(payload)

        if event.type == EventType.ERROR.value:
            message = _first_text(payload, "message", "error")
            return f"Error: {message or 'Unknown agent error.'}"

        return None

    def _append(self, text: str, *, inline: bool) -> None:
        if not self._output:
            self._output = text
            return
        if inline:
            self._output += text
            return
        if not self._output.endswith("\n"):
            self._output += "\n"
        self._output += text


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


def _format_usage(payload: Mapping[str, Any]) -> str:
    raw_totals = payload.get("totals")
    usage = raw_totals if isinstance(raw_totals, Mapping) else payload
    if "model_calls" not in usage:
        exact = usage.get("exact") is True
        usage = {
            **usage,
            "model_calls": 1,
            "exact_calls": 1 if exact else 0,
            "estimated_calls": 0 if exact else 1,
        }
    reasoning_effort = _first_text(
        payload,
        "reasoning_effort",
    ) or _first_text(usage, "reasoning_effort")
    return format_model_usage_totals(
        usage,
        reasoning_effort=reasoning_effort,
    )


__all__ = ["ConsoleRenderer"]
