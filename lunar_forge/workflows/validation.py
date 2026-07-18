"""Project-aware local validation workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lunar_forge.project_detection import ProjectInfo, detect_project
from lunar_forge.runtime.local_runner import DEFAULT_TIMEOUT_MS, run_local_command
from lunar_forge.tools.files import safe_path


PACKAGE_JSON_CHARACTER_LIMIT = 1_000_000
PYTEST_CONFIG_CHARACTER_LIMIT = 200_000


@dataclass(frozen=True)
class WorkflowResult:
    """Compatibility result type retained for earlier workflow callers."""

    ok: bool
    message: str = ""


def run_validation(
    project_root: str | Path,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    """Detect and run likely project validation commands in a bounded runner."""
    root = Path(project_root).expanduser().resolve()
    try:
        project_info = detect_project(root)
        commands = _select_validation_commands(root, project_info)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "message": "Validation command detection failed.",
            "commands": [],
            "results": [],
            "error": str(exc),
        }

    if not commands:
        return {
            "ok": True,
            "message": "No validation commands were found for this project.",
            "commands": [],
            "results": [],
        }

    results = [
        run_local_command(root, command, timeout_ms)
        for command in commands
    ]
    all_succeeded = all(result.get("ok") is True for result in results)
    return {
        "ok": all_succeeded,
        "message": (
            "All validation commands passed."
            if all_succeeded
            else "One or more validation commands failed."
        ),
        "commands": commands,
        "results": results,
    }


def _select_validation_commands(
    root: Path,
    project_info: ProjectInfo,
) -> list[str]:
    commands: list[str] = []
    languages = project_info["languages"]

    if "python" in languages:
        commands.append("python -m compileall .")
        if _has_pytest_tests_or_config(root):
            commands.append("pytest")

    if "javascript" in languages:
        package_manager = project_info["package_manager"]
        scripts = _read_package_scripts(root)
        if package_manager in {"npm", "pnpm", "yarn"}:
            for script in ("test", "lint", "build"):
                if script in scripts:
                    commands.append(_package_script_command(package_manager, script))

    return commands


def _has_pytest_tests_or_config(root: Path) -> bool:
    if _is_directory(root, "tests"):
        return True
    if _has_root_test_file(root):
        return True
    if _is_file(root, "pytest.ini") or _is_file(root, ".pytest.ini"):
        return True
    return any(
        _file_contains(root, path, section)
        for path, section in (
            ("pyproject.toml", "[tool.pytest.ini_options]"),
            ("tox.ini", "[pytest]"),
            ("setup.cfg", "[tool:pytest]"),
        )
    )


def _has_root_test_file(root: Path) -> bool:
    try:
        candidates = root.iterdir()
    except OSError:
        return False
    for candidate in candidates:
        if not candidate.is_file():
            continue
        if candidate.name.startswith("test_") and candidate.suffix == ".py":
            return True
        if candidate.name.endswith("_test.py"):
            return True
    return False


def _read_package_scripts(root: Path) -> set[str]:
    try:
        path = safe_path(root, "package.json")
        with path.open("r", encoding="utf-8") as handle:
            content = handle.read(PACKAGE_JSON_CHARACTER_LIMIT + 1)
        if len(content) > PACKAGE_JSON_CHARACTER_LIMIT:
            return set()
        data = json.loads(content)
    except (OSError, UnicodeError, json.JSONDecodeError, PermissionError):
        return set()
    if not isinstance(data, dict):
        return set()
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return set()
    return {
        name
        for name, command in scripts.items()
        if isinstance(name, str) and isinstance(command, str)
    }


def _package_script_command(package_manager: str, script: str) -> str:
    if package_manager == "npm":
        return "npm test" if script == "test" else f"npm run {script}"
    return f"{package_manager} {script}"


def _file_contains(root: Path, relative_path: str, marker: str) -> bool:
    try:
        path = safe_path(root, relative_path)
        with path.open("r", encoding="utf-8") as handle:
            content = handle.read(PYTEST_CONFIG_CHARACTER_LIMIT + 1)
    except (OSError, UnicodeError, PermissionError):
        return False
    return len(content) <= PYTEST_CONFIG_CHARACTER_LIMIT and marker in content


def _is_file(root: Path, relative_path: str) -> bool:
    try:
        return safe_path(root, relative_path).is_file()
    except PermissionError:
        return False


def _is_directory(root: Path, relative_path: str) -> bool:
    try:
        return safe_path(root, relative_path).is_dir()
    except PermissionError:
        return False
