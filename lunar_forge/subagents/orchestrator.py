"""Deterministic phase planning for future role-specific model calls."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from lunar_forge.subagents.base import SubagentRole
from lunar_forge.subagents.coder import CODER_ROLE
from lunar_forge.subagents.planner import PLANNER_ROLE
from lunar_forge.subagents.reviewer import REVIEWER_ROLE
from lunar_forge.subagents.scaffolder import SCAFFOLDER_ROLE
from lunar_forge.subagents.security import SECURITY_ROLE
from lunar_forge.subagents.tester import TESTER_ROLE
from lunar_forge.tools.registry import TaskProfile


_MUTATION_REQUEST_PATTERN = re.compile(
    r"(?i)\b(?:add|build|change|create|delete|edit|fix|implement|insert|"
    r"refactor|remove|replace|scaffold|update|write)\b"
)
_NO_MUTATION_REQUEST_PATTERN = re.compile(
    r"(?i)\b(?:do not|don't|dont|never)\s+"
    r"(?:change|edit|modify|write)\b|"
    r"\bwithout\s+(?:changing|editing|modifying|writing)\b|"
    r"\bread[- ]only\b"
)
_VALIDATION_REQUEST_PATTERN = re.compile(
    r"(?i)\b(?:run|execute|perform)\s+(?:the\s+)?"
    r"(?:build|checks?|lint|tests?|validation)\b|"
    r"\bvalidate\s+(?:it|the|this|project|repository|repo|changes?)\b"
)
_REVIEW_REQUEST_PATTERN = re.compile(
    r"(?i)\b(?:audit|review)\b|"
    r"\bcommit\s+readiness\b|"
    r"\bready\s+(?:for|to)\s+commit\b|"
    r"\buncommitted\s+changes?\b|"
    r"\b(?:diff|changes?)\s+(?:correctness|quality|readiness)\b"
)


class WorkflowKind(str, Enum):
    EXISTING_PROJECT = "existing_project"
    NEW_PROJECT = "new_project"


@dataclass(frozen=True)
class SubagentPhase:
    """One ordered phase; approval phases intentionally have no role."""

    name: str
    description: str
    role: SubagentRole | None = None
    requires_user_approval: bool = False
    parallel_group_id: str | None = None

    @property
    def role_name(self) -> str | None:
        return self.role.name if self.role is not None else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "role": self.role_name,
            "requires_user_approval": self.requires_user_approval,
            "parallel_group_id": self.parallel_group_id,
        }


@dataclass(frozen=True)
class SubagentPhasePlan:
    """A finite phase sequence; it contains no model or execution loop."""

    workflow: WorkflowKind
    phases: tuple[SubagentPhase, ...]

    def __post_init__(self) -> None:
        grouped: dict[str, list[SubagentPhase]] = {}
        for phase in self.phases:
            group_id = phase.parallel_group_id
            if group_id is None:
                continue
            if phase.role is None:
                raise ValueError("Approval phases cannot join parallel groups.")
            if not phase.role.can_run_in_parallel:
                raise ValueError(
                    f"Writer subagent {phase.role.name!r} cannot run in parallel."
                )
            grouped.setdefault(group_id, []).append(phase)
        for group_id, phases in grouped.items():
            if len(phases) < 2:
                raise ValueError(
                    f"Parallel group {group_id!r} must contain at least two roles."
                )

    @property
    def role_names(self) -> tuple[str, ...]:
        return tuple(
            phase.role_name
            for phase in self.phases
            if phase.role_name is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow.value,
            "phases": [phase.as_dict() for phase in self.phases],
        }

    @property
    def parallel_groups(self) -> tuple[tuple[str, tuple[SubagentPhase, ...]], ...]:
        """Return parallel groups in first-phase order."""
        groups: dict[str, list[SubagentPhase]] = {}
        for phase in self.phases:
            if phase.parallel_group_id is not None:
                groups.setdefault(phase.parallel_group_id, []).append(phase)
        return tuple(
            (group_id, tuple(phases)) for group_id, phases in groups.items()
        )


DEFAULT_ROLES = (
    PLANNER_ROLE,
    CODER_ROLE,
    REVIEWER_ROLE,
    TESTER_ROLE,
    SECURITY_ROLE,
    SCAFFOLDER_ROLE,
)


class SubagentOrchestrator:
    """Build a deterministic handoff plan without executing any phase."""

    def __init__(self, roles: Iterable[SubagentRole] = DEFAULT_ROLES) -> None:
        configured_roles = tuple(roles)
        role_map = {role.name: role for role in configured_roles}
        if len(role_map) != len(configured_roles):
            raise ValueError("Subagent role names must be unique.")
        self._roles: Mapping[str, SubagentRole] = MappingProxyType(role_map)

    @property
    def roles(self) -> Mapping[str, SubagentRole]:
        return self._roles

    def build_phase_plan(
        self,
        workflow: WorkflowKind | str,
        *,
        include_security: bool = False,
        parallel: bool = False,
    ) -> SubagentPhasePlan:
        resolved_workflow = _normalize_workflow(workflow)
        post_edit_group = "post-edit" if parallel else None
        if resolved_workflow is WorkflowKind.NEW_PROJECT:
            phases = [
                SubagentPhase(
                    name="scaffold",
                    role=self._get_role("scaffolder"),
                    description="Create the approved starter project.",
                ),
                SubagentPhase(
                    name="test",
                    role=self._get_role("tester"),
                    description="Run permission-gated, focused validation.",
                    parallel_group_id=post_edit_group,
                ),
                SubagentPhase(
                    name="review",
                    role=self._get_role("reviewer"),
                    description="Review the generated starter without mutating files.",
                    parallel_group_id=post_edit_group,
                ),
            ]
        else:
            analysis_group = "analysis" if parallel and include_security else None
            phases = [
                SubagentPhase(
                    name="plan",
                    role=self._get_role("planner"),
                    description=(
                        "Inspect context and produce a concrete implementation plan."
                    ),
                    parallel_group_id=analysis_group,
                ),
                SubagentPhase(
                    name="approval",
                    description=(
                        "Wait for permission approval before implementation work."
                    ),
                    requires_user_approval=True,
                ),
                SubagentPhase(
                    name="implement",
                    role=self._get_role("coder"),
                    description="Apply the approved existing-project changes.",
                ),
                SubagentPhase(
                    name="test",
                    role=self._get_role("tester"),
                    description="Run permission-gated, focused validation.",
                    parallel_group_id=post_edit_group,
                ),
                SubagentPhase(
                    name="review",
                    role=self._get_role("reviewer"),
                    description="Review the resulting changes without mutating files.",
                    parallel_group_id=post_edit_group,
                ),
            ]
        if include_security:
            security_phase = SubagentPhase(
                name="security",
                role=self._get_role("security"),
                description="Review changes that affect sensitive trust boundaries.",
                parallel_group_id=(
                    "analysis"
                    if parallel and resolved_workflow is WorkflowKind.EXISTING_PROJECT
                    else None
                ),
            )
            if parallel and resolved_workflow is WorkflowKind.EXISTING_PROJECT:
                phases.insert(1, security_phase)
            else:
                phases.append(security_phase)
        return SubagentPhasePlan(resolved_workflow, tuple(phases))

    def build_task_phase_plan(
        self,
        workflow: WorkflowKind | str,
        *,
        request: str,
        task_profile: TaskProfile | str,
        mode: str = "default",
        browser_intent: bool = False,
        include_security: bool = False,
        parallel: bool = False,
    ) -> SubagentPhasePlan:
        """Build only the phases justified by the selected task profile."""
        resolved_workflow = _normalize_workflow(workflow)
        resolved_profile = _normalize_task_profile(task_profile)
        normalized_mode = str(mode).strip().lower()
        if resolved_workflow is WorkflowKind.NEW_PROJECT:
            return self.build_phase_plan(
                resolved_workflow,
                include_security=include_security,
                parallel=parallel,
            )
        if normalized_mode == "plan" or resolved_profile is TaskProfile.PLAN_ONLY:
            return SubagentPhasePlan(
                resolved_workflow,
                (self._planner_phase("plan"),),
            )

        mutation_requested = _has_mutation_request(request)
        validation_requested = (
            browser_intent
            or _VALIDATION_REQUEST_PATTERN.search(str(request)) is not None
        )
        review_requested = _REVIEW_REQUEST_PATTERN.search(str(request)) is not None

        if (
            resolved_profile
            in {
                TaskProfile.EDIT_TASK,
                TaskProfile.BROWSER_TASK,
                TaskProfile.COMMIT_TASK,
            }
            and mutation_requested
        ):
            return self.build_phase_plan(
                resolved_workflow,
                include_security=include_security,
                parallel=parallel,
            )

        phases: list[SubagentPhase]
        if resolved_profile is TaskProfile.BROWSER_TASK:
            parallel_group_id = (
                "post-edit" if parallel and review_requested else None
            )
            phases = [
                SubagentPhase(
                    name="test",
                    role=self._get_role("tester"),
                    description="Run the explicitly requested browser validation.",
                    parallel_group_id=parallel_group_id,
                )
            ]
            if review_requested:
                phases.append(self._reviewer_phase(parallel_group_id))
        elif validation_requested:
            parallel_group_id = (
                "post-edit" if parallel and review_requested else None
            )
            phases = [
                SubagentPhase(
                    name="test",
                    role=self._get_role("tester"),
                    description="Run the explicitly requested focused validation.",
                    parallel_group_id=parallel_group_id,
                )
            ]
            if review_requested:
                phases.append(self._reviewer_phase(parallel_group_id))
        elif review_requested or resolved_profile is TaskProfile.COMMIT_TASK:
            phases = [self._reviewer_phase()]
        else:
            phases = [self._planner_phase("inspect")]

        if include_security:
            phases.append(
                SubagentPhase(
                    name="security",
                    role=self._get_role("security"),
                    description="Review the named sensitive trust boundary.",
                )
            )
        return SubagentPhasePlan(resolved_workflow, tuple(phases))

    def _planner_phase(self, name: str) -> SubagentPhase:
        description = (
            "Inspect and directly answer the read-only request."
            if name == "inspect"
            else "Inspect context and produce a concrete implementation plan."
        )
        return SubagentPhase(
            name=name,
            role=self._get_role("planner"),
            description=description,
        )

    def _reviewer_phase(
        self,
        parallel_group_id: str | None = None,
    ) -> SubagentPhase:
        return SubagentPhase(
            name="review",
            role=self._get_role("reviewer"),
            description="Review the requested files or diff without mutation.",
            parallel_group_id=parallel_group_id,
        )

    def _get_role(self, name: str) -> SubagentRole:
        try:
            return self._roles[name]
        except KeyError as exc:
            raise ValueError(f"Required subagent role is not configured: {name}") from exc


def build_phase_plan(
    workflow: WorkflowKind | str,
    *,
    include_security: bool = False,
    parallel: bool = False,
) -> SubagentPhasePlan:
    """Build the default finite phase sequence for a workflow."""
    return SubagentOrchestrator().build_phase_plan(
        workflow,
        include_security=include_security,
        parallel=parallel,
    )


def build_task_phase_plan(
    workflow: WorkflowKind | str,
    *,
    request: str,
    task_profile: TaskProfile | str,
    mode: str = "default",
    browser_intent: bool = False,
    include_security: bool = False,
    parallel: bool = False,
) -> SubagentPhasePlan:
    """Build a task-aware phase plan with skipped roles omitted."""
    return SubagentOrchestrator().build_task_phase_plan(
        workflow,
        request=request,
        task_profile=task_profile,
        mode=mode,
        browser_intent=browser_intent,
        include_security=include_security,
        parallel=parallel,
    )


def task_profile_for_role(
    role: SubagentRole | str,
    base_profile: TaskProfile | str,
    *,
    browser_intent: bool = False,
) -> TaskProfile:
    """Compose a task profile with a role's narrower static allowlist."""
    role_name = role.name if isinstance(role, SubagentRole) else str(role)
    normalized_role = role_name.strip().lower()
    try:
        resolved_base = (
            base_profile
            if isinstance(base_profile, TaskProfile)
            else TaskProfile(str(base_profile).strip().lower().replace("-", "_"))
        )
    except ValueError as exc:
        raise ValueError(f"Unknown base task profile: {base_profile!r}") from exc

    if resolved_base in {
        TaskProfile.EXPLICIT_READONLY,
        TaskProfile.PLAN_ONLY,
        TaskProfile.REVIEW_ONLY,
    }:
        return resolved_base
    if normalized_role == "planner":
        return TaskProfile.PLAN_ONLY
    if normalized_role in {"reviewer", "security"}:
        return TaskProfile.REVIEW_ONLY
    if normalized_role == "coder":
        return TaskProfile.EDIT_TASK
    if normalized_role == "tester":
        return (
            TaskProfile.BROWSER_TASK
            if browser_intent
            else TaskProfile.EDIT_TASK
        )
    if normalized_role == "scaffolder":
        return TaskProfile.NEW_PROJECT
    raise ValueError(f"Unknown subagent role for task profiling: {role_name!r}")


def requires_security_analysis(request: str) -> bool:
    """Conservatively detect prompts that name a sensitive trust boundary."""
    normalized = str(request).casefold()
    sensitive_boundary = any(
        keyword in normalized
        for keyword in (
            "permission",
            "credential",
            "secret",
            "api key",
            "access token",
            "authentication",
            "authorization",
            "command runner",
            "shell execution",
            "docker",
            "mcp",
            "plugin",
        )
    )
    explicit_security_review = any(
        keyword in normalized
        for keyword in (
            "security",
            "vulnerability",
            "threat model",
        )
    )
    ci_security_review = (
        any(keyword in normalized for keyword in ("ci", "workflow", "pipeline"))
        and any(keyword in normalized for keyword in ("security", "audit", "review"))
    )
    return sensitive_boundary or explicit_security_review or ci_security_review


def requires_security_review(changed_paths: Iterable[str]) -> bool:
    """Return whether changed code touches a sensitive trust boundary."""
    for path in changed_paths:
        normalized = str(path).replace("\\", "/").strip("/").casefold()
        if not normalized:
            continue
        parts = tuple(part for part in normalized.split("/") if part)
        filename = parts[-1]
        if filename in {
            "permissions.py",
            "shell.py",
            "dockerfile",
            "config.py",
            "config.yaml",
            "config.yml",
        }:
            return True
        if any(
            part in {"permissions", "docker", "mcp", "plugin", "plugins"}
            for part in parts
        ):
            return True
        if "docker" in filename:
            return True
    return False


def _normalize_workflow(workflow: WorkflowKind | str) -> WorkflowKind:
    if isinstance(workflow, WorkflowKind):
        return workflow
    if not isinstance(workflow, str):
        raise ValueError("Workflow must be 'existing_project' or 'new_project'.")
    normalized = workflow.strip().lower().replace("-", "_")
    try:
        return WorkflowKind(normalized)
    except ValueError as exc:
        raise ValueError(
            "Workflow must be 'existing_project' or 'new_project'."
        ) from exc


def _normalize_task_profile(profile: TaskProfile | str) -> TaskProfile:
    if isinstance(profile, TaskProfile):
        return profile
    if not isinstance(profile, str):
        raise ValueError("Task profile must be a string or TaskProfile.")
    normalized = profile.strip().lower().replace("-", "_")
    try:
        return TaskProfile(normalized)
    except ValueError as exc:
        raise ValueError(f"Unknown task profile: {profile!r}") from exc


def _has_mutation_request(request: str) -> bool:
    text = str(request)
    return (
        _MUTATION_REQUEST_PATTERN.search(text) is not None
        and _NO_MUTATION_REQUEST_PATTERN.search(text) is None
    )


__all__ = [
    "DEFAULT_ROLES",
    "SubagentOrchestrator",
    "SubagentPhase",
    "SubagentPhasePlan",
    "WorkflowKind",
    "build_phase_plan",
    "build_task_phase_plan",
    "requires_security_analysis",
    "requires_security_review",
    "task_profile_for_role",
]
