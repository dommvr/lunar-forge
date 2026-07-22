"""Bounded local command execution without invoking a command shell."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Sequence

from lunar_forge.permissions import dangerous_command_reason


DEFAULT_TIMEOUT_MS = 120_000
MAX_STDOUT_CHARACTERS = 50_000
MAX_STDERR_CHARACTERS = 50_000
_TRUNCATION_MARKER = "\n...[output truncated]"


def run_local_command(
    project_root: str | Path,
    command: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Execute one program in ``project_root`` and return a JSON-safe result.

    Windows has no fully general, lossless equivalent of POSIX ``shlex.split``.
    The Windows parser here supports normal executable/argument commands and
    quoted arguments. Shell built-ins and operators such as pipes, redirects,
    and ``&&`` are intentionally unsupported because execution always uses
    ``shell=False``.
    """
    started = time.perf_counter()
    if not isinstance(command, str) or not command.strip():
        return _error_result(
            command if isinstance(command, str) else "",
            "Command must be a non-empty string.",
            started,
        )

    # Check the untouched command before tokenization and again at the execution
    # boundary, even though PermissionManager performs the same policy check.
    dangerous_pattern = dangerous_command_reason(command)
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

    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        return _error_result(
            command,
            f"Project root is not a directory: {root}",
            started,
        )

    try:
        arguments = split_command(command)
    except ValueError as exc:
        return _error_result(command, f"Could not parse command: {exc}", started)
    if not arguments:
        return _error_result(command, "Command must not be empty.", started)
    normalized_dangerous_pattern = dangerous_command_reason(" ".join(arguments))
    if normalized_dangerous_pattern is not None:
        return _error_result(
            command,
            (
                "Command blocked by safety policy after argument normalization: "
                f"matched prohibited pattern {normalized_dangerous_pattern!r}."
            ),
            started,
        )

    executable = arguments[0]
    resolved_executable = _resolve_executable(executable, root)
    if resolved_executable is None:
        return _error_result(
            command,
            (
                f"Executable {executable!r} was not found. "
                f"{_path_summary()}"
            ),
            started,
        )
    arguments[0] = resolved_executable

    try:
        completed = subprocess.run(
            arguments,
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
        stdout, stdout_truncated = _truncate_output(
            _output_text(exc.stdout),
            MAX_STDOUT_CHARACTERS,
        )
        stderr, stderr_truncated = _truncate_output(
            _output_text(exc.stderr),
            MAX_STDERR_CHARACTERS,
        )
        return {
            "ok": False,
            "command": command,
            "exit_code": None,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": _elapsed_ms(started),
            "truncated": stdout_truncated or stderr_truncated,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "timed_out": True,
            "error": f"Command timed out after {timeout_ms} ms.",
        }
    except OSError as exc:
        return _error_result(command, f"Could not start command: {exc}", started)

    stdout, stdout_truncated = _truncate_output(
        completed.stdout,
        MAX_STDOUT_CHARACTERS,
    )
    stderr, stderr_truncated = _truncate_output(
        completed.stderr,
        MAX_STDERR_CHARACTERS,
    )
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
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
        result["error"] = f"Command exited with code {completed.returncode}."
    return result


def _split_command(command: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command, posix=True)

    # shlex is POSIX-oriented. In non-POSIX mode it preserves surrounding
    # quotes, so remove only a single matching pair from each parsed argument.
    return [_strip_matching_quotes(item) for item in shlex.split(command, posix=False)]


def split_command(command: str) -> list[str]:
    """Parse a command using the same platform rules as local execution."""
    return _split_command(command)


def _resolve_executable(executable: str, cwd: Path) -> str | None:
    """Resolve one argv executable without involving a command shell."""
    path_value = os.environ.get("PATH")
    lookup_name = executable
    if "/" in executable or "\\" in executable:
        executable_path = Path(executable)
        if not executable_path.is_absolute():
            executable_path = cwd / executable_path
        lookup_name = str(executable_path)

    resolved = shutil.which(lookup_name, path=path_value)
    if resolved is not None:
        return str(resolved)
    if not _is_windows() or PureWindowsPath(executable).suffix:
        return None

    # ``shutil.which`` normally applies PATHEXT on Windows. Trying the validated
    # candidates explicitly also covers Python/platform combinations where the
    # extension lookup is not applied to an extensionless command.
    for extension in _windows_pathext():
        resolved = shutil.which(f"{lookup_name}{extension}", path=path_value)
        if resolved is not None:
            return str(resolved)
    return None


def resolve_executable(executable: str, cwd: str | Path) -> str | None:
    """Public executable resolver shared by local subprocess integrations."""
    return _resolve_executable(executable, Path(cwd).expanduser().resolve())


def _windows_pathext() -> tuple[str, ...]:
    raw_value = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    extensions: list[str] = []
    seen: set[str] = set()
    for raw_extension in raw_value.split(";"):
        extension = raw_extension.strip()
        if not extension.startswith("."):
            extension = f".{extension}"
        if (
            len(extension) < 2
            or len(extension) > 16
            or not extension[1:].isalnum()
        ):
            continue
        normalized = extension.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        extensions.append(extension)
    return tuple(extensions)


def _path_summary() -> str:
    raw_path = os.environ.get("PATH")
    if raw_path is None:
        summary = "PATH summary: PATH is unset."
    else:
        separator = ";" if _is_windows() else os.pathsep
        entry_count = sum(1 for entry in raw_path.split(separator) if entry.strip())
        summary = f"PATH summary: {entry_count} non-empty entries configured."
    if _is_windows():
        extensions = _windows_pathext()
        summary = (
            f"{summary} PATHEXT summary: {len(extensions)} validated "
            "candidates configured."
        )
    return summary


def executable_path_summary() -> str:
    """Return a sanitized PATH/PATHEXT summary without exposing their values."""
    return _path_summary()


def _is_windows() -> bool:
    return os.name == "nt"


def _strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _truncate_output(value: str, limit: int) -> tuple[str, bool]:
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


def _error_result(command: str, error: str, started: float) -> dict[str, Any]:
    return {
        "ok": False,
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


# Compatibility helpers retained for callers of the original argv-based API.
@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class LocalRunner:
    def __init__(self, cwd: str | Path) -> None:
        self.cwd = Path(cwd)

    def run(self, command: Sequence[str]) -> CommandResult:
        result = run(command, self.cwd)
        return result

    def run_command(
        self,
        command: str,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Run the model-facing string command API in this runner's directory."""
        return run_local_command(self.cwd, command, timeout_ms)


def run(command: Sequence[str], cwd: str | Path) -> CommandResult:
    command_text = _join_arguments(command)
    result = run_local_command(cwd, command_text)
    exit_code = result["exit_code"]
    return CommandResult(
        returncode=exit_code if isinstance(exit_code, int) else -1,
        stdout=str(result["stdout"]),
        stderr=str(result["stderr"] or result.get("error", "")),
    )


def _join_arguments(arguments: Sequence[str]) -> str:
    values = [str(argument) for argument in arguments]
    if os.name == "nt":
        return subprocess.list2cmdline(values)
    return shlex.join(values)
