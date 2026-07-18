"""Planning primitives."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlanStep:
    description: str
    done: bool = False


@dataclass(frozen=True)
class Plan:
    steps: tuple[PlanStep, ...] = field(default_factory=tuple)

    @classmethod
    def from_request(cls, request: str) -> "Plan":
        description = request.strip() or "No request provided."
        return cls(steps=(PlanStep(description=description),))
