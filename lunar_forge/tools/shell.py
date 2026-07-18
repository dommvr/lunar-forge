"""Model-callable local shell tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from lunar_forge.runtime.docker_runner import run_docker_command
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
    *,
    runtime_mode: str = "local",
    allow_network: bool = False,
) -> dict[str, Any]:
    """Dispatch a bounded command to the configured application-owned runner."""
    normalized_mode = runtime_mode.strip().lower()
    if normalized_mode == "local":
        return run_local_command(project_root, command, timeout_ms)
    if normalized_mode == "docker":
        return run_docker_command(
            project_root,
            command,
            timeout_ms,
            allow_network=allow_network,
        )
    if normalized_mode == "no-command":
        return {
            "ok": False,
            "runtime": "no-command",
            "command": command,
            "error": "Command execution is disabled by runtime mode.",
        }
    return {
        "ok": False,
        "runtime": normalized_mode,
        "command": command,
        "error": f"Unsupported runtime mode: {runtime_mode}",
    }


def run(command: Sequence[str], cwd: str | Path) -> CommandResult:
    """Compatibility wrapper for the original argv-based helper."""
    return _run_arguments(command, cwd)


__all__ = ["CommandResult", "run", "run_command"]
