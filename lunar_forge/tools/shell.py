"""Model-callable local shell tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from lunar_forge.runtime.local_runner import (
    DEFAULT_TIMEOUT_MS,
    CommandResult,
    run as _run_arguments,
    run_local_command,
)


def run_command(
    project_root: str | Path,
    command: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Run a bounded command in the project without invoking a shell."""
    return run_local_command(project_root, command, timeout_ms)


def run(command: Sequence[str], cwd: str | Path) -> CommandResult:
    """Compatibility wrapper for the original argv-based helper."""
    return _run_arguments(command, cwd)


__all__ = ["CommandResult", "run", "run_command"]
