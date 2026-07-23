"""Bounded, static dependency and script inspection."""

from __future__ import annotations

import ast
import configparser
import json
import re
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from lunar_forge.tools.files import safe_path


MAX_MANIFEST_CHARACTERS = 1_000_000
MAX_REQUIREMENT_LINES = 2_000
MAX_DEPENDENCY_ITEMS = 50
MAX_SCRIPT_ITEMS = 30
MAX_SCRIPT_CHARACTERS = 300
MAX_SPECIFIER_CHARACTERS = 300
MAX_WARNINGS = 10

_LOCKFILES = (
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "bun.lock",
    "bun.lockb",
    "uv.lock",
    "Pipfile.lock",
    "poetry.lock",
)
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*"
    r"\s*=\s*)([^\s]+)"
)
_API_KEY = re.compile(
    r"(?i)\b(?:sk-(?:ant-)?|gh[pousr]_|github_pat_)[a-z0-9_-]{8,}\b"
)
_REQUIREMENT_NAME = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?\s*(.*)$"
)


def dependency_summary(project_root: str | Path) -> dict[str, Any]:
    """Summarize dependency manifests without installing or executing anything."""
    try:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {root}"
            )

        manifests: list[str] = []
        warnings: list[str] = []
        oversized_files: list[str] = []
        scripts: dict[str, str] = {}
        python_scripts: dict[str, str] = {}
        node_dependencies: list[dict[str, str]] = []
        node_dev_dependencies: list[dict[str, str]] = []
        python_dependencies: list[dict[str, str]] = []
        package_data: dict[str, Any] = {}
        pyproject_data: dict[str, Any] = {}

        package_path = _existing_file(root, "package.json")
        if package_path is not None:
            manifests.append("package.json")
            package_data = _load_json_manifest(
                package_path,
                "package.json",
                warnings,
                oversized_files,
            )
            scripts = _package_scripts(package_data)
            node_dependencies = _mapping_dependencies(
                package_data.get("dependencies"),
                source="package.json",
            )
            node_dev_dependencies = _mapping_dependencies(
                package_data.get("devDependencies"),
                source="package.json",
            )

        pyproject_path = _existing_file(root, "pyproject.toml")
        if pyproject_path is not None:
            manifests.append("pyproject.toml")
            pyproject_data = _load_toml_manifest(
                pyproject_path,
                "pyproject.toml",
                warnings,
                oversized_files,
            )
            _collect_pyproject_dependencies(
                pyproject_data,
                python_dependencies,
            )
            _collect_pyproject_scripts(pyproject_data, python_scripts)

        requirements_path = _existing_file(root, "requirements.txt")
        if requirements_path is not None:
            manifests.append("requirements.txt")
            _collect_requirements_file(
                requirements_path,
                "requirements.txt",
                python_dependencies,
                warnings,
                oversized_files,
            )

        setup_cfg_path = _existing_file(root, "setup.cfg")
        if setup_cfg_path is not None:
            manifests.append("setup.cfg")
            _collect_setup_cfg(
                setup_cfg_path,
                python_dependencies,
                python_scripts,
                warnings,
                oversized_files,
            )

        setup_py_path = _existing_file(root, "setup.py")
        if setup_py_path is not None:
            manifests.append("setup.py")
            _collect_setup_py(
                setup_py_path,
                python_dependencies,
                python_scripts,
                warnings,
                oversized_files,
            )

        lockfiles = [
            name for name in _LOCKFILES if _existing_file(root, name) is not None
        ]
        node_manager = _node_package_manager(root, package_data)
        python_manager = _python_package_manager(root, pyproject_data, manifests)
        package_manager_hints = _stable_unique(
            item for item in (node_manager, python_manager) if item is not None
        )

        scripts_items, scripts_truncated = _bounded_mapping(scripts, MAX_SCRIPT_ITEMS)
        python_scripts_items, python_scripts_truncated = _bounded_mapping(
            python_scripts,
            MAX_SCRIPT_ITEMS,
        )
        dependencies_items, dependencies_truncated = _bounded_items(
            node_dependencies,
            MAX_DEPENDENCY_ITEMS,
        )
        dev_items, dev_truncated = _bounded_items(
            node_dev_dependencies,
            MAX_DEPENDENCY_ITEMS,
        )
        python_items, python_truncated = _bounded_items(
            _deduplicate_dependencies(python_dependencies),
            MAX_DEPENDENCY_ITEMS,
        )
        framework_hints = _framework_hints(
            dependencies=node_dependencies,
            dev_dependencies=node_dev_dependencies,
            python_dependencies=python_dependencies,
            root=root,
        )
        likely_commands = _likely_commands(
            root=root,
            node_manager=node_manager,
            scripts=scripts,
            python_manifests=any(
                name in manifests
                for name in (
                    "pyproject.toml",
                    "requirements.txt",
                    "setup.cfg",
                    "setup.py",
                )
            ),
            python_dependencies=python_dependencies,
            pyproject_data=pyproject_data,
            framework_hints=framework_hints,
        )

        truncation = {
            "scripts": scripts_truncated,
            "python_scripts": python_scripts_truncated,
            "dependencies": dependencies_truncated,
            "dev_dependencies": dev_truncated,
            "python_dependencies": python_truncated,
            "oversized_manifests": sorted(oversized_files),
        }
        return {
            "ok": True,
            "package_manager": (
                node_manager
                or python_manager
            ),
            "package_manager_hints": package_manager_hints,
            "manifests": manifests,
            "lockfiles": lockfiles,
            "scripts": scripts_items,
            "python_scripts": python_scripts_items,
            "dependencies": dependencies_items,
            "dev_dependencies": dev_items,
            "python_dependencies": python_items,
            "framework_hints": framework_hints,
            "likely_commands": likely_commands,
            "warnings": warnings[:MAX_WARNINGS],
            "truncated": (
                any(
                    (
                        scripts_truncated,
                        python_scripts_truncated,
                        dependencies_truncated,
                        dev_truncated,
                        python_truncated,
                    )
                )
                or bool(oversized_files)
                or len(warnings) > MAX_WARNINGS
            ),
            "truncation": truncation,
        }
    except (OSError, PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _load_json_manifest(
    path: Path,
    display_name: str,
    warnings: list[str],
    oversized_files: list[str],
) -> dict[str, Any]:
    content, truncated = _read_bounded(path)
    if truncated:
        oversized_files.append(display_name)
        _warn(warnings, f"{display_name} exceeded the static parsing limit.")
        return {}
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, RecursionError):
        _warn(warnings, f"{display_name} is not valid JSON.")
        return {}
    if not isinstance(data, dict):
        _warn(warnings, f"{display_name} does not contain a JSON object.")
        return {}
    return data


def _load_toml_manifest(
    path: Path,
    display_name: str,
    warnings: list[str],
    oversized_files: list[str],
) -> dict[str, Any]:
    content, truncated = _read_bounded(path)
    if truncated:
        oversized_files.append(display_name)
        _warn(warnings, f"{display_name} exceeded the static parsing limit.")
        return {}
    try:
        data = tomllib.loads(content)
    except (tomllib.TOMLDecodeError, RecursionError):
        _warn(warnings, f"{display_name} is not valid TOML.")
        return {}
    return data if isinstance(data, dict) else {}


def _package_scripts(package_data: Mapping[str, Any]) -> dict[str, str]:
    section = package_data.get("scripts")
    if not isinstance(section, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_name, raw_command in section.items():
        if not isinstance(raw_name, str) or not isinstance(raw_command, str):
            continue
        name = raw_name[:100]
        result[name] = _sanitize(raw_command)[:MAX_SCRIPT_CHARACTERS]
    return result


def _mapping_dependencies(
    section: Any,
    *,
    source: str,
) -> list[dict[str, str]]:
    if not isinstance(section, Mapping):
        return []
    result: list[dict[str, str]] = []
    for raw_name, raw_specifier in sorted(
        section.items(),
        key=lambda item: str(item[0]).casefold(),
    ):
        if not isinstance(raw_name, str):
            continue
        result.append(
            {
                "name": raw_name[:200],
                "specifier": _specifier(raw_specifier),
                "source": source,
            }
        )
    return result


def _collect_pyproject_dependencies(
    data: Mapping[str, Any],
    output: list[dict[str, str]],
) -> None:
    project = _mapping(data.get("project"))
    _add_requirement_values(
        output,
        project.get("dependencies"),
        "pyproject.toml:project",
    )
    optional = _mapping(project.get("optional-dependencies"))
    for group, values in sorted(
        optional.items(),
        key=lambda item: str(item[0]).casefold(),
    ):
        _add_requirement_values(
            output,
            values,
            f"pyproject.toml:optional:{str(group)[:100]}",
        )

    tool = _mapping(data.get("tool"))
    poetry = _mapping(tool.get("poetry"))
    for name, specifier in _mapping(poetry.get("dependencies")).items():
        if str(name).casefold() == "python":
            continue
        output.append(
            {
                "name": str(name)[:200],
                "specifier": _specifier(specifier),
                "source": "pyproject.toml:poetry",
            }
        )
    poetry_groups = _mapping(poetry.get("group"))
    for group, group_data in poetry_groups.items():
        dependencies = _mapping(_mapping(group_data).get("dependencies"))
        for name, specifier in dependencies.items():
            output.append(
                {
                    "name": str(name)[:200],
                    "specifier": _specifier(specifier),
                    "source": f"pyproject.toml:poetry:{str(group)[:100]}",
                }
            )


def _collect_pyproject_scripts(
    data: Mapping[str, Any],
    output: dict[str, str],
) -> None:
    project = _mapping(data.get("project"))
    _add_script_mapping(output, project.get("scripts"))
    tool = _mapping(data.get("tool"))
    poetry = _mapping(tool.get("poetry"))
    _add_script_mapping(output, poetry.get("scripts"))


def _collect_requirements_file(
    path: Path,
    display_name: str,
    output: list[dict[str, str]],
    warnings: list[str],
    oversized_files: list[str],
) -> None:
    content, truncated = _read_bounded(path)
    if truncated:
        oversized_files.append(display_name)
    lines = content.splitlines()
    if len(lines) > MAX_REQUIREMENT_LINES:
        lines = lines[:MAX_REQUIREMENT_LINES]
        truncated = True
    if truncated:
        _warn(warnings, f"{display_name} was parsed only to its bounded limit.")
    for line in lines:
        value = line.strip()
        if not value or value.startswith(("#", "-")):
            continue
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        item = _requirement_item(value, display_name)
        if item is not None:
            output.append(item)


def _collect_setup_cfg(
    path: Path,
    output: list[dict[str, str]],
    scripts: dict[str, str],
    warnings: list[str],
    oversized_files: list[str],
) -> None:
    content, truncated = _read_bounded(path)
    if truncated:
        oversized_files.append("setup.cfg")
        _warn(warnings, "setup.cfg exceeded the static parsing limit.")
        return
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(content)
    except configparser.Error:
        _warn(warnings, "setup.cfg could not be parsed.")
        return
    if parser.has_option("options", "install_requires"):
        _add_requirement_values(
            output,
            parser.get("options", "install_requires").splitlines(),
            "setup.cfg:install_requires",
        )
    if parser.has_section("options.extras_require"):
        for group, value in parser.items("options.extras_require"):
            _add_requirement_values(
                output,
                value.splitlines(),
                f"setup.cfg:extra:{group[:100]}",
            )
    if parser.has_option("options.entry_points", "console_scripts"):
        _add_entry_point_lines(
            scripts,
            parser.get("options.entry_points", "console_scripts").splitlines(),
        )


def _collect_setup_py(
    path: Path,
    output: list[dict[str, str]],
    scripts: dict[str, str],
    warnings: list[str],
    oversized_files: list[str],
) -> None:
    content, truncated = _read_bounded(path)
    if truncated:
        oversized_files.append("setup.py")
        _warn(warnings, "setup.py exceeded the static parsing limit.")
        return
    try:
        tree = ast.parse(content, filename="setup.py")
    except (SyntaxError, RecursionError):
        _warn(warnings, "setup.py could not be parsed statically.")
        return
    found_setup = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_setup_call(node.func):
            continue
        found_setup = True
        for keyword in node.keywords:
            if keyword.arg not in {
                "install_requires",
                "extras_require",
                "entry_points",
            }:
                continue
            try:
                value = ast.literal_eval(keyword.value)
            except (ValueError, TypeError, RecursionError):
                _warn(
                    warnings,
                    f"setup.py {keyword.arg} is dynamic and was not executed.",
                )
                continue
            if keyword.arg == "install_requires":
                _add_requirement_values(
                    output,
                    value,
                    "setup.py:install_requires",
                )
            elif isinstance(value, Mapping):
                if keyword.arg == "extras_require":
                    for group, requirements in value.items():
                        _add_requirement_values(
                            output,
                            requirements,
                            f"setup.py:extra:{str(group)[:100]}",
                        )
                elif keyword.arg == "entry_points":
                    _add_entry_point_lines(
                        scripts,
                        value.get("console_scripts"),
                    )
    if not found_setup:
        _warn(warnings, "setup.py has no statically recognizable setup() call.")


def _add_requirement_values(
    output: list[dict[str, str]],
    values: Any,
    source: str,
) -> None:
    if isinstance(values, str):
        candidates: Iterable[Any] = values.splitlines()
    elif isinstance(values, (list, tuple)):
        candidates = values
    else:
        return
    for raw_value in candidates:
        if not isinstance(raw_value, str):
            continue
        value = raw_value.strip()
        if not value or value.startswith(("#", "-")):
            continue
        item = _requirement_item(value, source)
        if item is not None:
            output.append(item)


def _add_script_mapping(output: dict[str, str], values: Any) -> None:
    if not isinstance(values, Mapping):
        return
    for raw_name, raw_target in values.items():
        if not isinstance(raw_name, str):
            continue
        output[raw_name[:100]] = _specifier(raw_target)


def _add_entry_point_lines(output: dict[str, str], values: Any) -> None:
    if isinstance(values, str):
        candidates: Iterable[Any] = values.splitlines()
    elif isinstance(values, (list, tuple)):
        candidates = values
    else:
        return
    for raw_value in candidates:
        if not isinstance(raw_value, str):
            continue
        name, separator, target = raw_value.partition("=")
        if not separator or not name.strip() or not target.strip():
            continue
        output[name.strip()[:100]] = _sanitize(target.strip())[
            :MAX_SCRIPT_CHARACTERS
        ]


def _requirement_item(value: str, source: str) -> dict[str, str] | None:
    sanitized = _sanitize(value)[:500]
    match = _REQUIREMENT_NAME.match(sanitized)
    if match is None:
        return None
    name, specifier = match.groups()
    return {
        "name": name[:200],
        "specifier": specifier.strip()[:MAX_SPECIFIER_CHARACTERS],
        "source": source[:200],
    }


def _framework_hints(
    *,
    dependencies: list[dict[str, str]],
    dev_dependencies: list[dict[str, str]],
    python_dependencies: list[dict[str, str]],
    root: Path,
) -> list[str]:
    node_names = {
        item["name"].casefold() for item in (*dependencies, *dev_dependencies)
    }
    python_names = {
        item["name"].replace("_", "-").casefold()
        for item in python_dependencies
    }
    hints: list[str] = []
    for dependency, framework in (
        ("next", "nextjs"),
        ("vite", "vite"),
        ("react", "react"),
        ("vue", "vue"),
        ("svelte", "svelte"),
        ("express", "express"),
    ):
        if dependency in node_names:
            hints.append(framework)
    for dependency, framework in (
        ("django", "django"),
        ("flask", "flask"),
        ("fastapi", "fastapi"),
    ):
        if dependency in python_names:
            hints.append(framework)
    if _existing_file(root, "manage.py") is not None and "django" not in hints:
        hints.append("django")
    return hints


def _likely_commands(
    *,
    root: Path,
    node_manager: str | None,
    scripts: Mapping[str, str],
    python_manifests: bool,
    python_dependencies: list[dict[str, str]],
    pyproject_data: Mapping[str, Any],
    framework_hints: list[str],
) -> dict[str, list[str]]:
    validation: list[str] = []
    development: list[str] = []
    build: list[str] = []
    if node_manager is not None:
        for name in ("test", "lint", "typecheck", "check", "build"):
            if name in scripts:
                validation.append(_script_command(node_manager, name))
        for name in ("dev", "start"):
            if name in scripts:
                development.append(_script_command(node_manager, name))
        if "build" in scripts:
            build.append(_script_command(node_manager, "build"))

    if python_manifests:
        python_names = {
            item["name"].replace("_", "-").casefold()
            for item in python_dependencies
        }
        tool = _mapping(pyproject_data.get("tool"))
        has_tests = any(
            _existing_directory(root, path)
            for path in ("tests", "test")
        )
        if has_tests or "pytest" in python_names or "pytest" in tool:
            validation.append("python -m pytest")
        if "ruff" in python_names or "ruff" in tool:
            validation.append("ruff check .")
        if "mypy" in python_names or "mypy" in tool:
            validation.append("mypy .")
        validation.append("python -B -m compileall .")

    if "django" in framework_hints and _existing_file(root, "manage.py") is not None:
        development.append("python manage.py runserver")
    if "flask" in framework_hints and _existing_file(root, "app.py") is not None:
        development.append("flask --app app run")
    if "fastapi" in framework_hints:
        module = "app" if _existing_file(root, "app.py") is not None else "main"
        development.append(f"uvicorn {module}:app --reload")

    return {
        "validation": _stable_unique(validation)[:20],
        "dev": _stable_unique(development)[:10],
        "build": _stable_unique(build)[:10],
    }


def _node_package_manager(
    root: Path,
    package_data: Mapping[str, Any],
) -> str | None:
    for filename, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lock", "bun"),
        ("bun.lockb", "bun"),
        ("package-lock.json", "npm"),
    ):
        if _existing_file(root, filename) is not None:
            return manager
    declared = package_data.get("packageManager")
    if isinstance(declared, str):
        manager = declared.partition("@")[0].casefold()
        if manager in {"npm", "pnpm", "yarn", "bun"}:
            return manager
    return "npm" if _existing_file(root, "package.json") is not None else None


def _python_package_manager(
    root: Path,
    pyproject_data: Mapping[str, Any],
    manifests: list[str],
) -> str | None:
    if _existing_file(root, "uv.lock") is not None:
        return "uv"
    tool = _mapping(pyproject_data.get("tool"))
    if "poetry" in tool or _existing_file(root, "poetry.lock") is not None:
        return "poetry"
    if _existing_file(root, "Pipfile") is not None:
        return "pipenv"
    if any(
        item in manifests
        for item in ("pyproject.toml", "requirements.txt", "setup.cfg", "setup.py")
    ):
        return "pip"
    return None


def _script_command(manager: str, name: str) -> str:
    if manager == "npm":
        return "npm test" if name == "test" else f"npm run {name}"
    return f"{manager} {name}"


def _specifier(value: Any) -> str:
    if isinstance(value, str):
        rendered = value
    elif isinstance(value, Mapping):
        selected = {
            str(key): item
            for key, item in value.items()
            if str(key) in {"version", "path", "git", "url", "branch", "tag"}
        }
        try:
            rendered = json.dumps(
                selected,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            rendered = "[complex specifier]"
    else:
        rendered = str(value)
    return _sanitize(rendered)[:MAX_SPECIFIER_CHARACTERS]


def _sanitize(value: str) -> str:
    sanitized = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    sanitized = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", sanitized)
    return _API_KEY.sub("[REDACTED]", sanitized)


def _bounded_mapping(
    values: Mapping[str, str],
    limit: int,
) -> tuple[dict[str, str], bool]:
    items = sorted(values.items(), key=lambda item: item[0].casefold())
    return dict(items[:limit]), len(items) > limit


def _bounded_items(
    values: list[dict[str, str]],
    limit: int,
) -> tuple[list[dict[str, str]], bool]:
    sorted_values = sorted(
        values,
        key=lambda item: (
            item.get("name", "").casefold(),
            item.get("source", "").casefold(),
            item.get("specifier", ""),
        ),
    )
    return sorted_values[:limit], len(sorted_values) > limit


def _deduplicate_dependencies(
    values: list[dict[str, str]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in values:
        key = (
            item["name"].casefold(),
            item["specifier"],
            item["source"],
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _read_bounded(path: Path) -> tuple[str, bool]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.read(MAX_MANIFEST_CHARACTERS + 1)
    return content[:MAX_MANIFEST_CHARACTERS], len(content) > MAX_MANIFEST_CHARACTERS


def _existing_file(root: Path, relative_path: str) -> Path | None:
    try:
        candidate = safe_path(root, relative_path)
    except PermissionError:
        return None
    return candidate if candidate.is_file() and not candidate.is_symlink() else None


def _existing_directory(root: Path, relative_path: str) -> bool:
    try:
        candidate = safe_path(root, relative_path)
    except PermissionError:
        return False
    return candidate.is_dir() and not candidate.is_symlink()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_setup_call(function: ast.expr) -> bool:
    if isinstance(function, ast.Name):
        return function.id == "setup"
    return isinstance(function, ast.Attribute) and function.attr == "setup"


def _warn(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _stable_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = ["dependency_summary"]
