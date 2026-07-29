"""Create timestamped snapshots of files before mutation."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lunar_forge.tools.files import safe_path


MAX_CHECKPOINT_LIST_ENTRIES = 200


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


def list_checkpoint_directories(project_root: str | Path) -> dict[str, object]:
    """List project-local checkpoint directories, newest first."""
    try:
        root = _project_root(project_root)
        checkpoints_root = safe_path(root, ".agent/checkpoints")
        if not checkpoints_root.exists():
            return {
                "ok": True,
                "message": "No checkpoints found.",
                "checkpoints": [],
            }
        if not checkpoints_root.is_dir():
            raise NotADirectoryError(".agent/checkpoints is not a directory.")

        checkpoints: list[dict[str, str]] = []
        truncated = False
        for entry in sorted(
            checkpoints_root.iterdir(),
            key=lambda item: item.name,
            reverse=True,
        ):
            safe_entry = safe_path(root, entry)
            if not safe_entry.is_dir():
                continue
            if len(checkpoints) >= MAX_CHECKPOINT_LIST_ENTRIES:
                truncated = True
                break
            checkpoints.append(
                {
                    "id": safe_entry.name,
                    "path": safe_entry.relative_to(root).as_posix(),
                }
            )
        return {
            "ok": True,
            "message": (
                f"Found {len(checkpoints)} checkpoint director"
                f"{'y' if len(checkpoints) == 1 else 'ies'}"
                f"{' (list truncated).' if truncated else '.'}"
            ),
            "checkpoints": checkpoints,
            "truncated": truncated,
        }
    except (OSError, PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "checkpoints": []}


def rollback_file(
    project_root: str | Path,
    path: str | Path,
    *,
    checkpoint_id: str | None = None,
) -> dict[str, object]:
    """Restore an exact or latest checkpoint for one project-local file."""
    try:
        preview = preview_rollback_file(
            project_root,
            path,
            checkpoint_id=checkpoint_id,
        )
        if preview.get("ok") is not True:
            return preview
        root = _project_root(project_root)
        relative_target = Path(str(preview["path"]))
        target = safe_path(root, relative_target)
        checkpoint_source = safe_path(
            root,
            str(preview["checkpoint_path"]),
        )

        previous_state_checkpoint: str | None = None
        restored_existing = target.exists()
        if restored_existing:
            previous_state = create_file_checkpoint(root, target)
            previous_state_checkpoint = previous_state.relative_to(root).as_posix()

        safe_target = safe_path(root, relative_target)
        safe_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(checkpoint_source, safe_target)
        return {
            "ok": True,
            "path": relative_target.as_posix(),
            "checkpoint_path": checkpoint_source.relative_to(root).as_posix(),
            "checkpoint_id": checkpoint_source.parent.relative_to(
                safe_path(root, ".agent/checkpoints")
            ).parts[0],
            "previous_state_checkpoint": previous_state_checkpoint,
            "restored_existing": restored_existing,
        }
    except (OSError, PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def preview_rollback_file(
    project_root: str | Path,
    path: str | Path,
    *,
    checkpoint_id: str | None = None,
) -> dict[str, object]:
    """Resolve a rollback source without modifying the target project."""

    try:
        root = _project_root(project_root)
        target = safe_path(root, path)
        relative_target = target.relative_to(root)
        if not relative_target.parts:
            raise ValueError("Rollback path must identify a file.")
        if target.exists() and not target.is_file():
            raise IsADirectoryError("Rollback path is not a file.")
        checkpoint_source = _checkpoint_for(
            root,
            relative_target,
            checkpoint_id=checkpoint_id,
        )
        if checkpoint_source is None:
            qualifier = (
                f" in checkpoint {checkpoint_id}"
                if checkpoint_id is not None
                else ""
            )
            return {
                "ok": False,
                "path": relative_target.as_posix(),
                "error": (
                    "No checkpoint exists for "
                    f"{relative_target.as_posix()}{qualifier}."
                ),
            }
        return {
            "ok": True,
            "path": relative_target.as_posix(),
            "checkpoint_path": checkpoint_source.relative_to(root).as_posix(),
            "restored_existing": target.exists(),
        }
    except (OSError, PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _latest_checkpoint_for(root: Path, relative_path: Path) -> Path | None:
    return _checkpoint_for(root, relative_path, checkpoint_id=None)


def _checkpoint_for(
    root: Path,
    relative_path: Path,
    *,
    checkpoint_id: str | None,
) -> Path | None:
    checkpoints_root = safe_path(root, ".agent/checkpoints")
    if not checkpoints_root.exists():
        return None
    if not checkpoints_root.is_dir():
        raise NotADirectoryError(".agent/checkpoints is not a directory.")
    if checkpoint_id is None:
        candidates = sorted(
            checkpoints_root.iterdir(),
            key=lambda item: item.name,
            reverse=True,
        )
    else:
        normalized_id = checkpoint_id.strip()
        identifier = Path(normalized_id)
        if (
            not normalized_id
            or identifier.name != normalized_id
            or identifier.parent != Path(".")
        ):
            raise ValueError(
                "Checkpoint ID must be one direct checkpoint directory name."
            )
        candidates = [safe_path(root, checkpoints_root / normalized_id)]
    for checkpoint_directory in candidates:
        safe_directory = safe_path(root, checkpoint_directory)
        if not safe_directory.is_dir():
            continue
        candidate = safe_path(root, safe_directory / relative_path)
        if candidate.is_file():
            return candidate
    return None


def _project_root(project_root: str | Path) -> Path:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {root}")
    return root


def _timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.strftime("%Y%m%dT%H%M%S.%fZ")
