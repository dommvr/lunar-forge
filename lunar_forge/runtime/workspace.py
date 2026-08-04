"""Built-in local, Docker, and no-command workspace runtime adapters."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from lunar_forge.permissions import (
    dangerous_command_reason,
    normalized_dangerous_command_reason,
)
from lunar_forge.runtime.base import (
    MAX_RUNTIME_DIRECTORY_ENTRIES,
    MAX_RUNTIME_COMMAND_CHARACTERS,
    MAX_RUNTIME_COMMAND_TIMEOUT_MS,
    MAX_RUNTIME_OUTPUT_CHARACTERS,
    MAX_RUNTIME_TEXT_CHARACTERS,
    RuntimeCheckpoint,
    RuntimeCommandResult,
    RuntimeFileInfo,
    RuntimeNetworkPolicy,
    RuntimeOperationResult,
    RuntimePathType,
    RuntimeRollbackResult,
    RuntimeRollbackStatus,
    RuntimeTextResult,
    RuntimeWriteResult,
    normalize_workspace_path,
)
from lunar_forge.runtime.checkpoints import rollback_file
from lunar_forge.runtime.docker_runner import build_docker_args
from lunar_forge.runtime.local_runner import resolve_executable, split_command
from lunar_forge.tools.files import (
    IGNORED_DIRECTORIES,
    create_dir,
    list_dir,
    read_file,
    safe_path,
    write_file,
)


@dataclass(slots=True)
class _TurnMutation:
    path: str
    created: bool
    checkpoint_id: str | None
    expected_digest: str | None


class LocalWorkspaceRuntime:
    """Project-confined local filesystem and cancellable local commands."""

    def __init__(self, project_root: str | Path) -> None:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Project root is not a directory: {root}")
        self._root = root
        self._state_lock = Lock()
        self._active_process: subprocess.Popen[str] | None = None
        self._active_turn_id: str | None = None
        self._mutations: dict[str, _TurnMutation] = {}

    @property
    def workspace_id(self) -> str:
        return str(self._root)

    @property
    def local_project_root(self) -> Path:
        return self._root

    @property
    def network_policy(self) -> RuntimeNetworkPolicy:
        return RuntimeNetworkPolicy.HOST

    def list_directory(self, path: str = ".") -> tuple[RuntimeFileInfo, ...]:
        relative = normalize_workspace_path(path, allow_root=True)
        result = list_dir(self._root, relative)
        if result.get("ok") is not True:
            raise OSError(str(result.get("error", "Directory listing failed.")))
        entries: list[RuntimeFileInfo] = []
        for item in result.get("entries", ())[:MAX_RUNTIME_DIRECTORY_ENTRIES]:
            if not isinstance(item, dict):
                continue
            entries.append(
                RuntimeFileInfo(
                    path=str(item["path"]),
                    type=str(item.get("type", "other")),
                    size_bytes=(
                        item.get("size")
                        if isinstance(item.get("size"), int)
                        else None
                    ),
                )
            )
        return tuple(entries)

    def stat(self, path: str) -> RuntimeFileInfo | None:
        relative = normalize_workspace_path(path, allow_root=True)
        raw_target = self._root if relative == "." else self._root / relative
        safe_path(self._root, raw_target.parent)
        if raw_target.is_symlink():
            stat_result = raw_target.lstat()
            return RuntimeFileInfo(
                path=relative,
                type=RuntimePathType.SYMLINK,
                modified_at=datetime.fromtimestamp(
                    stat_result.st_mtime,
                    tz=timezone.utc,
                ).isoformat(),
            )
        target = safe_path(self._root, raw_target)
        if not target.exists() and not target.is_symlink():
            return None
        if target.is_file():
            kind = RuntimePathType.FILE
        elif target.is_dir():
            kind = RuntimePathType.DIRECTORY
        else:
            kind = RuntimePathType.OTHER
        stat_result = target.stat()
        return RuntimeFileInfo(
            path=relative,
            type=kind,
            size_bytes=stat_result.st_size if target.is_file() else None,
            modified_at=datetime.fromtimestamp(
                stat_result.st_mtime,
                tz=timezone.utc,
            ).isoformat(),
        )

    def read_text(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
        max_characters: int = 50_000,
    ) -> RuntimeTextResult:
        if (
            isinstance(max_characters, bool)
            or not isinstance(max_characters, int)
            or not 1 <= max_characters <= MAX_RUNTIME_TEXT_CHARACTERS
        ):
            raise ValueError("max_characters is outside the runtime read limit.")
        relative = normalize_workspace_path(path)
        result = read_file(
            self._root,
            relative,
            start_line=start_line,
            end_line=end_line,
        )
        if result.get("ok") is not True:
            raise OSError(str(result.get("error", "File read failed.")))
        content = str(result.get("content", ""))
        clipped = len(content) > max_characters
        return RuntimeTextResult(
            path=relative,
            content=content[:max_characters],
            truncated=result.get("truncated") is True or clipped,
            start_line=int(result.get("start_line") or start_line),
            end_line=(
                result.get("end_line")
                if isinstance(result.get("end_line"), int)
                else None
            ),
        )

    def write_text(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = False,
    ) -> RuntimeWriteResult:
        relative = normalize_workspace_path(path)
        result = write_file(
            self._root,
            relative,
            content,
            overwrite=overwrite,
        )
        write_result = RuntimeWriteResult(
            ok=result.get("ok") is True,
            path=relative,
            created=result.get("created") is True,
            overwritten=result.get("overwritten") is True,
            diff=str(result.get("diff", "")),
            diff_truncated=result.get("diff_truncated") is True,
            checkpoint_id=(
                str(result["checkpoint_path"])
                if result.get("checkpoint_path")
                else None
            ),
            error=(
                str(result["error"])
                if result.get("error") is not None
                else None
            ),
        )
        if write_result.ok:
            self._record_mutation(write_result)
        return write_result

    def create_directory(self, path: str) -> RuntimeOperationResult:
        relative = normalize_workspace_path(path)
        result = create_dir(self._root, relative)
        return RuntimeOperationResult(
            ok=result.get("ok") is True,
            path=relative,
            error=str(result["error"]) if result.get("error") else None,
        )

    def delete_path(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> RuntimeOperationResult:
        relative = _mutable_path(path)
        try:
            target = self._root / relative
            safe_path(self._root, target.parent)
            if not target.exists() and not target.is_symlink():
                raise FileNotFoundError("Workspace path does not exist.")
            if target.is_symlink():
                target.unlink()
            elif target.is_dir():
                if not recursive:
                    target.rmdir()
                else:
                    shutil.rmtree(target)
            else:
                target.unlink()
            return RuntimeOperationResult(True, relative)
        except (OSError, PermissionError, ValueError) as exc:
            return RuntimeOperationResult(False, relative, error=str(exc))

    def move_path(
        self,
        source: str,
        destination: str,
        *,
        overwrite: bool = False,
    ) -> RuntimeOperationResult:
        source_path = _mutable_path(source)
        destination_path = _mutable_path(destination)
        try:
            source_target = self._root / source_path
            destination_target = self._root / destination_path
            safe_path(self._root, source_target.parent)
            safe_path(self._root, destination_target.parent)
            if not source_target.exists() and not source_target.is_symlink():
                raise FileNotFoundError("Source workspace path does not exist.")
            if (
                destination_target.exists() or destination_target.is_symlink()
            ) and not overwrite:
                raise FileExistsError("Destination already exists.")
            destination_target.parent.mkdir(parents=True, exist_ok=True)
            if (
                destination_target.exists() or destination_target.is_symlink()
            ) and overwrite:
                if destination_target.is_symlink():
                    destination_target.unlink()
                elif destination_target.is_dir():
                    shutil.rmtree(destination_target)
                else:
                    destination_target.unlink()
            source_target.replace(destination_target)
            return RuntimeOperationResult(
                True,
                source_path,
                destination=destination_path,
            )
        except (OSError, PermissionError, ValueError) as exc:
            return RuntimeOperationResult(
                False,
                source_path,
                destination=destination_path,
                error=str(exc),
            )

    def execute(
        self,
        command: str,
        *,
        timeout_ms: int,
        max_output_characters: int = MAX_RUNTIME_OUTPUT_CHARACTERS,
    ) -> RuntimeCommandResult:
        started = time.perf_counter()
        validation_error = _validate_command(command, timeout_ms)
        if validation_error is not None:
            return _command_error(command, validation_error, started)
        try:
            arguments = split_command(command)
        except ValueError as exc:
            return _command_error(command, f"Could not parse command: {exc}", started)
        if not arguments:
            return _command_error(command, "Command must not be empty.", started)
        executable = resolve_executable(arguments[0], self._root)
        if executable is None:
            return _command_error(
                command,
                f"Executable {arguments[0]!r} was not found.",
                started,
            )
        arguments[0] = executable
        return self._run_process(
            arguments,
            command=command,
            timeout_ms=timeout_ms,
            max_output_characters=max_output_characters,
            started=started,
        )

    def cancel_active_command(self) -> bool:
        with self._state_lock:
            process = self._active_process
        if process is None or process.poll() is not None:
            return False
        try:
            process.terminate()
            return True
        except OSError:
            return False

    def checkpoint_turn(self, turn_id: str) -> RuntimeCheckpoint:
        with self._state_lock:
            self._active_turn_id = str(turn_id)
            self._mutations.clear()
        return RuntimeCheckpoint(True, checkpoint_id=str(turn_id))

    def rollback_turn(self, checkpoint_id: str) -> RuntimeRollbackResult:
        with self._state_lock:
            if self._active_turn_id != checkpoint_id:
                return RuntimeRollbackResult(
                    RuntimeRollbackStatus.FAILED,
                    errors=("Runtime checkpoint is not active.",),
                )
            mutations = tuple(self._mutations.values())
        restored: list[str] = []
        removed: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []
        for mutation in mutations:
            try:
                target = safe_path(self._root, mutation.path)
                if mutation.expected_digest is None:
                    skipped.append(mutation.path)
                    continue
                if not target.exists():
                    if mutation.created:
                        removed.append(mutation.path)
                    else:
                        skipped.append(mutation.path)
                    continue
                if not target.is_file() or _digest(target) != mutation.expected_digest:
                    skipped.append(
                        mutation.path
                    )
                    continue
                if mutation.created:
                    target.unlink()
                    removed.append(mutation.path)
                    continue
                checkpoint_id_value = _local_checkpoint_id(mutation.checkpoint_id)
                if checkpoint_id_value is None:
                    skipped.append(mutation.path)
                    continue
                result = rollback_file(
                    self._root,
                    mutation.path,
                    checkpoint_id=checkpoint_id_value,
                )
                if result.get("ok") is True:
                    restored.append(mutation.path)
                else:
                    errors.append(
                        f"{mutation.path}: {result.get('error', 'rollback failed')}"
                    )
            except (OSError, PermissionError, ValueError) as exc:
                errors.append(f"{mutation.path}: {exc}")
        status = (
            RuntimeRollbackStatus.COMPLETED
            if not skipped and not errors
            else RuntimeRollbackStatus.PARTIAL
        )
        return RuntimeRollbackResult(
            status,
            restored_files=tuple(restored),
            removed_files=tuple(removed),
            skipped_files=tuple(skipped),
            errors=tuple(errors),
        )

    def _record_mutation(self, result: RuntimeWriteResult) -> None:
        try:
            target = safe_path(self._root, result.path)
            digest = _digest(target) if target.is_file() else None
        except (OSError, PermissionError, ValueError):
            digest = None
        with self._state_lock:
            if self._active_turn_id is None:
                return
            existing = self._mutations.get(result.path)
            if existing is None:
                self._mutations[result.path] = _TurnMutation(
                    path=result.path,
                    created=result.created,
                    checkpoint_id=result.checkpoint_id,
                    expected_digest=digest,
                )
                return
            if existing.checkpoint_id is None and not existing.created:
                existing.checkpoint_id = result.checkpoint_id
            existing.expected_digest = digest

    def _run_process(
        self,
        arguments: list[str],
        *,
        command: str,
        timeout_ms: int,
        max_output_characters: int,
        started: float,
    ) -> RuntimeCommandResult:
        try:
            process = subprocess.Popen(
                arguments,
                cwd=self._root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError as exc:
            return _command_error(command, f"Could not start command: {exc}", started)
        with self._state_lock:
            self._active_process = process
        timed_out = False
        cancelled = False
        try:
            try:
                stdout, stderr = process.communicate(timeout=timeout_ms / 1000)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                stdout, stderr = process.communicate()
            return_code = process.returncode
            cancelled = not timed_out and return_code is not None and return_code < 0
        finally:
            with self._state_lock:
                if self._active_process is process:
                    self._active_process = None
        stdout, stdout_truncated = _truncate(stdout, max_output_characters)
        stderr, stderr_truncated = _truncate(stderr, max_output_characters)
        ok = return_code == 0 and not timed_out and not cancelled
        error = None
        if timed_out:
            error = f"Command timed out after {timeout_ms} ms."
        elif cancelled:
            error = "Command was cancelled."
        elif return_code != 0:
            error = f"Command exited with code {return_code}."
        return RuntimeCommandResult(
            ok=ok,
            command=command,
            exit_code=None if timed_out or cancelled else return_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=_elapsed_ms(started),
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            error=error,
        )


class DockerWorkspaceRuntime(LocalWorkspaceRuntime):
    """Local workspace files with cancellable commands in the fixed Docker image."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        allow_network: bool = False,
    ) -> None:
        super().__init__(project_root)
        self.allow_network = bool(allow_network)

    @property
    def network_policy(self) -> RuntimeNetworkPolicy:
        return (
            RuntimeNetworkPolicy.ALLOWED
            if self.allow_network
            else RuntimeNetworkPolicy.DENIED
        )

    def execute(
        self,
        command: str,
        *,
        timeout_ms: int,
        max_output_characters: int = MAX_RUNTIME_OUTPUT_CHARACTERS,
    ) -> RuntimeCommandResult:
        started = time.perf_counter()
        validation_error = _validate_command(command, timeout_ms)
        if validation_error is not None:
            return _command_error(command, validation_error, started)
        try:
            info = subprocess.run(
                ["docker", "info"],
                cwd=self.local_project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(timeout_ms / 1000, 30),
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _command_error(command, f"Docker is unavailable: {exc}", started)
        if info.returncode != 0:
            return _command_error(command, "Docker is unavailable: docker info failed.", started)
        arguments = build_docker_args(
            self.local_project_root,
            command,
            allow_network=self.allow_network,
        )
        result = self._run_process(
            arguments,
            command=command,
            timeout_ms=timeout_ms,
            max_output_characters=max_output_characters,
            started=started,
        )
        return result


class NoCommandWorkspaceRuntime(LocalWorkspaceRuntime):
    """Local workspace adapter that deterministically denies commands."""

    @property
    def network_policy(self) -> RuntimeNetworkPolicy:
        return RuntimeNetworkPolicy.DENIED

    def execute(
        self,
        command: str,
        *,
        timeout_ms: int,
        max_output_characters: int = MAX_RUNTIME_OUTPUT_CHARACTERS,
    ) -> RuntimeCommandResult:
        return RuntimeCommandResult(
            ok=False,
            command=command,
            error="Command execution is disabled by runtime mode.",
        )


def create_workspace_runtime(
    project_root: str | Path,
    *,
    mode: str = "local",
    allow_network: bool = False,
) -> LocalWorkspaceRuntime:
    """Create one compatible built-in runtime without changing old defaults."""

    normalized = mode.strip().lower()
    if normalized == "local":
        return LocalWorkspaceRuntime(project_root)
    if normalized == "docker":
        return DockerWorkspaceRuntime(
            project_root,
            allow_network=allow_network,
        )
    if normalized == "no-command":
        return NoCommandWorkspaceRuntime(project_root)
    raise ValueError(f"Unsupported built-in runtime mode: {mode!r}.")


def _mutable_path(path: str | Path) -> str:
    relative = normalize_workspace_path(path)
    if relative.split("/", 1)[0] in IGNORED_DIRECTORIES:
        raise PermissionError("Path is inside an ignored runtime directory.")
    return relative


def _validate_command(command: str, timeout_ms: int) -> str | None:
    if not isinstance(command, str) or not command.strip():
        return "Command must be a non-empty string."
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
        return "timeout_ms must be a positive integer."
    if timeout_ms > MAX_RUNTIME_COMMAND_TIMEOUT_MS:
        return (
            "timeout_ms exceeds the public runtime maximum of "
            f"{MAX_RUNTIME_COMMAND_TIMEOUT_MS}."
        )
    if len(command) > MAX_RUNTIME_COMMAND_CHARACTERS:
        return "Command exceeds the public runtime command limit."
    dangerous = dangerous_command_reason(command)
    if dangerous is None:
        dangerous = normalized_dangerous_command_reason(command)
    if dangerous is not None:
        return f"Command blocked by safety policy: matched prohibited pattern {dangerous!r}."
    return None


def _command_error(
    command: str,
    error: str,
    started: float,
) -> RuntimeCommandResult:
    return RuntimeCommandResult(
        ok=False,
        command=command,
        duration_ms=_elapsed_ms(started),
        error=error,
    )


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    marker = "\n...[runtime output truncated]"
    return f"{value[: max(0, limit - len(marker))]}{marker}", True


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _local_checkpoint_id(value: str | None) -> str | None:
    if value is None:
        return None
    parts = Path(value.replace("\\", "/")).parts
    if len(parts) < 3 or parts[0] != ".agent" or parts[1] != "checkpoints":
        return None
    return parts[2]


__all__ = [
    "DockerWorkspaceRuntime",
    "LocalWorkspaceRuntime",
    "NoCommandWorkspaceRuntime",
    "create_workspace_runtime",
]
