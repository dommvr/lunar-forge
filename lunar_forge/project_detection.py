"""Detect common project languages, frameworks, and commands."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

from lunar_forge.tools.files import IGNORED_DIRECTORIES, safe_path


PACKAGE_JSON_CHARACTER_LIMIT = 1_000_000


class ProjectInfo(TypedDict):
    languages: list[str]
    frameworks: list[str]
    package_manager: str | None
    routing: str | None
    test_command: str | None
    build_command: str | None
    is_empty: bool


def detect_project(project_root: str | Path) -> ProjectInfo:
    """Return JSON-serializable metadata inferred from common project markers."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {root}")

    try:
        package_json_path = safe_path(root, "package.json")
        has_package_json = package_json_path.is_file()
    except PermissionError:
        package_json_path = root / "package.json"
        has_package_json = False
    package_data = _read_package_json(package_json_path) if has_package_json else {}

    has_pyproject = _is_file(root, "pyproject.toml")
    has_requirements = _is_file(root, "requirements.txt")
    has_manage_py = _is_file(root, "manage.py")
    has_app_py = _is_file(root, "app.py")
    has_next_config = _has_config(root, "next.config")
    has_vite_config = _has_config(root, "vite.config")
    has_react_app = _has_react_app(root)
    has_tsconfig = _is_file(root, "tsconfig.json")

    dependencies = _package_dependencies(package_data)
    has_next = has_next_config or "next" in dependencies
    has_vite = has_vite_config or "vite" in dependencies
    has_react = has_react_app or "react" in dependencies or has_next

    languages: list[str] = []
    if has_pyproject or has_requirements or has_manage_py or has_app_py:
        languages.append("python")
    if has_package_json:
        languages.append("javascript")
        if has_tsconfig:
            languages.append("typescript")

    frameworks: list[str] = []
    if has_next:
        frameworks.append("nextjs")
    if has_vite:
        frameworks.append("vite")
    if has_react:
        frameworks.append("react")
    if has_manage_py:
        frameworks.append("django")
    if has_app_py:
        frameworks.append("flask")

    package_manager = _detect_package_manager(root, package_data, has_package_json)
    routing = _detect_routing(root, has_next)
    scripts = _package_scripts(package_data)

    test_command: str | None = None
    if "test" in scripts and package_manager:
        test_command = _package_command(package_manager, "test")
    elif "python" in languages:
        test_command = "pytest"

    build_command: str | None = None
    if "build" in scripts and package_manager:
        build_command = _package_command(package_manager, "build")

    return {
        "languages": languages,
        "frameworks": frameworks,
        "package_manager": package_manager,
        "routing": routing,
        "test_command": test_command,
        "build_command": build_command,
        "is_empty": _is_empty(root),
    }


def detect_project_type(root: str | Path) -> str:
    """Return the legacy single-value project type for existing callers."""
    project = detect_project(root)
    frameworks = project["frameworks"]
    if "nextjs" in frameworks:
        return "nextjs"
    if "vite" in frameworks:
        return "vite"
    if "django" in frameworks:
        return "django"
    if "flask" in frameworks:
        return "flask"
    if project["languages"] == ["python"]:
        return "python"
    if "javascript" in project["languages"]:
        return "node"
    if _is_file(Path(root).expanduser().resolve(), "index.html"):
        return "static_html"
    if project["is_empty"]:
        return "empty"
    return "unknown"


def _read_package_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            content = handle.read(PACKAGE_JSON_CHARACTER_LIMIT + 1)
        if len(content) > PACKAGE_JSON_CHARACTER_LIMIT:
            return {}
        data = json.loads(content)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _package_dependencies(package_data: Mapping[str, Any]) -> set[str]:
    dependencies: set[str] = set()
    for section_name in ("dependencies", "devDependencies"):
        section = package_data.get(section_name, {})
        if isinstance(section, Mapping):
            dependencies.update(str(name) for name in section)
    return dependencies


def _package_scripts(package_data: Mapping[str, Any]) -> Mapping[str, Any]:
    scripts = package_data.get("scripts", {})
    return scripts if isinstance(scripts, Mapping) else {}


def _detect_package_manager(
    root: Path,
    package_data: Mapping[str, Any],
    has_package_json: bool,
) -> str | None:
    if _is_file(root, "pnpm-lock.yaml"):
        return "pnpm"
    if _is_file(root, "yarn.lock"):
        return "yarn"
    if _is_file(root, "package-lock.json"):
        return "npm"

    declared_manager = package_data.get("packageManager")
    if isinstance(declared_manager, str):
        name = declared_manager.partition("@")[0].lower()
        if name in {"pnpm", "yarn", "npm"}:
            return name
    return "npm" if has_package_json else None


def _detect_routing(root: Path, has_next: bool) -> str | None:
    if not has_next:
        return None
    if _is_directory(root, "app"):
        return "app_router"
    if _is_directory(root, "pages"):
        return "pages_router"
    return None


def _package_command(package_manager: str, script: str) -> str:
    if package_manager == "npm":
        return "npm test" if script == "test" else f"npm run {script}"
    return f"{package_manager} {script}"


def _has_config(root: Path, stem: str) -> bool:
    return any(
        _is_file(root, f"{stem}{suffix}")
        for suffix in (".js", ".mjs", ".cjs", ".ts")
    )


def _has_react_app(root: Path) -> bool:
    try:
        src = safe_path(root, "src")
    except PermissionError:
        return False
    if not src.is_dir():
        return False
    for candidate in src.iterdir():
        if not candidate.name.startswith("App."):
            continue
        try:
            if safe_path(root, candidate).is_file():
                return True
        except PermissionError:
            continue
    return False


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


def _is_empty(root: Path) -> bool:
    for entry in root.iterdir():
        if entry.name in IGNORED_DIRECTORIES:
            continue
        try:
            safe_path(root, entry)
        except PermissionError:
            continue
        return False
    return True
