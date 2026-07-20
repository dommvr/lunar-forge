"""Base protocol for model clients."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    """A provider-neutral request to invoke a named tool."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    """Normalized model output returned to the agent loop."""

    text: str
    model: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def content(self) -> str:
        """Compatibility alias for callers that describe model text as content."""
        return self.text


class ModelClient(Protocol):
    """Provider-independent synchronous model interface."""

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> ModelResponse:
        """Return a normalized completion for messages and optional tools."""
