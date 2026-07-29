"""Stable, UI-neutral package API for future LunarForge wrappers.

Website, server, and cloud-runtime repositories should use this module (or the
matching exports from :mod:`lunar_forge`) instead of importing implementation
modules directly. The adapters here deliberately reuse the existing agent,
event, approval, configuration, and session systems.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lunar_forge.approvals import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
)
from lunar_forge.config import ALLOWED_REASONING_EFFORTS, load_config
from lunar_forge.events import AgentEvent, sanitize_event_payload
from lunar_forge.runtime.sessions import (
    LoadedSession,
    list_session_files,
    load_session,
)


MAX_PUBLIC_REQUEST_CHARACTERS = 50_000
MAX_PUBLIC_COMMIT_MESSAGE_CHARACTERS = 5_000
PUBLIC_RUNTIME_MODES = frozenset({"local", "docker", "no-command"})
PUBLIC_PERMISSION_MODES = frozenset(
    {"default", "yes", "no-command", "plan", "docker"}
)


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Safe project-relative reference returned by :func:`list_sessions`."""

    session_id: str
    selector: str
    path: str
    size_bytes: int

    def __post_init__(self) -> None:
        for name in ("session_id", "selector", "path"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"SessionRef {name} must not be empty.")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError(
                "SessionRef size_bytes must be a non-negative integer."
            )

    def to_dict(self) -> dict[str, Any]:
        """Return detached JSON-safe discovery metadata."""

        return sanitize_event_payload(
            {
                "session_id": self.session_id,
                "selector": self.selector,
                "path": self.path,
                "size_bytes": self.size_bytes,
            }
        )


@dataclass(frozen=True, slots=True)
class ResumedSession:
    """Bounded safe context loaded without replaying historical actions."""

    reference: SessionRef
    messages: tuple[dict[str, str], ...]
    historical_event_count: int
    compacted_summary_count: int
    tool_calls_replayed: bool = False
    approvals_reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a bounded JSON-safe transport representation."""

        return sanitize_event_payload(
            {
                "reference": self.reference.to_dict(),
                "messages": [dict(message) for message in self.messages],
                "historical_event_count": self.historical_event_count,
                "compacted_summary_count": self.compacted_summary_count,
                "tool_calls_replayed": self.tool_calls_replayed,
                "approvals_reused": self.approvals_reused,
            }
        )


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """One typed request for the public structured agent-event stream."""

    project_root: Path | str
    message: str
    runtime_mode: str | None = None
    permission_mode: str | None = None
    allow_network: bool | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    resume: SessionRef | str | None = None
    offer_commit: bool = False
    commit_message: str | None = None
    show_usage: bool = False
    ui_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        root = Path(self.project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {root}"
            )
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("AgentRequest message must not be empty.")
        if len(self.message) > MAX_PUBLIC_REQUEST_CHARACTERS:
            raise ValueError(
                "AgentRequest message must not exceed "
                f"{MAX_PUBLIC_REQUEST_CHARACTERS:,} characters."
            )
        if (
            self.runtime_mode is not None
            and self.runtime_mode not in PUBLIC_RUNTIME_MODES
        ):
            raise ValueError(
                f"Unsupported public runtime mode: {self.runtime_mode!r}."
            )
        if (
            self.permission_mode is not None
            and self.permission_mode not in PUBLIC_PERMISSION_MODES
        ):
            raise ValueError(
                "Unsupported public permission mode: "
                f"{self.permission_mode!r}."
            )
        if self.allow_network is not None and not isinstance(
            self.allow_network,
            bool,
        ):
            raise TypeError("AgentRequest allow_network must be a boolean.")
        if self.model is not None and (
            not isinstance(self.model, str) or not self.model.strip()
        ):
            raise ValueError("AgentRequest model must not be empty.")
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in ALLOWED_REASONING_EFFORTS
        ):
            raise ValueError(
                "AgentRequest reasoning_effort must be one of: "
                f"{', '.join(ALLOWED_REASONING_EFFORTS)}."
            )
        if not isinstance(self.offer_commit, bool):
            raise TypeError("AgentRequest offer_commit must be a boolean.")
        if not isinstance(self.show_usage, bool):
            raise TypeError("AgentRequest show_usage must be a boolean.")
        if self.commit_message is not None and not isinstance(
            self.commit_message,
            str,
        ):
            raise TypeError("AgentRequest commit_message must be a string.")
        if (
            self.commit_message is not None
            and len(self.commit_message) > MAX_PUBLIC_COMMIT_MESSAGE_CHARACTERS
        ):
            raise ValueError(
                "AgentRequest commit_message must not exceed "
                f"{MAX_PUBLIC_COMMIT_MESSAGE_CHARACTERS:,} characters."
            )
        if self.resume is not None and not isinstance(
            self.resume,
            (SessionRef, str),
        ):
            raise TypeError(
                "AgentRequest resume must be a SessionRef or session selector."
            )
        if isinstance(self.resume, str) and not self.resume.strip():
            raise ValueError("AgentRequest resume selector must not be empty.")
        if (
            self.allow_network is True
            and self.runtime_mode is not None
            and self.runtime_mode != "docker"
        ):
            raise ValueError(
                "AgentRequest allow_network requires Docker runtime mode."
            )
        if not isinstance(self.ui_metadata, Mapping):
            raise TypeError("AgentRequest ui_metadata must be a mapping.")

        object.__setattr__(self, "project_root", root)
        object.__setattr__(self, "message", self.message.strip())
        object.__setattr__(
            self,
            "ui_metadata",
            sanitize_event_payload(dict(self.ui_metadata)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return bounded request metadata without raw secrets or reasoning."""

        resume_selector = (
            self.resume.selector
            if isinstance(self.resume, SessionRef)
            else self.resume
        )
        return sanitize_event_payload(
            {
                "project_root": str(self.project_root),
                "message": self.message,
                "runtime_mode": self.runtime_mode,
                "permission_mode": self.permission_mode,
                "allow_network": self.allow_network,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "resume": resume_selector,
                "offer_commit": self.offer_commit,
                "commit_message": self.commit_message,
                "show_usage": self.show_usage,
                "ui_metadata": dict(self.ui_metadata),
            }
        )


def list_sessions(project_root: Path | str) -> tuple[SessionRef, ...]:
    """Return bounded project-local session references without reading logs."""

    root = Path(project_root).expanduser().resolve()
    result = list_session_files(root)
    if result.get("ok") is not True:
        raise ValueError(str(result.get("error", "Could not list sessions.")))
    references: list[SessionRef] = []
    for item in result.get("sessions", ()):
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        path = item.get("path")
        size = item.get("size")
        if (
            not isinstance(name, str)
            or not isinstance(path, str)
            or isinstance(size, bool)
            or not isinstance(size, int)
        ):
            continue
        references.append(
            SessionRef(
                session_id=f"session_{Path(name).stem}",
                selector=name,
                path=path,
                size_bytes=size,
            )
        )
    return tuple(references)


def resume_session(
    project_root: Path | str,
    session: SessionRef | str = "latest",
) -> ResumedSession:
    """Load safe resume context without replaying tools or approvals."""

    root = Path(project_root).expanduser().resolve()
    selector = session.selector if isinstance(session, SessionRef) else session
    loaded = load_session(
        root,
        selector,
        require_resumable=True,
    )
    return _resumed_session(loaded)


def run_agent_events(
    request: AgentRequest,
    approval_provider: ApprovalProvider | None = None,
) -> Iterator[AgentEvent]:
    """Yield the existing transport-neutral event stream for ``request``."""

    if not isinstance(request, AgentRequest):
        raise TypeError("run_agent_events expects an AgentRequest.")

    # Keep the package front door importable without Rich/Textual. The core
    # agent engine itself is loaded only when iteration actually begins.
    from lunar_forge.agent import run_agent_events as _run_agent_events

    overrides: dict[str, Any] = {}
    if request.runtime_mode is not None or request.allow_network is not None:
        overrides["runtime"] = {}
        if request.runtime_mode is not None:
            overrides["runtime"]["mode"] = request.runtime_mode
        if request.allow_network is not None:
            overrides["runtime"]["allow_network"] = request.allow_network
    if request.permission_mode is not None:
        overrides["permissions"] = {"mode": request.permission_mode}
    if request.model is not None or request.reasoning_effort is not None:
        overrides["model"] = {}
        if request.model is not None:
            overrides["model"]["model"] = request.model
        if request.reasoning_effort is not None:
            overrides["model"]["reasoning"] = {
                "effort": request.reasoning_effort
            }
    config = load_config(
        request.project_root,
        cli_overrides=overrides or None,
    )
    if request.allow_network is True and config.runtime.mode != "docker":
        raise ValueError(
            "AgentRequest allow_network requires Docker runtime mode."
        )
    loaded: ResumedSession | None = None
    if request.resume is not None:
        loaded = resume_session(request.project_root, request.resume)
    yield from _run_agent_events(
        request.message,
        request.project_root,
        config=config,
        mode=request.permission_mode or config.permissions.mode,
        approval_provider=approval_provider,
        resume_messages=loaded.messages if loaded is not None else (),
        resumed_from=(
            loaded.reference.path if loaded is not None else None
        ),
        offer_commit=request.offer_commit,
        commit_message=request.commit_message,
        show_usage=request.show_usage,
    )


def _resumed_session(loaded: LoadedSession) -> ResumedSession:
    reference = SessionRef(
        session_id=loaded.session_id,
        selector=loaded.path.name,
        path=loaded.safe_display_path,
        size_bytes=loaded.path.stat().st_size,
    )
    return ResumedSession(
        reference=reference,
        messages=tuple(dict(message) for message in loaded.messages),
        historical_event_count=len(loaded.events),
        compacted_summary_count=len(loaded.compacted_summaries),
    )


__all__ = [
    "AgentEvent",
    "AgentRequest",
    "ApprovalDecision",
    "ApprovalProvider",
    "ApprovalRequest",
    "ResumedSession",
    "SessionRef",
    "list_sessions",
    "load_config",
    "resume_session",
    "run_agent_events",
]
