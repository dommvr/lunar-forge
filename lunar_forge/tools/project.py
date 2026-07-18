"""Project-level tools."""

from __future__ import annotations

from pathlib import Path

from lunar_forge.project_detection import detect_project_type


def describe_project(root: str | Path) -> dict[str, str]:
    root_path = Path(root).expanduser().resolve()
    return {
        "root": str(root_path),
        "type": detect_project_type(root_path),
    }
