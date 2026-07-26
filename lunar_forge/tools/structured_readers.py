"""Bounded structured and batched file readers confined to a project root."""

from __future__ import annotations

import codecs
import json
import math
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

from lunar_forge.tools.files import IGNORED_DIRECTORIES, safe_path


DEFAULT_STRUCTURED_MAX_BYTES = 64_000
MAX_STRUCTURED_MAX_BYTES = 128_000
DEFAULT_MANY_BYTES_PER_FILE = 16_000
MAX_MANY_BYTES_PER_FILE = 64_000
DEFAULT_MANY_TOTAL_BYTES = 64_000
MAX_MANY_TOTAL_BYTES = 128_000
MAX_MANY_FILES = 20
MAX_TOP_LEVEL_KEYS = 100
MAX_NORMALIZED_NODES = 10_000
MAX_NORMALIZED_DEPTH = 64
MAX_ERROR_CHARACTERS = 500
MAX_PATH_CHARACTERS = 500
MAX_REQUEST_PATH_CHARACTERS = 2_000

_BLOCKED_DIRECTORIES = frozenset(
    {
        *(name.casefold() for name in IGNORED_DIRECTORIES),
        ".cache",
        ".mypy_cache",
        ".nox",
        ".nuxt",
        ".output",
        ".parcel-cache",
        ".pnpm-store",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".turbo",
        "htmlcov",
        "site-packages",
    }
)
_SECRET_DIRECTORIES = frozenset(
    {
        ".aws",
        ".azure",
        ".gnupg",
        ".ssh",
        ".secrets",
        "credentials",
        "secrets",
    }
)
_SECRET_FILENAMES = frozenset(
    {
        ".dockercfg",
        ".envrc",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "auth.json",
        "credentials.json",
        "credentials.toml",
        "credentials.yaml",
        "credentials.yml",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "private-key.json",
        "private_key.json",
        "service-account.json",
        "service_account.json",
        "secrets.json",
        "secrets.toml",
        "secrets.yaml",
        "secrets.yml",
    }
)
_SECRET_SUFFIXES = frozenset(
    {
        ".cer",
        ".cert",
        ".crt",
        ".der",
        ".jks",
        ".key",
        ".kdbx",
        ".keystore",
        ".p12",
        ".pem",
        ".pfx",
    }
)
_ALLOWED_AGENT_CONFIGURATION_PATHS = frozenset(
    {
        (".agent", "config.yaml"),
        (".agent", "config.yml"),
        (".agent", "mcp.yaml"),
        (".agent", "mcp.yml"),
    }
)


def read_json(
    project_root: str | Path,
    path: str | Path,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Parse one bounded JSON file without executing project code."""
    limit = DEFAULT_STRUCTURED_MAX_BYTES if max_bytes is None else max_bytes
    try:
        _validate_limit(limit, "max_bytes", MAX_STRUCTURED_MAX_BYTES)
        _, file_path, relative_path = resolve_safe_readable_file(
            project_root,
            path,
        )
        payload, truncated = _read_bounded_bytes(file_path, limit)
        text = _decode_readable_text(payload, truncated=truncated)
        if truncated:
            return {
                "ok": True,
                "path": relative_path,
                "preview": text,
                "truncated": True,
            }

        def reject_constant(constant: str) -> None:
            position = max(text.find(constant), 0)
            raise json.JSONDecodeError(
                f"Invalid JSON constant {constant}",
                text,
                position,
            )

        data = json.loads(text, parse_constant=reject_constant)
        result = _structured_success(relative_path, data)
        _assert_json_serializable(result)
        return result
    except json.JSONDecodeError as exc:
        return _parse_error(path, "JSON", exc.msg, exc.lineno, exc.colno)
    except (
        OSError,
        PermissionError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return _file_error(path, exc)


def read_yaml(
    project_root: str | Path,
    path: str | Path,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Parse one bounded YAML file using ``yaml.safe_load`` only."""
    limit = DEFAULT_STRUCTURED_MAX_BYTES if max_bytes is None else max_bytes
    try:
        _validate_limit(limit, "max_bytes", MAX_STRUCTURED_MAX_BYTES)
        _, file_path, relative_path = resolve_safe_readable_file(
            project_root,
            path,
        )
        payload, truncated = _read_bounded_bytes(file_path, limit)
        text = _decode_readable_text(payload, truncated=truncated)
        if truncated:
            return {
                "ok": True,
                "path": relative_path,
                "preview": text,
                "truncated": True,
            }

        parsed = yaml.safe_load(text)
        data = _normalize_yaml_value(parsed)
        result = _structured_success(relative_path, data)
        _assert_json_serializable(result)
        return result
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        column = mark.column + 1 if mark is not None else None
        problem = getattr(exc, "problem", None) or str(exc)
        return _parse_error(path, "YAML", problem, line, column)
    except (
        OSError,
        PermissionError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        return _file_error(path, exc)


def read_many_files(
    project_root: str | Path,
    paths: Sequence[str | Path],
    max_bytes_per_file: int | None = None,
    max_total_bytes: int | None = None,
) -> dict[str, Any]:
    """Read a bounded batch of UTF-8 text files with per-file failures."""
    per_file_limit = (
        DEFAULT_MANY_BYTES_PER_FILE
        if max_bytes_per_file is None
        else max_bytes_per_file
    )
    total_limit = (
        DEFAULT_MANY_TOTAL_BYTES
        if max_total_bytes is None
        else max_total_bytes
    )
    try:
        _validate_paths(paths)
        _validate_limit(
            per_file_limit,
            "max_bytes_per_file",
            MAX_MANY_BYTES_PER_FILE,
        )
        _validate_limit(
            total_limit,
            "max_total_bytes",
            MAX_MANY_TOTAL_BYTES,
        )
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "error": _bounded_text(str(exc), MAX_ERROR_CHARACTERS),
        }

    requested_count = len(paths)
    selected_paths = paths[:MAX_MANY_FILES]
    files: list[dict[str, Any]] = []
    total_bytes = 0
    any_content_truncated = False

    for requested_path in selected_paths:
        display_path = _requested_path(requested_path)
        remaining = total_limit - total_bytes
        if remaining <= 0:
            files.append(
                {
                    "path": display_path,
                    "content": "",
                    "line_count": 0,
                    "truncated": True,
                    "error": "Total byte limit reached before reading file.",
                }
            )
            any_content_truncated = True
            continue

        try:
            _, file_path, relative_path = resolve_safe_readable_file(
                project_root,
                requested_path,
            )
            effective_limit = min(per_file_limit, remaining)
            payload, truncated = _read_bounded_bytes(file_path, effective_limit)
            text = _decode_readable_text(payload, truncated=truncated)
            total_bytes += len(payload)
            files.append(
                {
                    "path": relative_path,
                    "content": text,
                    "line_count": _line_count(text),
                    "truncated": truncated,
                }
            )
            any_content_truncated = any_content_truncated or truncated
        except (OSError, PermissionError, TypeError, UnicodeError, ValueError) as exc:
            files.append(
                {
                    "path": display_path,
                    "content": "",
                    "line_count": 0,
                    "truncated": False,
                    "error": _bounded_text(str(exc), MAX_ERROR_CHARACTERS),
                }
            )

    error_count = sum("error" in item for item in files)
    paths_truncated = requested_count > MAX_MANY_FILES
    result = {
        "ok": True,
        "files": files,
        "requested_count": requested_count,
        "returned_count": len(files),
        "success_count": len(files) - error_count,
        "error_count": error_count,
        "total_bytes": total_bytes,
        "max_bytes_per_file": per_file_limit,
        "max_total_bytes": total_limit,
        "paths_truncated": paths_truncated,
        "omitted_count": max(requested_count - len(files), 0),
        "truncated": paths_truncated or any_content_truncated,
    }
    _assert_json_serializable(result)
    return result


def resolve_safe_readable_file(
    project_root: str | Path,
    path: str | Path,
) -> tuple[Path, Path, str]:
    """Resolve one non-secret, non-runtime project file without opening it."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError("Project root does not exist or is not a directory.")
    if not isinstance(path, (str, Path)):
        raise TypeError("path must be a string.")
    rendered_path = str(path)
    if not rendered_path.strip():
        raise ValueError("path must not be empty.")
    if len(rendered_path) > MAX_REQUEST_PATH_CHARACTERS:
        raise ValueError(
            f"path must be at most {MAX_REQUEST_PATH_CHARACTERS} characters."
        )

    requested = Path(rendered_path).expanduser()
    lexical_path = (
        requested if requested.is_absolute() else root / requested
    )
    lexical_path = Path(os.path.abspath(lexical_path))
    try:
        lexical_relative = lexical_path.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Path is outside the project root.") from exc
    _assert_path_allowed(lexical_relative)

    file_path = safe_path(root, path)
    relative_path = file_path.relative_to(root)
    _assert_path_allowed(relative_path)
    if not file_path.exists():
        raise FileNotFoundError("File does not exist.")
    if not file_path.is_file():
        raise IsADirectoryError("Path is not a file.")
    return root, file_path, relative_path.as_posix()


def _assert_path_allowed(relative_path: Path) -> None:
    parts = tuple(part.casefold() for part in relative_path.parts)
    allowed_agent_configuration = parts in _ALLOWED_AGENT_CONFIGURATION_PATHS
    if any(directory in _SECRET_DIRECTORIES for directory in parts[:-1]):
        raise PermissionError(
            "Path is inside a blocked secret or credential directory."
        )
    for directory in parts:
        if (
            directory in _BLOCKED_DIRECTORIES
            and not (
                directory == ".agent"
                and allowed_agent_configuration
            )
        ):
            raise PermissionError(
                "Path is inside a blocked runtime or generated directory."
            )
    if any("pycache" in directory for directory in parts[:-1]):
        raise PermissionError(
            "Path is inside a blocked runtime or generated directory."
        )
    if not parts:
        raise IsADirectoryError("Path is not a file.")

    filename = parts[-1]
    if (
        filename == ".env"
        or filename.startswith(".env.")
        or filename in _SECRET_FILENAMES
        or Path(filename).suffix.casefold() in _SECRET_SUFFIXES
    ):
        raise PermissionError("Path looks like a secret or credential file.")


def _read_bounded_bytes(path: Path, limit: int) -> tuple[bytes, bool]:
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    return payload[:limit], len(payload) > limit


def _decode_text(payload: bytes, *, truncated: bool) -> str:
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    return decoder.decode(payload, final=not truncated)


def _decode_readable_text(payload: bytes, *, truncated: bool) -> str:
    try:
        text = _decode_text(payload, truncated=truncated)
    except UnicodeDecodeError as exc:
        raise ValueError("File is binary or is not valid UTF-8.") from exc
    if _looks_binary(payload, text):
        raise ValueError("File is binary or is not valid UTF-8.")
    return text


def _looks_binary(payload: bytes, text: str) -> bool:
    if b"\x00" in payload:
        return True
    if not text:
        return False
    control_count = sum(
        ord(character) < 32 and character not in "\n\r\t"
        for character in text
    )
    return control_count / len(text) > 0.10


def _structured_success(path: str, data: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "path": path,
        "data": data,
        "truncated": False,
    }
    if isinstance(data, Mapping):
        keys = list(data)[:MAX_TOP_LEVEL_KEYS]
        result["top_level_keys"] = keys
        result["top_level_keys_truncated"] = len(data) > len(keys)
    return result


def _normalize_yaml_value(value: Any) -> Any:
    state = {"nodes": 0}
    return _normalize_yaml_node(value, depth=0, active=set(), state=state)


def _normalize_yaml_node(
    value: Any,
    *,
    depth: int,
    active: set[int],
    state: dict[str, int],
) -> Any:
    state["nodes"] += 1
    if state["nodes"] > MAX_NORMALIZED_NODES:
        raise ValueError("YAML data exceeds the structured node limit.")
    if depth > MAX_NORMALIZED_DEPTH:
        raise ValueError("YAML data exceeds the structured depth limit.")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("YAML contains a non-finite number.")
        return value
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("YAML contains a recursive structure.")
        active.add(identity)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                normalized_key = _normalize_yaml_key(key)
                if normalized_key in normalized:
                    raise ValueError(
                        "YAML mapping keys collide after JSON normalization."
                    )
                normalized[normalized_key] = _normalize_yaml_node(
                    item,
                    depth=depth + 1,
                    active=active,
                    state=state,
                )
            return normalized
        finally:
            active.remove(identity)

    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in active:
            raise ValueError("YAML contains a recursive structure.")
        active.add(identity)
        try:
            normalized_items = [
                _normalize_yaml_node(
                    item,
                    depth=depth + 1,
                    active=active,
                    state=state,
                )
                for item in value
            ]
            if isinstance(value, (set, frozenset)):
                normalized_items.sort(
                    key=lambda item: json.dumps(
                        item,
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
            return normalized_items
        finally:
            active.remove(identity)

    raise ValueError(
        f"YAML contains unsupported value type: {type(value).__name__}."
    )


def _normalize_yaml_key(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    raise ValueError("YAML mapping keys must be JSON-compatible scalars.")


def _validate_paths(paths: Sequence[str | Path]) -> None:
    if isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence):
        raise TypeError("paths must be a list of project-relative paths.")
    if not paths:
        raise ValueError("paths must contain at least one path.")


def _validate_limit(value: int, name: str, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer.")
    if value < 1:
        raise ValueError(f"{name} must be at least 1.")
    if value > maximum:
        raise ValueError(f"{name} must be at most {maximum}.")


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _parse_error(
    path: str | Path,
    format_name: str,
    message: str,
    line: int | None,
    column: int | None,
) -> dict[str, Any]:
    location = (
        f" at line {line}, column {column}"
        if line is not None and column is not None
        else ""
    )
    result: dict[str, Any] = {
        "ok": False,
        "path": _requested_path(path),
        "error": _bounded_text(
            f"Invalid {format_name}{location}: {message}",
            MAX_ERROR_CHARACTERS,
        ),
        "truncated": False,
    }
    if line is not None:
        result["line"] = line
    if column is not None:
        result["column"] = column
    return result


def _file_error(path: str | Path, error: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "path": _requested_path(path),
        "error": _bounded_text(str(error), MAX_ERROR_CHARACTERS),
        "truncated": False,
    }


def _requested_path(path: object) -> str:
    return _bounded_text(str(path), MAX_PATH_CHARACTERS)


def _bounded_text(text: str, maximum: int) -> str:
    return text if len(text) <= maximum else f"{text[: maximum - 3]}..."


def _assert_json_serializable(value: Any) -> None:
    json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )


__all__ = [
    "DEFAULT_MANY_BYTES_PER_FILE",
    "DEFAULT_MANY_TOTAL_BYTES",
    "DEFAULT_STRUCTURED_MAX_BYTES",
    "MAX_MANY_BYTES_PER_FILE",
    "MAX_MANY_FILES",
    "MAX_MANY_TOTAL_BYTES",
    "MAX_REQUEST_PATH_CHARACTERS",
    "MAX_STRUCTURED_MAX_BYTES",
    "read_json",
    "read_many_files",
    "read_yaml",
    "resolve_safe_readable_file",
]
