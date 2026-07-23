"""Compact, read-only project readiness inspection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from lunar_forge.runtime.local_runner import resolve_executable
from lunar_forge.tools.dependencies import dependency_summary
from lunar_forge.tools.files import IGNORED_DIRECTORIES, safe_path


MAX_SCAN_DIRECTORIES = 2_000
MAX_RUNTIME_PATHS = 30
MAX_CI_PATHS = 20
MAX_TRACKED_OUTPUT_BYTES = 250_000
MAX_SUSPICIOUS_TRACKED_PATHS = 30
MAX_TEST_CONFIG_CHARACTERS = 200_000
GIT_INSPECTION_TIMEOUT_SECONDS = 5

_README_NAMES = ("README.md", "README.rst", "README.txt", "README")
_AGENTS_NAMES = ("AGENTS.md", "agents.md")
_TEST_DIRECTORIES = ("tests", "test")
_TEST_CONFIG_NAMES = (
    "pytest.ini",
    "tox.ini",
    "noxfile.py",
    ".coveragerc",
    "jest.config.js",
    "jest.config.cjs",
    "jest.config.mjs",
    "jest.config.ts",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.ts",
    "playwright.config.js",
    "playwright.config.ts",
)
_PACKAGE_MARKERS = (
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
)
_RUNTIME_DIRECTORY_NAMES = frozenset(
    {
        ".agent",
        ".mypy_cache",
        ".next",
        ".nox",
        ".nuxt",
        ".output",
        ".parcel-cache",
        ".pnpm-store",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".turbo",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "venv",
    }
)
_SECRET_FILENAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
        "secrets.toml",
        "secrets.yaml",
        "secrets.yml",
    }
)
_SECRET_SUFFIXES = frozenset(
    {".jks", ".kdbx", ".key", ".p12", ".pem", ".pfx"}
)


def project_health(
    project_root: str | Path,
    *,
    allow_git: bool = True,
) -> dict[str, Any]:
    """Return bounded readiness signals without executing project code."""
    try:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {root}"
            )

        readme_path = _first_file(root, _README_NAMES)
        root_agents_path = _first_file(root, _AGENTS_NAMES)
        scan = _scan_project_tree(root)
        test_directories = [
            name for name in _TEST_DIRECTORIES if _is_directory(root, name)
        ]
        test_configs = [
            name for name in _TEST_CONFIG_NAMES if _is_file(root, name)
        ]
        if _pyproject_has_test_config(root):
            test_configs.append("pyproject.toml")
        package_markers = [
            name for name in _PACKAGE_MARKERS if _is_file(root, name)
        ]
        dependencies = dependency_summary(root)
        scripts = dependencies.get("scripts", {})
        likely_commands = dependencies.get("likely_commands", {})
        if (
            isinstance(scripts, dict)
            and "test" in scripts
            and "package.json" not in test_configs
        ):
            test_configs.append("package.json")
        validation_commands = (
            list(likely_commands.get("validation", []))
            if isinstance(likely_commands, dict)
            and isinstance(likely_commands.get("validation"), list)
            else []
        )
        ci_paths, ci_truncated = _ci_paths(root)
        if allow_git:
            tracked_check = _tracked_suspicious_paths(root)
        else:
            tracked_check = {
                "status": "skipped_no_command",
                "paths": [],
                "truncated": False,
            }
        suspicious_tracked = tracked_check["paths"]

        missing: list[str] = []
        if readme_path is None:
            missing.append("README")
        if root_agents_path is None:
            missing.append("AGENTS.md")
        if not test_directories and not test_configs:
            missing.append("tests")
        if not _is_file(root, ".gitignore"):
            missing.append(".gitignore")
        if not validation_commands:
            missing.append("validation command hints")
        if not package_markers:
            missing.append("package markers")

        is_empty = _is_effectively_empty(root)
        status = (
            "empty"
            if is_empty
            else "needs_attention"
            if missing or suspicious_tracked
            else "ready"
        )
        return {
            "ok": True,
            "status": status,
            "checks": {
                "readme": {
                    "present": readme_path is not None,
                    "path": readme_path,
                },
                "agents": {
                    "present": root_agents_path is not None,
                    "path": root_agents_path,
                    "nested_count": scan["nested_agents_count"],
                    "nested_count_truncated": scan["scan_truncated"],
                },
                "tests": {
                    "present": bool(test_directories or test_configs),
                    "directories": test_directories,
                    "configs": sorted(test_configs),
                },
                "gitignore": {"present": _is_file(root, ".gitignore")},
                "ci": {
                    "present": bool(ci_paths),
                    "paths": ci_paths,
                    "truncated": ci_truncated,
                },
            },
            "package_markers": package_markers,
            "validation_commands": validation_commands[:20],
            "generated_runtime_paths": scan["runtime_paths"],
            "suspicious_tracked_paths": suspicious_tracked,
            "tracked_path_check": tracked_check["status"],
            "missing": missing,
            "truncated": bool(
                scan["scan_truncated"]
                or scan["runtime_paths_truncated"]
                or ci_truncated
                or tracked_check["truncated"]
                or dependencies.get("truncated") is True
            ),
        }
    except (OSError, PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _scan_project_tree(root: Path) -> dict[str, Any]:
    runtime_paths: list[str] = []
    nested_agents_count = 0
    visited = 0
    scan_truncated = False
    runtime_paths_truncated = False

    for current_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        visited += 1
        if visited > MAX_SCAN_DIRECTORIES:
            scan_truncated = True
            directory_names[:] = []
            break
        current = Path(current_root)
        if (
            current != root
            and _directory_has_agents(root, current, file_names)
        ):
            nested_agents_count += 1

        safe_directories: list[str] = []
        for name in sorted(directory_names, key=str.casefold):
            candidate = current / name
            if candidate.is_symlink():
                continue
            if name == ".git":
                continue
            try:
                resolved = safe_path(root, candidate)
            except PermissionError:
                continue
            if name in _RUNTIME_DIRECTORY_NAMES:
                if len(runtime_paths) < MAX_RUNTIME_PATHS:
                    runtime_paths.append(resolved.relative_to(root).as_posix())
                else:
                    runtime_paths_truncated = True
                continue
            if name in IGNORED_DIRECTORIES:
                continue
            if resolved.is_dir():
                safe_directories.append(name)
        directory_names[:] = safe_directories

    return {
        "nested_agents_count": nested_agents_count,
        "runtime_paths": runtime_paths,
        "runtime_paths_truncated": runtime_paths_truncated,
        "scan_truncated": scan_truncated,
    }


def _ci_paths(root: Path) -> tuple[list[str], bool]:
    paths = [
        name
        for name in (
            ".gitlab-ci.yml",
            ".gitlab-ci.yaml",
            "azure-pipelines.yml",
            "bitbucket-pipelines.yml",
            "Jenkinsfile",
            ".circleci/config.yml",
        )
        if _is_file(root, name)
    ]
    try:
        workflows = safe_path(root, ".github/workflows")
    except PermissionError:
        workflows = None
    if (
        workflows is not None
        and workflows.is_dir()
        and not workflows.is_symlink()
    ):
        for entry in sorted(
            workflows.iterdir(),
            key=lambda item: item.name.casefold(),
        ):
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.suffix.casefold() not in {".yml", ".yaml"}:
                continue
            paths.append(entry.relative_to(root).as_posix())
            if len(paths) > MAX_CI_PATHS:
                return paths[:MAX_CI_PATHS], True
    return paths[:MAX_CI_PATHS], len(paths) > MAX_CI_PATHS


def _tracked_suspicious_paths(root: Path) -> dict[str, Any]:
    git_executable = resolve_executable("git", root)
    if git_executable is None:
        return {"status": "git_unavailable", "paths": [], "truncated": False}
    try:
        completed = subprocess.run(
            [git_executable, "ls-files", "--cached", "-z", "--", "."],
            cwd=root,
            capture_output=True,
            timeout=GIT_INSPECTION_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "git_unavailable", "paths": [], "truncated": False}
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").casefold()
        status = (
            "not_a_git_repository"
            if "not a git repository" in stderr
            else "git_unavailable"
        )
        return {"status": status, "paths": [], "truncated": False}
    if len(completed.stdout) > MAX_TRACKED_OUTPUT_BYTES:
        return {"status": "output_too_large", "paths": [], "truncated": True}

    suspicious: list[str] = []
    truncated = False
    for raw_path in completed.stdout.decode("utf-8", errors="replace").split("\0"):
        if not raw_path:
            continue
        normalized = raw_path.replace("\\", "/")
        pure_path = PurePosixPath(normalized)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            continue
        if not _is_suspicious_path(pure_path):
            continue
        if len(suspicious) >= MAX_SUSPICIOUS_TRACKED_PATHS:
            truncated = True
            break
        suspicious.append(pure_path.as_posix())
    return {
        "status": "checked",
        "paths": suspicious,
        "truncated": truncated,
    }


def _is_suspicious_path(path: PurePosixPath) -> bool:
    if any(part in _RUNTIME_DIRECTORY_NAMES for part in path.parts):
        return True
    name = path.name.casefold()
    if name == ".env" or (
        name.startswith(".env.")
        and name not in {".env.example", ".env.sample", ".env.template"}
    ):
        return True
    return name in _SECRET_FILENAMES or path.suffix.casefold() in _SECRET_SUFFIXES


def _first_file(root: Path, names: tuple[str, ...]) -> str | None:
    for name in names:
        if _is_file(root, name):
            return name
    return None


def _directory_has_agents(
    root: Path,
    directory: Path,
    file_names: list[str],
) -> bool:
    for name in _AGENTS_NAMES:
        if name not in file_names:
            continue
        raw_path = directory / name
        if raw_path.is_symlink():
            continue
        try:
            candidate = safe_path(root, raw_path)
        except PermissionError:
            continue
        if candidate.is_file():
            return True
    return False


def _pyproject_has_test_config(root: Path) -> bool:
    path = _existing_regular_file(root, "pyproject.toml")
    if path is None:
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(MAX_TEST_CONFIG_CHARACTERS + 1).casefold()
    except OSError:
        return False
    return any(
        marker in content
        for marker in (
            "[tool.pytest.",
            "[tool.coverage.",
        )
    )


def _existing_regular_file(root: Path, relative_path: str) -> Path | None:
    try:
        candidate = safe_path(root, relative_path)
    except PermissionError:
        return None
    return candidate if candidate.is_file() and not candidate.is_symlink() else None


def _is_file(root: Path, relative_path: str) -> bool:
    return _existing_regular_file(root, relative_path) is not None


def _is_directory(root: Path, relative_path: str) -> bool:
    try:
        candidate = safe_path(root, relative_path)
    except PermissionError:
        return False
    return candidate.is_dir() and not candidate.is_symlink()


def _is_effectively_empty(root: Path) -> bool:
    for entry in root.iterdir():
        if entry.name == ".git" or entry.name in _RUNTIME_DIRECTORY_NAMES:
            continue
        try:
            safe_path(root, entry)
        except PermissionError:
            continue
        return False
    return True


__all__ = ["project_health"]
