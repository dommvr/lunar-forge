"""Base protocol for model clients."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolCall:
    """A provider-neutral request to invoke a named tool."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelUsage:
    """Provider-neutral token usage for one model call."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    model: str | None = None
    provider: str | None = None
    phase: str | None = None
    role: str | None = None
    exact: bool = True


@dataclass(frozen=True)
class ModelResponse:
    """Normalized model output returned to the agent loop."""

    text: str
    model: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage | None = None

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


@runtime_checkable
class ModelClientFactory(Protocol):
    """Create one in-memory model client for an agent run or parallel phase."""

    def __call__(self) -> ModelClient:
        """Return a new provider-neutral model client."""
        ...


@runtime_checkable
class RedactingModelClient(ModelClient, Protocol):
    """Optional client capability for registering in-memory secrets."""

    def sensitive_values_for_redaction(self) -> Sequence[str]:
        """Return transient values the core must redact from public records."""
        ...
