"""Model-facing adapters for guarded read-only Git helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lunar_forge.runtime.git import (
    DEFAULT_GIT_TIMEOUT_MS,
    git_diff as runtime_git_diff,
    git_status as runtime_git_status,
    list_changed_files as runtime_list_changed_files,
)


def git_status(
    project_root: str | Path,
    *,
    mode: str = "default",
    timeout_ms: int = DEFAULT_GIT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Return compact status through the existing guarded Git runtime."""
    return runtime_git_status(
        project_root,
        mode=mode,
        timeout_ms=timeout_ms,
    )


def git_diff(
    project_root: str | Path,
    path: str | None = None,
    staged: bool = False,
    max_lines: int | None = None,
    *,
    mode: str = "default",
    timeout_ms: int = DEFAULT_GIT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Return a bounded safe diff through the existing Git runtime."""
    return runtime_git_diff(
        project_root,
        path=path,
        staged=staged,
        max_lines=max_lines,
        mode=mode,
        timeout_ms=timeout_ms,
    )


def list_changed_files(
    project_root: str | Path,
    source: str = "both",
    *,
    session_files: Sequence[str] = (),
    mode: str = "default",
    timeout_ms: int = DEFAULT_GIT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Combine registry-tracked session paths with guarded Git state."""
    return runtime_list_changed_files(
        project_root,
        source=source,
        session_files=session_files,
        mode=mode,
        timeout_ms=timeout_ms,
    )


__all__ = ["git_diff", "git_status", "list_changed_files"]
