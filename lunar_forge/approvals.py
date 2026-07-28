"""UI-independent approval request and decision providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from lunar_forge.events import sanitize_event_payload


APPROVAL_KINDS = frozenset(
    {
        "command",
        "write",
        "git_commit",
        "browser_server",
        "mcp_tool",
        "plugin_tool",
    }
)
APPROVAL_RISKS = frozenset({"low", "medium", "high"})
APPROVAL_MODES = frozenset(
    {"local", "docker", "plan", "no-command", "default"}
)
APPROVAL_SOURCES = frozenset({"cli", "auto", "deny", "textual"})


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """A presentation-neutral request for one guarded operation."""

    id: str
    kind: str
    title: str
    summary: str
    details: str
    risk: str
    mode: str
    default: bool = False
    command: str | None = None
    tool_name: str | None = None
    file_path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Approval request id must be a non-empty string.")
        if self.kind not in APPROVAL_KINDS:
            raise ValueError(f"Unsupported approval kind: {self.kind!r}.")
        if self.risk not in APPROVAL_RISKS:
            raise ValueError(f"Unsupported approval risk: {self.risk!r}.")
        if self.mode not in APPROVAL_MODES:
            raise ValueError(f"Unsupported approval mode: {self.mode!r}.")
        for name in ("title", "summary", "details"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Approval request {name} must be a non-empty string."
                )
        if not isinstance(self.default, bool):
            raise TypeError("Approval request default must be a boolean.")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Approval request metadata must be a mapping.")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        title: str,
        summary: str,
        details: str,
        risk: str,
        mode: str,
        default: bool = False,
        command: str | None = None,
        tool_name: str | None = None,
        file_path: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Create a request with a collision-resistant public identifier."""
        return cls(
            id=f"approval_{uuid4().hex}",
            kind=kind,
            title=title,
            summary=summary,
            details=details,
            risk=risk,
            mode=mode,
            default=default,
            command=command,
            tool_name=tool_name,
            file_path=file_path,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, bounded, and redacted transport payload."""
        return sanitize_event_payload(
            {
                "id": self.id,
                "request_id": self.id,
                "kind": self.kind,
                "title": self.title,
                "summary": self.summary,
                "details": self.details,
                "risk": self.risk,
                "mode": self.mode,
                "default": self.default,
                "command": self.command,
                "tool_name": self.tool_name,
                "file_path": self.file_path,
                "metadata": dict(self.metadata),
            }
        )


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """The provider-independent result for one approval request."""

    request_id: str
    approved: bool
    reason: str
    decided_at: str
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("Approval decision request_id must be non-empty.")
        if not isinstance(self.approved, bool):
            raise TypeError("Approval decision approved must be a boolean.")
        if not isinstance(self.reason, str):
            raise TypeError("Approval decision reason must be a string.")
        if not isinstance(self.decided_at, str) or not self.decided_at.strip():
            raise ValueError("Approval decision decided_at must be non-empty.")
        if self.source not in APPROVAL_SOURCES:
            raise ValueError(f"Unsupported approval source: {self.source!r}.")

    @classmethod
    def create(
        cls,
        request_id: str,
        *,
        approved: bool,
        reason: str,
        source: str,
    ) -> ApprovalDecision:
        """Create a timestamped decision."""
        return cls(
            request_id=request_id,
            approved=approved,
            reason=reason,
            decided_at=_timestamp(),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a bounded and redacted plain event/session payload."""
        return sanitize_event_payload(
            {
                "request_id": self.request_id,
                "approved": self.approved,
                "allowed": self.approved,
                "reason": self.reason,
                "decided_at": self.decided_at,
                "source": self.source,
            }
        )


@runtime_checkable
class ApprovalProvider(Protocol):
    """Synchronous boundary implemented by CLI and future UI adapters."""

    def request_approval(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        """Resolve one approval request."""
        ...


InputFunction = Callable[[str], str]


@dataclass(slots=True)
class CliApprovalProvider:
    """Render the established CLI prompt and read one terminal answer."""

    input_func: InputFunction = field(
        default_factory=lambda: input,
        repr=False,
    )

    def request_approval(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        description = request.details.rstrip()
        separator = (
            "\n\n"
            if "\n\n" in description
            else ("\n" if "\n" in description else " ")
        )
        try:
            answer = self.input_func(
                f"{description}{separator}Allow? [y/N] "
            ).strip().lower()
        except (EOFError, OSError, KeyboardInterrupt):
            return ApprovalDecision.create(
                request.id,
                approved=request.default,
                reason="Approval was unavailable or cancelled.",
                source="cli",
            )
        if answer in {"y", "yes"}:
            return ApprovalDecision.create(
                request.id,
                approved=True,
                reason="Approved by user.",
                source="cli",
            )
        return ApprovalDecision.create(
            request.id,
            approved=request.default,
            reason="Denied by user.",
            source="cli",
        )


@dataclass(slots=True)
class AutoApprovalProvider:
    """Approve only low-risk writes; delegate every other operation."""

    fallback: ApprovalProvider | None = None

    def request_approval(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        dependency_install = request.metadata.get("dependency_install") is True
        if (
            request.kind == "write"
            and request.risk == "low"
            and not dependency_install
        ):
            return ApprovalDecision.create(
                request.id,
                approved=True,
                reason="Auto-approved by yes mode.",
                source="auto",
            )
        if self.fallback is not None:
            return self.fallback.request_approval(request)
        return ApprovalDecision.create(
            request.id,
            approved=False,
            reason="This operation is not eligible for automatic approval.",
            source="auto",
        )


@dataclass(slots=True)
class DenyApprovalProvider:
    """Deny every request without interacting with a UI."""

    reason: str = "Denied by approval policy."

    def request_approval(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        return ApprovalDecision.create(
            request.id,
            approved=False,
            reason=self.reason,
            source="deny",
        )


TextualResolver = Callable[[ApprovalRequest], ApprovalDecision]


@dataclass(slots=True)
class TextualApprovalProvider:
    """Thin future-facing adapter; it has no Textual dependency or UI code."""

    resolver: TextualResolver

    def request_approval(
        self,
        request: ApprovalRequest,
    ) -> ApprovalDecision:
        decision = self.resolver(request)
        if decision.request_id != request.id:
            raise ValueError("Textual approval decision request_id did not match.")
        if decision.source != "textual":
            raise ValueError("Textual approval decisions must use source='textual'.")
        return decision


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


__all__ = [
    "APPROVAL_KINDS",
    "APPROVAL_MODES",
    "APPROVAL_RISKS",
    "APPROVAL_SOURCES",
    "ApprovalDecision",
    "ApprovalProvider",
    "ApprovalRequest",
    "AutoApprovalProvider",
    "CliApprovalProvider",
    "DenyApprovalProvider",
    "TextualApprovalProvider",
]
