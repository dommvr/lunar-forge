"""Renderer contracts for public agent events."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from lunar_forge.events import AgentEvent


@runtime_checkable
class Renderer(Protocol):
    """Consume agent events without participating in agent decisions."""

    def handle(self, event: AgentEvent) -> str | None:
        """Handle one event and return any rendered text."""

    def consume(self, events: Iterable[AgentEvent]) -> str:
        """Consume an event iterable and return the rendered output."""


__all__ = ["Renderer"]
