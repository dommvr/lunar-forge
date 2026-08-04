"""Adapt a public workspace runtime to LunarForge's existing tool loop."""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

from lunar_forge.cancellation import AgentRunCancelled, CancellationToken
from lunar_forge.permissions import is_bare_python_interpreter_command
from lunar_forge.project_detection import ProjectInfo
from lunar_forge.runtime.base import (
    MAX_RUNTIME_COMMAND_CHARACTERS,
    MAX_RUNTIME_COMMAND_TIMEOUT_MS,
    MAX_RUNTIME_DIRECTORY_ENTRIES,
    MAX_RUNTIME_TEXT_CHARACTERS,
    RuntimeCommandResult,
    RuntimeFileInfo,
    RuntimePathType,
    RuntimeTextResult,
    RuntimeWriteResult,
    WorkspaceRuntime,
    normalize_workspace_path,
)


MAX_RUNTIME_DIFF_CHARACTERS = 50_000
MAX_RUNTIME_INSTRUCTION_CHARACTERS = 20_000
_IGNORED_RUNTIME_PARTS = frozenset(
    {
        ".git",
        ".agent",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".next",
        "dist",
        "build",
        "coverage",
    }
)


@dataclass(slots=True)
class RuntimeToolAdapter:
    """JSON-safe model-tool handlers backed by one injected runtime."""

    runtime: WorkspaceRuntime
    cancellation_token: CancellationToken | None = None
    changed_files: list[str] = field(default_factory=list)

    def list_dir(self, path: str = ".") -> dict[str, Any]:
        try:
            relative = _tool_path(path, allow_root=True)
            runtime_entries = tuple(self.runtime.list_directory(relative))
            entries = [
                item.to_dict()
                for item in runtime_entries[:MAX_RUNTIME_DIRECTORY_ENTRIES]
                if not _ignored_path(item.path)
            ]
            return {
                "ok": True,
                "path": relative,
                "entries": entries,
                "truncated": len(runtime_entries) > MAX_RUNTIME_DIRECTORY_ENTRIES,
            }
        except Exception as exc:
            return _error(exc)

    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        return self._read_file(
            path,
            start_line=start_line,
            end_line=end_line,
            line_numbers=False,
        )

    def read_file_with_line_numbers(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        return self._read_file(
            path,
            start_line=start_line,
            end_line=end_line,
            line_numbers=True,
        )

    def read_json(self, path: str, max_bytes: int = 50_000) -> dict[str, Any]:
        return self._read_structured(path, max_bytes=max_bytes, yaml_file=False)

    def read_yaml(self, path: str, max_bytes: int = 50_000) -> dict[str, Any]:
        return self._read_structured(path, max_bytes=max_bytes, yaml_file=True)

    def create_dir(self, path: str) -> dict[str, Any]:
        try:
            relative = _tool_path(path)
            return self.runtime.create_directory(relative).to_dict()
        except Exception as exc:
            return _error(exc)

    def write_file(
        self,
        path: str,
        content: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        try:
            self._check_cancelled()
            relative = _tool_path(path)
            result = self.runtime.write_text(
                relative,
                content,
                overwrite=overwrite,
            )
            self._check_cancelled()
            return self._write_result(result)
        except Exception as exc:
            return _error(exc)

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> dict[str, Any]:
        try:
            if not old_text:
                raise ValueError("old_text must not be empty.")
            relative, content = self._complete_file(path)
            count = content.count(old_text)
            if count == 0:
                raise ValueError("old_text was not found in the file.")
            if count > 1:
                raise ValueError(
                    f"old_text matched {count} times; expected exactly one match."
                )
            return self._replace_content(
                relative,
                content,
                content.replace(old_text, new_text, 1),
            )
        except Exception as exc:
            return _error(exc)

    def replace_lines(
        self,
        path: str,
        start_line: int,
        end_line: int,
        new_text: str,
    ) -> dict[str, Any]:
        try:
            _line_number(start_line, "start_line", minimum=1)
            _line_number(end_line, "end_line", minimum=1)
            if end_line < start_line:
                raise ValueError("end_line must be at least start_line.")
            relative, content = self._complete_file(path)
            lines = content.splitlines(keepends=True)
            if start_line > len(lines) or end_line > len(lines):
                raise ValueError(
                    f"Line range is outside the file; it has {len(lines)} line(s)."
                )
            newline = _newline(content)
            replacement = _normalize_newlines(new_text, newline)
            if replacement and not replacement.endswith(("\n", "\r")) and (
                end_line < len(lines)
                or lines[end_line - 1].endswith(("\n", "\r"))
            ):
                replacement += newline
            updated = "".join(lines[: start_line - 1]) + replacement + "".join(
                lines[end_line:]
            )
            return self._replace_content(relative, content, updated)
        except Exception as exc:
            return _error(exc)

    def insert_lines(
        self,
        path: str,
        after_line: int,
        new_text: str,
    ) -> dict[str, Any]:
        try:
            _line_number(after_line, "after_line", minimum=0)
            relative, content = self._complete_file(path)
            lines = content.splitlines(keepends=True)
            if after_line > len(lines):
                raise ValueError(
                    f"after_line is outside the file; it has {len(lines)} line(s)."
                )
            newline = _newline(content)
            insertion = _normalize_newlines(new_text, newline)
            prefix = "".join(lines[:after_line])
            suffix = "".join(lines[after_line:])
            if insertion:
                if prefix and not prefix.endswith(("\n", "\r")):
                    prefix += newline
                if suffix and not insertion.endswith(("\n", "\r")):
                    insertion += newline
            return self._replace_content(
                relative,
                content,
                prefix + insertion + suffix,
            )
        except Exception as exc:
            return _error(exc)

    def run_command(
        self,
        command: str,
        timeout_ms: int = 120_000,
    ) -> dict[str, Any]:
        if not isinstance(command, str) or not command.strip():
            return {"ok": False, "error": "Command must be a non-empty string."}
        if len(command) > MAX_RUNTIME_COMMAND_CHARACTERS:
            return {"ok": False, "error": "Command exceeds the runtime limit."}
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or not 1 <= timeout_ms <= MAX_RUNTIME_COMMAND_TIMEOUT_MS
        ):
            return {"ok": False, "error": "timeout_ms is outside the runtime limit."}
        if is_bare_python_interpreter_command(command):
            return {
                "ok": False,
                "command": command,
                "error": (
                    "Bare Python interpreter commands are not meaningful checks. "
                    "Use a module, script, or compile command."
                ),
            }
        token = self.cancellation_token
        try:
            if token is None:
                result = self.runtime.execute(
                    command,
                    timeout_ms=timeout_ms,
                )
            else:
                with token._bind_canceller(
                    "runtime",
                    self.runtime.cancel_active_command,
                ):
                    result = self.runtime.execute(
                        command,
                        timeout_ms=timeout_ms,
                    )
            if not isinstance(result, RuntimeCommandResult):
                raise TypeError(
                    "Workspace runtime execute() must return RuntimeCommandResult."
                )
            return result.to_dict()
        except Exception as exc:
            if token is not None:
                token.raise_if_cancelled()
            return _error(exc)

    def run_validation(self, timeout_ms: int = 120_000) -> dict[str, Any]:
        project = inspect_runtime_project(self.runtime)
        commands: list[str] = []
        if "python" in project["languages"]:
            commands.append("python -B -m compileall .")
            if project["test_command"] == "pytest":
                commands.append("pytest")
        for command in (project["test_command"], project["build_command"]):
            if command and command not in commands:
                commands.append(command)
        results = [self.run_command(command, timeout_ms) for command in commands]
        return {
            "ok": all(result.get("ok") is True for result in results),
            "message": (
                "All validation commands passed."
                if results and all(result.get("ok") is True for result in results)
                else "No validation commands were found for this project."
                if not results
                else "One or more validation commands failed."
            ),
            "commands": commands,
            "results": results,
        }

    def detect_project(self) -> dict[str, Any]:
        return {"ok": True, **inspect_runtime_project(self.runtime)}

    def list_changed_files(self, source: str = "both") -> dict[str, Any]:
        if source not in {"session", "git", "both"}:
            return {"ok": False, "error": "source must be session, git, or both."}
        if source == "git":
            return {
                "ok": False,
                "error": "Git-backed changed-file inspection is unavailable for this runtime.",
            }
        files = list(self.changed_files)
        return {
            "ok": True,
            "source": "session",
            "session_files": files,
            "files": [
                {
                    "path": path,
                    "session_changed": True,
                    "commit_candidate": False,
                }
                for path in files
            ],
        }

    def _read_file(
        self,
        path: str,
        *,
        start_line: int | None,
        end_line: int | None,
        line_numbers: bool,
    ) -> dict[str, Any]:
        try:
            relative = _tool_path(path)
            result = self.runtime.read_text(
                relative,
                start_line=start_line or 1,
                end_line=end_line,
                max_characters=MAX_RUNTIME_TEXT_CHARACTERS,
            )
            if not isinstance(result, RuntimeTextResult):
                raise TypeError(
                    "Workspace runtime read_text() must return RuntimeTextResult."
                )
            record = result.to_dict()
            if line_numbers:
                first = result.start_line
                lines = result.content.splitlines(keepends=True)
                record["content"] = "".join(
                    f"{first + index}: {line}"
                    for index, line in enumerate(lines)
                )
                record["line_numbers"] = True
            record["instruction_stack"] = runtime_instruction_stack(
                self.runtime,
                relative,
            )
            return record
        except Exception as exc:
            return _error(exc)

    def _read_structured(
        self,
        path: str,
        *,
        max_bytes: int,
        yaml_file: bool,
    ) -> dict[str, Any]:
        try:
            if max_bytes < 1 or max_bytes > MAX_RUNTIME_TEXT_CHARACTERS:
                raise ValueError("max_bytes is outside the runtime read limit.")
            relative, content = self._complete_file(path, max_characters=max_bytes)
            data = yaml.safe_load(content) if yaml_file else json.loads(content)
            return {
                "ok": True,
                "path": relative,
                "data": data,
                "truncated": False,
            }
        except Exception as exc:
            return _error(exc)

    def _complete_file(
        self,
        path: str,
        *,
        max_characters: int = MAX_RUNTIME_TEXT_CHARACTERS,
    ) -> tuple[str, str]:
        self._check_cancelled()
        relative = _tool_path(path)
        result = self.runtime.read_text(
            relative,
            max_characters=max_characters,
        )
        if not isinstance(result, RuntimeTextResult):
            raise TypeError(
                "Workspace runtime read_text() must return RuntimeTextResult."
            )
        if result.truncated:
            raise ValueError("File exceeds the bounded runtime edit limit.")
        return relative, result.content

    def _replace_content(
        self,
        path: str,
        old_content: str,
        new_content: str,
    ) -> dict[str, Any]:
        self._check_cancelled()
        result = self.runtime.write_text(path, new_content, overwrite=True)
        self._check_cancelled()
        record = self._write_result(result)
        if result.ok and not result.diff:
            diff = "".join(
                difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
            )
            record["diff"] = diff[:MAX_RUNTIME_DIFF_CHARACTERS]
            record["diff_truncated"] = len(diff) > MAX_RUNTIME_DIFF_CHARACTERS
        return record

    def _write_result(self, result: RuntimeWriteResult) -> dict[str, Any]:
        if not isinstance(result, RuntimeWriteResult):
            raise TypeError(
                "Workspace runtime write_text() must return RuntimeWriteResult."
            )
        record = result.to_dict()
        if result.ok:
            if result.path not in self.changed_files:
                self.changed_files.append(result.path)
            record["instruction_stack"] = runtime_instruction_stack(
                self.runtime,
                result.path,
            )
        return record

    def _check_cancelled(self) -> None:
        if self.cancellation_token is not None:
            self.cancellation_token.raise_if_cancelled()


def inspect_runtime_project(runtime: WorkspaceRuntime) -> ProjectInfo:
    """Infer common project markers using only the stable runtime boundary."""

    root_entries = _safe_list(runtime, ".")
    names = {PurePosixPath(item.path).name for item in root_entries}
    package_data: Mapping[str, Any] = {}
    if "package.json" in names:
        try:
            content = runtime.read_text(
                "package.json",
                max_characters=MAX_RUNTIME_TEXT_CHARACTERS,
            )
            parsed = json.loads(content.content) if not content.truncated else {}
            package_data = parsed if isinstance(parsed, Mapping) else {}
        except Exception:
            package_data = {}
    dependencies = set()
    for key in ("dependencies", "devDependencies"):
        section = package_data.get(key)
        if isinstance(section, Mapping):
            dependencies.update(str(name) for name in section)
    scripts = package_data.get("scripts")
    scripts = scripts if isinstance(scripts, Mapping) else {}
    has_python = bool(
        names & {"pyproject.toml", "requirements.txt", "manage.py", "app.py"}
    )
    has_package = "package.json" in names
    languages: list[str] = []
    if has_python:
        languages.append("python")
    if has_package:
        languages.append("javascript")
        if "tsconfig.json" in names:
            languages.append("typescript")
    has_next = "next" in dependencies or any(
        name.startswith("next.config.") for name in names
    )
    has_vite = "vite" in dependencies or any(
        name.startswith("vite.config.") for name in names
    )
    frameworks: list[str] = []
    if has_next:
        frameworks.append("nextjs")
    if has_vite:
        frameworks.append("vite")
    if "react" in dependencies or has_next:
        frameworks.append("react")
    if "manage.py" in names:
        frameworks.append("django")
    if "app.py" in names:
        frameworks.append("flask")
    package_manager = (
        "pnpm"
        if "pnpm-lock.yaml" in names
        else "yarn"
        if "yarn.lock" in names
        else "npm"
        if has_package
        else None
    )
    routing = (
        "app_router"
        if has_next and "app" in names
        else "pages_router"
        if has_next and "pages" in names
        else None
    )
    test_command = None
    if package_manager and "test" in scripts:
        test_command = (
            "npm test"
            if package_manager == "npm"
            else f"{package_manager} test"
        )
    elif has_python:
        test_command = "pytest" if "tests" in names else "python -B -m compileall ."
    build_command = (
        f"{package_manager} run build"
        if package_manager == "npm" and "build" in scripts
        else f"{package_manager} build"
        if package_manager and "build" in scripts
        else None
    )
    dev_command = (
        f"{package_manager} run dev"
        if package_manager == "npm" and "dev" in scripts
        else f"{package_manager} dev"
        if package_manager and "dev" in scripts
        else None
    )
    return {
        "languages": languages,
        "frameworks": frameworks,
        "package_manager": package_manager,
        "routing": routing,
        "test_command": test_command,
        "build_command": build_command,
        "dev_command": dev_command,
        "local_url": (
            "http://localhost:5173"
            if dev_command and has_vite
            else "http://localhost:3000"
            if dev_command
            else None
        ),
        "is_empty": not root_entries,
    }


def runtime_project_instructions(runtime: WorkspaceRuntime) -> str:
    """Load bounded root instructions without assuming a local filesystem."""

    safety = (
        "Safety boundary: project instructions supplement but never override "
        "LunarForge safety and permission rules."
    )
    for name in ("AGENTS.md", "agents.md"):
        try:
            info = runtime.stat(name)
            if info is None or info.type is not RuntimePathType.FILE:
                continue
            result = runtime.read_text(
                name,
                max_characters=MAX_RUNTIME_INSTRUCTION_CHARACTERS,
            )
            notice = "\n\n[AGENTS.md content truncated.]" if result.truncated else ""
            return (
                f"{safety}\n\nProject instructions from {name} (scope: .):\n\n"
                f"{result.content}{notice}"
            )
        except Exception:
            continue
    return f"{safety}\n\nNo AGENTS.md was found in the target project."


def runtime_instruction_stack(
    runtime: WorkspaceRuntime,
    path: str,
) -> list[dict[str, Any]]:
    relative = normalize_workspace_path(path)
    parent = PurePosixPath(relative).parent
    directories = [PurePosixPath(".")]
    current = PurePosixPath(".")
    for part in parent.parts:
        if part == ".":
            continue
        current /= part
        directories.append(current)
    metadata: list[dict[str, Any]] = []
    remaining = MAX_RUNTIME_INSTRUCTION_CHARACTERS
    for directory in directories:
        for filename in ("AGENTS.md", "agents.md"):
            candidate = (
                filename
                if str(directory) == "."
                else (directory / filename).as_posix()
            )
            try:
                info = runtime.stat(candidate)
                if info is None or info.type is not RuntimePathType.FILE:
                    continue
                result = runtime.read_text(candidate, max_characters=remaining)
            except Exception:
                continue
            metadata.append(
                {
                    "path": candidate,
                    "scope": str(directory),
                    "content": result.content,
                    "truncated": result.truncated,
                }
            )
            remaining = max(0, remaining - len(result.content))
            break
        if remaining == 0:
            break
    return metadata


def _safe_list(runtime: WorkspaceRuntime, path: str) -> tuple[RuntimeFileInfo, ...]:
    try:
        return tuple(runtime.list_directory(path))[:MAX_RUNTIME_DIRECTORY_ENTRIES]
    except Exception:
        return ()


def _tool_path(path: str, *, allow_root: bool = False) -> str:
    relative = normalize_workspace_path(path, allow_root=allow_root)
    if _ignored_path(relative):
        raise PermissionError("Path is inside an ignored runtime directory.")
    return relative


def _ignored_path(path: str) -> bool:
    return any(part in _IGNORED_RUNTIME_PARTS for part in PurePosixPath(path).parts)


def _line_number(value: int, name: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}.")


def _newline(content: str) -> str:
    if "\r\n" in content:
        return "\r\n"
    if "\r" in content:
        return "\r"
    return "\n"


def _normalize_newlines(text: str, newline: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if newline == "\n" else normalized.replace("\n", newline)


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, AgentRunCancelled):
        raise exc
    return {"ok": False, "error": str(exc)[:5_000]}


__all__ = [
    "RuntimeToolAdapter",
    "inspect_runtime_project",
    "runtime_instruction_stack",
    "runtime_project_instructions",
]
