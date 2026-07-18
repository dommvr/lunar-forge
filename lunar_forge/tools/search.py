"""Bounded search tools confined to a project root."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from lunar_forge.tools.files import IGNORED_DIRECTORIES, safe_path


MAX_SEARCH_RESULTS = 100
MAX_GLOB_RESULTS = 200
MAX_SNIPPET_CHARACTERS = 300
MAX_SEARCH_LINE_CHARACTERS = 20_000


def grep(
    project_root: str | Path,
    pattern: str,
    path: str | Path = ".",
) -> dict[str, Any]:
    """Search UTF-8 files with a regular expression and return short matches."""
    try:
        expression = re.compile(pattern)
        root = Path(project_root).expanduser().resolve()
        search_root = safe_path(root, path)
        _assert_searchable(root, search_root)

        matches: list[dict[str, Any]] = []
        truncated = False
        for file_path in _iter_files(root, search_root):
            try:
                with file_path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        searchable_line = line[:MAX_SEARCH_LINE_CHARACTERS]
                        if not expression.search(searchable_line):
                            continue
                        matches.append(
                            {
                                "path": file_path.relative_to(root).as_posix(),
                                "line": line_number,
                                "snippet": line.rstrip("\r\n")[:MAX_SNIPPET_CHARACTERS],
                            }
                        )
                        if len(matches) > MAX_SEARCH_RESULTS:
                            matches.pop()
                            truncated = True
                            break
            except (OSError, UnicodeError):
                continue
            if truncated:
                break

        return {
            "ok": True,
            "pattern": pattern,
            "path": _display_path(root, search_root),
            "matches": matches,
            "truncated": truncated,
        }
    except (OSError, PermissionError, re.error, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def glob_files(project_root: str | Path, pattern: str) -> dict[str, Any]:
    """Return project-relative files matching a glob pattern."""
    try:
        if not pattern:
            raise ValueError("Glob pattern must not be empty.")

        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError("Project root is not a directory.")

        normalized_pattern = pattern.replace("\\", "/")
        matches: list[str] = []
        truncated = False
        for file_path in _iter_files(root, root):
            relative = file_path.relative_to(root).as_posix()
            if not _matches_glob(relative, normalized_pattern):
                continue
            matches.append(relative)
            if len(matches) > MAX_GLOB_RESULTS:
                matches.pop()
                truncated = True
                break

        return {
            "ok": True,
            "pattern": pattern,
            "matches": matches,
            "truncated": truncated,
        }
    except (OSError, PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def find_files(root: str | Path, pattern: str) -> list[Path]:
    """Compatibility wrapper returning paths for existing callers."""
    root_path = Path(root).expanduser().resolve()
    result = glob_files(root_path, pattern)
    if not result["ok"]:
        return []
    return [root_path / relative for relative in result["matches"]]


def _iter_files(root: Path, search_root: Path) -> Iterator[Path]:
    if search_root.is_file():
        yield search_root
        return
    if not search_root.is_dir():
        raise FileNotFoundError("Search path does not exist.")

    for current_root, directory_names, file_names in os.walk(
        search_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        safe_directories: list[str] = []
        for name in sorted(directory_names, key=str.casefold):
            if name in IGNORED_DIRECTORIES:
                continue
            candidate = current / name
            if candidate.is_symlink():
                continue
            try:
                safe_path(root, candidate)
            except PermissionError:
                continue
            safe_directories.append(name)
        directory_names[:] = safe_directories

        for name in sorted(file_names, key=str.casefold):
            candidate = current / name
            if candidate.is_symlink():
                continue
            try:
                yield safe_path(root, candidate)
            except PermissionError:
                continue


def _assert_searchable(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    if any(part in IGNORED_DIRECTORIES for part in relative.parts):
        raise PermissionError("Path is inside an ignored directory.")
    if not path.exists():
        raise FileNotFoundError("Search path does not exist.")


def _matches_glob(relative: str, pattern: str) -> bool:
    relative_path = PurePosixPath(relative)
    if relative_path.match(pattern):
        return True
    if pattern.startswith("**/"):
        return relative_path.match(pattern[3:])
    return False


def _display_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    return relative.as_posix() if relative.parts else "."
