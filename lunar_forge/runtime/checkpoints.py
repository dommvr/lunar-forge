"""Create timestamped snapshots of files before mutation."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lunar_forge.tools.files import safe_path


@dataclass(frozen=True)
class Checkpoint:
    id: str
    created_at: datetime
    note: str = ""


def new_checkpoint(checkpoint_id: str, note: str = "") -> Checkpoint:
    return Checkpoint(
        id=checkpoint_id,
        created_at=datetime.now(timezone.utc),
        note=note,
    )


def create_file_checkpoint(
    project_root: str | Path,
    path: str | Path,
    *,
    created_at: datetime | None = None,
) -> Path:
    """Copy an existing project file into its timestamped checkpoint path."""
    root = Path(project_root).expanduser().resolve()
    source = safe_path(root, path)
    if not source.exists():
        raise FileNotFoundError("Cannot checkpoint a file that does not exist.")
    if not source.is_file():
        raise IsADirectoryError("Only files can be checkpointed.")

    relative_source = source.relative_to(root)
    timestamp = _timestamp(created_at or datetime.now(timezone.utc))
    checkpoint_relative = (
        Path(".agent") / "checkpoints" / timestamp / relative_source
    )
    checkpoint_path = safe_path(root, checkpoint_relative)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, checkpoint_path)
    return checkpoint_path


def _timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.strftime("%Y%m%dT%H%M%S.%fZ")
