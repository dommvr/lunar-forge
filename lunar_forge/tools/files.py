"""Bounded file tools confined to a project root."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any


IGNORED_DIRECTORIES = frozenset(
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

MAX_DIRECTORY_ENTRIES = 200
MAX_FILE_LINES = 400
MAX_FILE_CHARACTERS = 50_000
MAX_DIFF_CHARACTERS = 50_000


def safe_path(project_root: str | Path, path: str | Path) -> Path:
    """Resolve ``path`` and ensure it remains within ``project_root``.

    Resolving both paths also prevents an existing symlink inside the project
    from being used to reach a file outside it.
    """
    root = Path(project_root).expanduser().resolve()
    requested = Path(path).expanduser()
    candidate = (
        requested.resolve()
        if requested.is_absolute()
        else (root / requested).resolve()
    )

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Path is outside the project root.") from exc

    return candidate


def list_dir(
    project_root: str | Path,
    path: str | Path = ".",
) -> dict[str, Any]:
    """List a directory without returning ignored or unbounded results."""
    try:
        root = Path(project_root).expanduser().resolve()
        directory = safe_path(root, path)
        _assert_not_ignored(root, directory)
        if not directory.exists():
            raise FileNotFoundError("Directory does not exist.")
        if not directory.is_dir():
            raise NotADirectoryError("Path is not a directory.")

        entries: list[dict[str, Any]] = []
        truncated = False
        for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            try:
                resolved_entry = safe_path(root, entry)
            except PermissionError:
                continue
            if _is_ignored(root, resolved_entry):
                continue
            if len(entries) >= MAX_DIRECTORY_ENTRIES:
                truncated = True
                break

            kind = "other"
            if entry.is_symlink():
                kind = "symlink"
            elif entry.is_dir():
                kind = "directory"
            elif entry.is_file():
                kind = "file"

            item: dict[str, Any] = {
                "name": entry.name,
                "path": entry.relative_to(root).as_posix(),
                "type": kind,
            }
            if kind == "file":
                item["size"] = entry.stat().st_size
            entries.append(item)

        return {
            "ok": True,
            "path": _display_path(root, directory),
            "entries": entries,
            "truncated": truncated,
        }
    except (OSError, PermissionError, ValueError) as exc:
        return _error(exc)


def read_file(
    project_root: str | Path,
    path: str | Path,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Read a bounded, one-based inclusive line range from a UTF-8 file."""
    return _read_file_range(
        project_root,
        path,
        start_line=start_line,
        end_line=end_line,
        with_line_numbers=False,
    )


def read_file_with_line_numbers(
    project_root: str | Path,
    path: str | Path,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Read a bounded range prefixed with stable one-based line numbers."""
    return _read_file_range(
        project_root,
        path,
        start_line=start_line,
        end_line=end_line,
        with_line_numbers=True,
    )


def _read_file_range(
    project_root: str | Path,
    path: str | Path,
    *,
    start_line: int | None,
    end_line: int | None,
    with_line_numbers: bool,
) -> dict[str, Any]:
    """Implement bounded plain and numbered reads with identical metadata."""
    try:
        first_line = 1 if start_line is None else start_line
        if first_line < 1:
            raise ValueError("start_line must be at least 1.")
        if end_line is not None and end_line < first_line:
            raise ValueError("end_line must be greater than or equal to start_line.")

        root = Path(project_root).expanduser().resolve()
        file_path = safe_path(root, path)
        _assert_not_ignored(root, file_path)
        if not file_path.exists():
            raise FileNotFoundError("File does not exist.")
        if not file_path.is_file():
            raise IsADirectoryError("Path is not a file.")
        instruction_stack = _instruction_stack(root, file_path)

        content: list[str] = []
        character_count = 0
        last_line: int | None = None
        truncated = False

        with file_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line_number < first_line:
                    continue
                if end_line is not None and line_number > end_line:
                    break
                if len(content) >= MAX_FILE_LINES:
                    truncated = True
                    break

                rendered_line = (
                    f"{line_number}: {line}" if with_line_numbers else line
                )
                remaining = MAX_FILE_CHARACTERS - character_count
                if remaining <= 0:
                    truncated = True
                    break
                if len(rendered_line) > remaining:
                    content.append(rendered_line[:remaining])
                    last_line = line_number
                    truncated = True
                    break

                content.append(rendered_line)
                character_count += len(rendered_line)
                last_line = line_number

        result = {
            "ok": True,
            "path": _display_path(root, file_path),
            "content": "".join(content),
            "start_line": first_line,
            "end_line": last_line,
            "truncated": truncated,
            "instruction_stack": instruction_stack,
        }
        if with_line_numbers:
            result["line_numbers"] = True
        return result
    except (OSError, PermissionError, UnicodeError, ValueError) as exc:
        return _error(exc)


def create_dir(
    project_root: str | Path,
    path: str | Path,
) -> dict[str, Any]:
    """Create a directory and any missing parents inside the project root."""
    try:
        root = Path(project_root).expanduser().resolve()
        directory = safe_path(root, path)
        _assert_not_ignored(root, directory)
        if directory.exists() and not directory.is_dir():
            raise FileExistsError("A non-directory already exists at this path.")

        created = not directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        return {
            "ok": True,
            "path": _display_path(root, directory),
            "created": created,
        }
    except (OSError, PermissionError, ValueError) as exc:
        return _error(exc)


def write_file(
    project_root: str | Path,
    path: str | Path,
    content: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a UTF-8 file, refusing existing files unless overwrite is true."""
    try:
        if not isinstance(content, str):
            raise ValueError("content must be a string.")
        if not isinstance(overwrite, bool):
            raise ValueError("overwrite must be a boolean.")

        root = Path(project_root).expanduser().resolve()
        file_path = safe_path(root, path)
        _assert_not_ignored(root, file_path)
        if file_path.exists() and file_path.is_dir():
            raise IsADirectoryError("Path is a directory, not a file.")

        existed = file_path.exists()
        if existed and not overwrite:
            raise FileExistsError("File already exists; set overwrite=true to replace it.")
        instruction_stack = _instruction_stack(root, file_path)

        old_content = file_path.read_text(encoding="utf-8") if existed else ""
        relative_path = _display_path(root, file_path)
        diff, diff_truncated = _build_diff(
            relative_path,
            old_content,
            content,
            existed=existed,
        )

        checkpoint_path: str | None = None
        if existed:
            checkpoint_path = _checkpoint_file(root, file_path)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if overwrite else "x"
        with file_path.open(mode, encoding="utf-8", newline="") as handle:
            handle.write(content)

        return {
            "ok": True,
            "path": relative_path,
            "created": not existed,
            "overwritten": existed,
            "diff": diff,
            "diff_truncated": diff_truncated,
            "checkpoint_path": checkpoint_path,
            "instruction_stack": instruction_stack,
        }
    except (OSError, PermissionError, UnicodeError, ValueError) as exc:
        return _error(exc)


def edit_file(
    project_root: str | Path,
    path: str | Path,
    old_text: str,
    new_text: str,
) -> dict[str, Any]:
    """Replace text only when the exact old text occurs exactly once."""
    try:
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ValueError("old_text and new_text must be strings.")
        if not old_text:
            raise ValueError("old_text must not be empty.")

        root = Path(project_root).expanduser().resolve()
        file_path = safe_path(root, path)
        _assert_not_ignored(root, file_path)
        if not file_path.exists():
            raise FileNotFoundError("File does not exist.")
        if not file_path.is_file():
            raise IsADirectoryError("Path is not a file.")
        instruction_stack = _instruction_stack(root, file_path)

        old_content = file_path.read_text(encoding="utf-8")
        match_count = old_content.count(old_text)
        if match_count == 0:
            raise ValueError("old_text was not found in the file.")
        if match_count > 1:
            raise ValueError(
                f"old_text matched {match_count} times; expected exactly one match."
            )

        new_content = old_content.replace(old_text, new_text, 1)
        relative_path = _display_path(root, file_path)
        diff, diff_truncated = _build_diff(
            relative_path,
            old_content,
            new_content,
            existed=True,
        )
        checkpoint_path = _checkpoint_file(root, file_path)
        file_path.write_text(new_content, encoding="utf-8", newline="")

        return {
            "ok": True,
            "path": relative_path,
            "diff": diff,
            "diff_truncated": diff_truncated,
            "checkpoint_path": checkpoint_path,
            "instruction_stack": instruction_stack,
        }
    except (OSError, PermissionError, UnicodeError, ValueError) as exc:
        return _error(exc)


def replace_lines(
    project_root: str | Path,
    path: str | Path,
    start_line: int,
    end_line: int,
    new_text: str,
) -> dict[str, Any]:
    """Replace a one-based inclusive line range in an existing UTF-8 file."""
    try:
        _validate_line_number(start_line, "start_line", minimum=1)
        _validate_line_number(end_line, "end_line", minimum=1)
        if end_line < start_line:
            raise ValueError("end_line must be greater than or equal to start_line.")
        if not isinstance(new_text, str):
            raise ValueError("new_text must be a string.")

        root, file_path, instruction_stack, old_content = _editable_file(
            project_root,
            path,
        )
        lines = old_content.splitlines(keepends=True)
        if start_line > len(lines) or end_line > len(lines):
            raise ValueError(
                "Line range is outside the file; "
                f"the file has {len(lines)} line(s)."
            )

        newline = _detect_newline(old_content)
        replacement = _normalize_newlines(new_text, newline)
        suffix_exists = end_line < len(lines)
        selected_ended_with_newline = _ends_with_newline(lines[end_line - 1])
        if replacement and not _ends_with_newline(replacement) and (
            suffix_exists or selected_ended_with_newline
        ):
            replacement += newline

        new_content = (
            "".join(lines[: start_line - 1])
            + replacement
            + "".join(lines[end_line:])
        )
        return _write_existing_edit(
            root,
            file_path,
            old_content,
            new_content,
            instruction_stack,
        )
    except (OSError, PermissionError, UnicodeError, ValueError) as exc:
        return _error(exc)


def insert_lines(
    project_root: str | Path,
    path: str | Path,
    after_line: int,
    new_text: str,
) -> dict[str, Any]:
    """Insert text after a one-based line, with zero representing file top."""
    try:
        _validate_line_number(after_line, "after_line", minimum=0)
        if not isinstance(new_text, str):
            raise ValueError("new_text must be a string.")

        root, file_path, instruction_stack, old_content = _editable_file(
            project_root,
            path,
        )
        lines = old_content.splitlines(keepends=True)
        if after_line > len(lines):
            raise ValueError(
                "after_line is outside the file; "
                f"the file has {len(lines)} line(s)."
            )

        newline = _detect_newline(old_content)
        insertion = _normalize_newlines(new_text, newline)
        prefix = "".join(lines[:after_line])
        suffix = "".join(lines[after_line:])
        if insertion:
            if prefix and not _ends_with_newline(prefix):
                prefix += newline
            if suffix and not _ends_with_newline(insertion):
                insertion += newline
            elif (
                not suffix
                and _ends_with_newline(old_content)
                and not _ends_with_newline(insertion)
            ):
                insertion += newline

        new_content = prefix + insertion + suffix
        return _write_existing_edit(
            root,
            file_path,
            old_content,
            new_content,
            instruction_stack,
        )
    except (OSError, PermissionError, UnicodeError, ValueError) as exc:
        return _error(exc)


def _validate_line_number(value: int, name: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")


def _editable_file(
    project_root: str | Path,
    path: str | Path,
) -> tuple[Path, Path, list[dict[str, Any]], str]:
    root = Path(project_root).expanduser().resolve()
    file_path = safe_path(root, path)
    _assert_not_ignored(root, file_path)
    if not file_path.exists():
        raise FileNotFoundError("File does not exist.")
    if not file_path.is_file():
        raise IsADirectoryError("Path is not a file.")
    instruction_stack = _instruction_stack(root, file_path)
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    return root, file_path, instruction_stack, content


def _write_existing_edit(
    root: Path,
    file_path: Path,
    old_content: str,
    new_content: str,
    instruction_stack: list[dict[str, Any]],
) -> dict[str, Any]:
    relative_path = _display_path(root, file_path)
    diff, diff_truncated = _build_diff(
        relative_path,
        old_content,
        new_content,
        existed=True,
    )
    checkpoint_path = _checkpoint_file(root, file_path)
    with file_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(new_content)
    return {
        "ok": True,
        "path": relative_path,
        "diff": diff,
        "diff_truncated": diff_truncated,
        "checkpoint_path": checkpoint_path,
        "instruction_stack": instruction_stack,
    }


def _detect_newline(content: str) -> str:
    for index, character in enumerate(content):
        if character == "\n":
            return "\n"
        if character == "\r":
            return "\r\n" if content[index : index + 2] == "\r\n" else "\r"
    return "\n"


def _normalize_newlines(text: str, newline: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if newline == "\n" else normalized.replace("\n", newline)


def _ends_with_newline(text: str) -> bool:
    return text.endswith(("\n", "\r"))


def project_path(root: str | Path, relative_path: str | Path) -> Path:
    """Compatibility alias for callers that predate ``safe_path``."""
    return safe_path(root, relative_path)


def read_text(root: str | Path, relative_path: str | Path) -> str:
    return project_path(root, relative_path).read_text(encoding="utf-8")


def write_text(root: str | Path, relative_path: str | Path, content: str) -> Path:
    path = project_path(root, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _assert_not_ignored(root: Path, path: Path) -> None:
    if _is_ignored(root, path):
        raise PermissionError("Path is inside an ignored directory.")


def _is_ignored(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return any(part in IGNORED_DIRECTORIES for part in relative.parts)


def _display_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    return relative.as_posix() if relative.parts else "."


def _error(error: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(error)}


def _build_diff(
    relative_path: str,
    old_content: str,
    new_content: str,
    *,
    existed: bool,
) -> tuple[str, bool]:
    from_file = f"a/{relative_path}" if existed else "/dev/null"
    diff = "".join(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=from_file,
            tofile=f"b/{relative_path}",
        )
    )
    if not diff and not existed:
        diff = f"--- /dev/null\n+++ b/{relative_path}\n"
    if len(diff) <= MAX_DIFF_CHARACTERS:
        return diff, False

    marker = "\n[Diff truncated.]"
    return diff[: MAX_DIFF_CHARACTERS - len(marker)] + marker, True


def _checkpoint_file(root: Path, file_path: Path) -> str:
    # Imported lazily so the checkpoint module can reuse this module's
    # canonical safe_path implementation without a module import cycle.
    from lunar_forge.runtime.checkpoints import create_file_checkpoint

    checkpoint_path = create_file_checkpoint(root, file_path)
    return checkpoint_path.relative_to(root).as_posix()


def _instruction_stack(root: Path, file_path: Path) -> list[dict[str, Any]]:
    # Imported lazily because instruction discovery shares this module's
    # canonical safe_path implementation.
    from lunar_forge.instructions import get_instruction_stack_for_path

    return get_instruction_stack_for_path(root, file_path)
