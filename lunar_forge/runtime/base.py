"""Stable, implementation-neutral workspace runtime contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, runtime_checkable


MAX_RUNTIME_PATH_CHARACTERS = 1_000
MAX_RUNTIME_TEXT_CHARACTERS = 50_000
MAX_RUNTIME_COMMAND_CHARACTERS = 5_000
MAX_RUNTIME_COMMAND_TIMEOUT_MS = 900_000
MAX_RUNTIME_OUTPUT_CHARACTERS = 50_000
MAX_RUNTIME_ERROR_CHARACTERS = 5_000
MAX_RUNTIME_DIRECTORY_ENTRIES = 200
_OUTPUT_TRUNCATION_MARKER = "\n...[runtime output truncated]"


class RuntimeNetworkPolicy(str, Enum):
    """Network state reported by a runtime implementation."""

    DENIED = "denied"
    RESTRICTED = "restricted"
    ALLOWED = "allowed"
    HOST = "host"
    UNKNOWN = "unknown"


class RuntimePathType(str, Enum):
    """Portable file kinds returned by runtime metadata calls."""

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


class RuntimeRollbackStatus(str, Enum):
    """Stable current-turn rollback outcomes."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    NOT_REQUESTED = "not_requested"
    FAILED = "failed"


def normalize_workspace_path(
    path: str | Path,
    *,
    allow_root: bool = False,
) -> str:
    """Return one confined POSIX-style relative workspace path.

    The core validates paths before invoking an injected runtime. Runtime
    implementations must enforce the same confinement at their own boundary.
    """

    if not isinstance(path, (str, Path)):
        raise TypeError("Workspace path must be a string or Path.")
    raw = str(path).strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        raise ValueError("Workspace path must not be empty.")
    if len(raw) > MAX_RUNTIME_PATH_CHARACTERS:
        raise ValueError(
            "Workspace path exceeds the public runtime path limit."
        )
    if PureWindowsPath(raw).drive or raw.startswith("/"):
        raise PermissionError("Workspace paths must be project-relative.")
    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise PermissionError("Path is outside the runtime workspace.")
        parts.append(part)
    if not parts:
        if allow_root:
            return "."
        raise ValueError("Workspace path must identify a project entry.")
    return PurePosixPath(*parts).as_posix()


@dataclass(frozen=True, slots=True)
class RuntimeFileInfo:
    """Bounded metadata for one workspace entry."""

    path: str
    type: RuntimePathType | str
    size_bytes: int | None = None
    modified_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "path",
            normalize_workspace_path(self.path, allow_root=True),
        )
        path_type = (
            self.type
            if isinstance(self.type, RuntimePathType)
            else RuntimePathType(str(self.type))
        )
        object.__setattr__(self, "type", path_type)
        if self.size_bytes is not None and (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("Runtime file size must be a non-negative integer.")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": PurePosixPath(self.path).name,
            "type": self.type.value,
            "size": self.size_bytes,
            "modified_at": self.modified_at,
        }


@dataclass(frozen=True, slots=True)
class RuntimeTextResult:
    """A bounded UTF-8 text read."""

    path: str
    content: str
    truncated: bool = False
    start_line: int = 1
    end_line: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_workspace_path(self.path))
        if not isinstance(self.content, str):
            raise TypeError("Runtime text content must be a string.")
        if self.start_line < 1:
            raise ValueError("Runtime text start_line must be at least 1.")
        content, clipped = _bounded_text(
            self.content,
            MAX_RUNTIME_TEXT_CHARACTERS,
        )
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "truncated", self.truncated or clipped)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "path": self.path,
            "content": self.content,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class RuntimeOperationResult:
    """Portable result for create, delete, and move operations."""

    ok: bool
    path: str
    error: str | None = None
    destination: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_workspace_path(self.path))
        if self.destination is not None:
            object.__setattr__(
                self,
                "destination",
                normalize_workspace_path(self.destination),
            )
        if self.error is not None:
            object.__setattr__(
                self,
                "error",
                self.error[:MAX_RUNTIME_ERROR_CHARACTERS],
            )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"ok": self.ok, "path": self.path}
        if self.destination is not None:
            result["destination"] = self.destination
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True, slots=True)
class RuntimeWriteResult:
    """Portable result of creating or replacing one text file."""

    ok: bool
    path: str
    created: bool = False
    overwritten: bool = False
    diff: str = ""
    diff_truncated: bool = False
    checkpoint_id: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_workspace_path(self.path))
        diff, clipped = _bounded_text(self.diff, MAX_RUNTIME_TEXT_CHARACTERS)
        object.__setattr__(self, "diff", diff)
        object.__setattr__(
            self,
            "diff_truncated",
            self.diff_truncated or clipped,
        )
        if self.checkpoint_id is not None:
            object.__setattr__(
                self,
                "checkpoint_id",
                str(self.checkpoint_id)[:MAX_RUNTIME_PATH_CHARACTERS],
            )
        if self.error is not None:
            object.__setattr__(
                self,
                "error",
                self.error[:MAX_RUNTIME_ERROR_CHARACTERS],
            )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": self.ok,
            "path": self.path,
            "created": self.created,
            "overwritten": self.overwritten,
            "diff": self.diff,
            "diff_truncated": self.diff_truncated,
            "checkpoint_id": self.checkpoint_id,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True, slots=True)
class RuntimeCommandResult:
    """Bounded result of one runtime command."""

    ok: bool
    command: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    cancelled: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "command",
            str(self.command)[:MAX_RUNTIME_COMMAND_CHARACTERS],
        )
        stdout, stdout_clipped = _bounded_text(
            self.stdout,
            MAX_RUNTIME_OUTPUT_CHARACTERS,
        )
        stderr, stderr_clipped = _bounded_text(
            self.stderr,
            MAX_RUNTIME_OUTPUT_CHARACTERS,
        )
        object.__setattr__(self, "stdout", stdout)
        object.__setattr__(self, "stderr", stderr)
        object.__setattr__(
            self,
            "stdout_truncated",
            self.stdout_truncated or stdout_clipped,
        )
        object.__setattr__(
            self,
            "stderr_truncated",
            self.stderr_truncated or stderr_clipped,
        )
        if self.error is not None:
            object.__setattr__(
                self,
                "error",
                self.error[:MAX_RUNTIME_ERROR_CHARACTERS],
            )
        if self.duration_ms < 0:
            raise ValueError("Runtime command duration must be non-negative.")

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": self.ok,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True, slots=True)
class RuntimeCheckpoint:
    """Opaque current-turn checkpoint returned by a runtime."""

    supported: bool
    checkpoint_id: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.checkpoint_id is not None:
            object.__setattr__(
                self,
                "checkpoint_id",
                str(self.checkpoint_id)[:MAX_RUNTIME_PATH_CHARACTERS],
            )
        if self.error is not None:
            object.__setattr__(
                self,
                "error",
                str(self.error)[:MAX_RUNTIME_ERROR_CHARACTERS],
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "checkpoint_id": self.checkpoint_id,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RuntimeRollbackResult:
    """Bounded, conservative current-turn rollback report."""

    status: RuntimeRollbackStatus | str
    restored_files: tuple[str, ...] = ()
    removed_files: tuple[str, ...] = ()
    skipped_files: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        status = (
            self.status
            if isinstance(self.status, RuntimeRollbackStatus)
            else RuntimeRollbackStatus(str(self.status))
        )
        object.__setattr__(self, "status", status)
        for field_name in (
            "restored_files",
            "removed_files",
            "skipped_files",
        ):
            values = tuple(
                normalize_workspace_path(value)
                for value in getattr(self, field_name)[:200]
            )
            object.__setattr__(self, field_name, values)
        object.__setattr__(
            self,
            "errors",
            tuple(
                str(error)[:MAX_RUNTIME_ERROR_CHARACTERS]
                for error in self.errors[:200]
            ),
        )

    @property
    def complete(self) -> bool:
        return self.status is RuntimeRollbackStatus.COMPLETED

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "restored_files": list(self.restored_files),
            "removed_files": list(self.removed_files),
            "skipped_files": list(self.skipped_files),
            "errors": list(self.errors),
        }


@runtime_checkable
class WorkspaceRuntime(Protocol):
    """Stable boundary implemented by local, Docker, and remote workspaces.

    Paths are always project-relative. Implementations must independently
    enforce workspace confinement and every supplied bound.
    """

    @property
    def workspace_id(self) -> str:
        """Return an opaque, non-secret workspace identifier."""
        ...

    @property
    def local_project_root(self) -> Path | None:
        """Return a local root only when the workspace is locally addressable."""
        ...

    @property
    def network_policy(self) -> RuntimeNetworkPolicy:
        """Return the runtime's current effective network policy."""
        ...

    def list_directory(self, path: str = ".") -> tuple[RuntimeFileInfo, ...]:
        """List at most the public directory-entry limit."""
        ...

    def stat(self, path: str) -> RuntimeFileInfo | None:
        """Return metadata for a confined entry, or ``None`` when absent."""
        ...

    def read_text(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
        max_characters: int = MAX_RUNTIME_TEXT_CHARACTERS,
    ) -> RuntimeTextResult:
        """Read bounded UTF-8 text from a confined file."""
        ...

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = False,
    ) -> RuntimeWriteResult:
        """Create or explicitly replace one confined UTF-8 file."""
        ...

    def create_directory(self, path: str) -> RuntimeOperationResult:
        """Create one confined directory and necessary parents."""
        ...

    def delete_path(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> RuntimeOperationResult:
        """Delete one confined path, with recursion explicit."""
        ...

    def move_path(
        self,
        source: str,
        destination: str,
        *,
        overwrite: bool = False,
    ) -> RuntimeOperationResult:
        """Move one confined path without escaping the workspace."""
        ...

    def execute(
        self,
        command: str,
        *,
        timeout_ms: int,
        max_output_characters: int = MAX_RUNTIME_OUTPUT_CHARACTERS,
    ) -> RuntimeCommandResult:
        """Execute one bounded command with a mandatory timeout."""
        ...

    def cancel_active_command(self) -> bool:
        """Request best-effort cancellation of the active command."""
        ...

    def checkpoint_turn(self, turn_id: str) -> RuntimeCheckpoint:
        """Begin a current-turn checkpoint when supported."""
        ...

    def rollback_turn(self, checkpoint_id: str) -> RuntimeRollbackResult:
        """Conservatively roll back the supplied current-turn checkpoint."""
        ...


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    text = str(value)
    if len(text) <= limit:
        return text, False
    keep = max(0, limit - len(_OUTPUT_TRUNCATION_MARKER))
    return f"{text[:keep]}{_OUTPUT_TRUNCATION_MARKER}", True


__all__ = [
    "MAX_RUNTIME_DIRECTORY_ENTRIES",
    "MAX_RUNTIME_COMMAND_CHARACTERS",
    "MAX_RUNTIME_COMMAND_TIMEOUT_MS",
    "MAX_RUNTIME_OUTPUT_CHARACTERS",
    "MAX_RUNTIME_PATH_CHARACTERS",
    "MAX_RUNTIME_TEXT_CHARACTERS",
    "RuntimeCheckpoint",
    "RuntimeCommandResult",
    "RuntimeFileInfo",
    "RuntimeNetworkPolicy",
    "RuntimeOperationResult",
    "RuntimePathType",
    "RuntimeRollbackResult",
    "RuntimeRollbackStatus",
    "RuntimeTextResult",
    "RuntimeWriteResult",
    "WorkspaceRuntime",
    "normalize_workspace_path",
]
