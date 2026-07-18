"""Application-owned Docker wrapper for project-scoped command execution."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lunar_forge.permissions import (
    dangerous_command_reason,
    normalized_dangerous_command_reason,
)
from lunar_forge.runtime.local_runner import (
    DEFAULT_TIMEOUT_MS,
    MAX_STDERR_CHARACTERS,
    MAX_STDOUT_CHARACTERS,
)


DOCKER_IMAGE = "lunar-forge-sandbox"
DOCKER_WORKDIR = "/workspace"
DOCKER_MEMORY_LIMIT = "2g"
DOCKER_CPU_LIMIT = "2"
DOCKER_INFO_TIMEOUT_SECONDS = 30
_TRUNCATION_MARKER = "\n...[output truncated]"


def build_docker_args(
    project_root: str | Path,
    command: str,
    *,
    allow_network: bool = False,
) -> list[str]:
    """Build the fixed Docker argv; model input controls only the inner command."""
    root = _validated_project_root(project_root)
    if not isinstance(allow_network, bool):
        raise ValueError("allow_network must be a boolean.")
    network = "bridge" if allow_network else "none"
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "--memory",
        DOCKER_MEMORY_LIMIT,
        "--cpus",
        DOCKER_CPU_LIMIT,
        "-v",
        f"{root}:{DOCKER_WORKDIR}",
        "-w",
        DOCKER_WORKDIR,
        DOCKER_IMAGE,
        "bash",
        "-lc",
        command,
    ]


def run_docker_command(
    project_root: str | Path,
    command: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    *,
    allow_network: bool = False,
) -> dict[str, Any]:
    """Check Docker and run a bounded command in the fixed sandbox image."""
    started = time.perf_counter()
    if not isinstance(command, str) or not command.strip():
        return _error_result("", "Command must be a non-empty string.", started)

    # This check intentionally happens before docker info, argument construction,
    # or any subprocess call.
    dangerous_pattern = dangerous_command_reason(command)
    if dangerous_pattern is None:
        dangerous_pattern = normalized_dangerous_command_reason(command)
    if dangerous_pattern is not None:
        return _error_result(
            command,
            (
                "Command blocked by safety policy: matched prohibited "
                f"pattern {dangerous_pattern!r}."
            ),
            started,
        )
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        return _error_result(command, "timeout_ms must be an integer.", started)
    if timeout_ms <= 0:
        return _error_result(command, "timeout_ms must be greater than zero.", started)

    try:
        root = _validated_project_root(project_root)
        docker_args = build_docker_args(
            root,
            command,
            allow_network=allow_network,
        )
    except (OSError, PermissionError, ValueError) as exc:
        return _error_result(command, str(exc), started)

    availability = _check_docker(root, timeout_ms, started, command)
    if availability is not None:
        return availability

    try:
        completed = subprocess.run(
            docker_args,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_ms / 1000,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _timeout_result(command, timeout_ms, exc, started)
    except OSError as exc:
        return _error_result(
            command,
            f"Docker command could not start: {exc}",
            started,
        )

    stdout, stdout_truncated = _truncate(completed.stdout, MAX_STDOUT_CHARACTERS)
    stderr, stderr_truncated = _truncate(completed.stderr, MAX_STDERR_CHARACTERS)
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "runtime": "docker",
        "network": "bridge" if allow_network else "none",
        "command": command,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": _elapsed_ms(started),
        "truncated": stdout_truncated or stderr_truncated,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "timed_out": False,
    }
    if completed.returncode != 0:
        result["error"] = f"Docker command exited with code {completed.returncode}."
    return result


def _check_docker(
    root: Path,
    timeout_ms: int,
    started: float,
    command: str,
) -> dict[str, Any] | None:
    timeout_seconds = min(
        timeout_ms / 1000,
        DOCKER_INFO_TIMEOUT_SECONDS,
    )
    try:
        completed = subprocess.run(
            ["docker", "info"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return _error_result(
            command,
            "Docker is unavailable: 'docker info' timed out.",
            started,
        )
    except OSError as exc:
        return _error_result(
            command,
            f"Docker is unavailable: could not run 'docker info': {exc}",
            started,
        )

    if completed.returncode == 0:
        return None
    detail = (completed.stderr or completed.stdout).strip()
    detail, _ = _truncate(detail, 2_000)
    suffix = f" {detail}" if detail else ""
    return _error_result(
        command,
        f"Docker is unavailable: 'docker info' failed.{suffix}",
        started,
    )


def _validated_project_root(project_root: str | Path) -> Path:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {root}")
    if root == Path(root.anchor).resolve():
        raise PermissionError("Refusing to mount a filesystem root into Docker.")
    if root == Path.home().expanduser().resolve():
        raise PermissionError("Refusing to mount the host home directory into Docker.")

    docker_socket = Path("/var/run/docker.sock").resolve()
    try:
        docker_socket.relative_to(root)
    except ValueError:
        pass
    else:
        raise PermissionError("Refusing a mount that could expose the Docker socket.")
    return root


def _timeout_result(
    command: str,
    timeout_ms: int,
    error: subprocess.TimeoutExpired,
    started: float,
) -> dict[str, Any]:
    stdout, stdout_truncated = _truncate(
        _output_text(error.stdout),
        MAX_STDOUT_CHARACTERS,
    )
    stderr, stderr_truncated = _truncate(
        _output_text(error.stderr),
        MAX_STDERR_CHARACTERS,
    )
    return {
        "ok": False,
        "runtime": "docker",
        "command": command,
        "exit_code": None,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": _elapsed_ms(started),
        "truncated": stdout_truncated or stderr_truncated,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "timed_out": True,
        "error": f"Docker command timed out after {timeout_ms} ms.",
    }


def _error_result(command: str, error: str, started: float) -> dict[str, Any]:
    return {
        "ok": False,
        "runtime": "docker",
        "command": command,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "duration_ms": _elapsed_ms(started),
        "truncated": False,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "timed_out": False,
        "error": error,
    }


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    keep = max(0, limit - len(_TRUNCATION_MARKER))
    return f"{value[:keep]}{_TRUNCATION_MARKER}", True


def _output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


@dataclass(frozen=True)
class DockerRunner:
    project_root: Path
    allow_network: bool = False

    def run_command(
        self,
        command: str,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> dict[str, Any]:
        return run_docker_command(
            self.project_root,
            command,
            timeout_ms,
            allow_network=self.allow_network,
        )

    def run(
        self,
        command: str,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Compatibility alias matching the local runner's method name."""
        return self.run_command(command, timeout_ms)


__all__ = [
    "DOCKER_CPU_LIMIT",
    "DOCKER_IMAGE",
    "DOCKER_MEMORY_LIMIT",
    "DOCKER_WORKDIR",
    "DockerRunner",
    "build_docker_args",
    "run_docker_command",
]
