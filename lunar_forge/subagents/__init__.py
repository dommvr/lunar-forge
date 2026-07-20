"""Role definitions and deterministic orchestration primitives."""

from types import MappingProxyType

from lunar_forge.subagents.base import (
    BUILTIN_SUBAGENT_TOOLS,
    RestrictedToolRegistry,
    SubagentRole,
)
from lunar_forge.subagents.coder import CODER_ROLE
from lunar_forge.subagents.orchestrator import (
    SubagentOrchestrator,
    SubagentPhase,
    SubagentPhasePlan,
    WorkflowKind,
    build_phase_plan,
    requires_security_review,
)
from lunar_forge.subagents.planner import PLANNER_ROLE
from lunar_forge.subagents.reviewer import REVIEWER_ROLE
from lunar_forge.subagents.scaffolder import SCAFFOLDER_ROLE
from lunar_forge.subagents.security import SECURITY_ROLE
from lunar_forge.subagents.tester import TESTER_ROLE


SUBAGENT_ROLES = MappingProxyType(
    {
        role.name: role
        for role in (
            PLANNER_ROLE,
            CODER_ROLE,
            REVIEWER_ROLE,
            TESTER_ROLE,
            SECURITY_ROLE,
            SCAFFOLDER_ROLE,
        )
    }
)


def get_subagent_role(name: str) -> SubagentRole:
    """Return a configured role by its normalized name."""
    normalized = name.strip().lower()
    try:
        return SUBAGENT_ROLES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown subagent role: {name}") from exc


__all__ = [
    "BUILTIN_SUBAGENT_TOOLS",
    "CODER_ROLE",
    "PLANNER_ROLE",
    "REVIEWER_ROLE",
    "RestrictedToolRegistry",
    "SCAFFOLDER_ROLE",
    "SECURITY_ROLE",
    "SUBAGENT_ROLES",
    "SubagentOrchestrator",
    "SubagentPhase",
    "SubagentPhasePlan",
    "SubagentRole",
    "TESTER_ROLE",
    "WorkflowKind",
    "build_phase_plan",
    "get_subagent_role",
    "requires_security_review",
]
