"""Workflow for new projects."""

from __future__ import annotations

from pathlib import Path

from lunar_forge.workflows.validation import WorkflowResult


def run(root: str | Path) -> WorkflowResult:
    Path(root).mkdir(parents=True, exist_ok=True)
    return WorkflowResult(ok=True, message="Project directory is ready.")
