"""Workflow for existing projects."""

from __future__ import annotations

from pathlib import Path

from lunar_forge.project_detection import detect_project_type
from lunar_forge.workflows.validation import WorkflowResult


def run(root: str | Path) -> WorkflowResult:
    project_type = detect_project_type(root)
    return WorkflowResult(ok=True, message=f"Detected {project_type} project.")
