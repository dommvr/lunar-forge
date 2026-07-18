from datetime import datetime, timezone

import pytest

from lunar_forge.runtime import create_file_checkpoint


def test_checkpoint_preserves_relative_path_and_original_bytes(tmp_path):
    source = tmp_path / "src" / "settings.bin"
    source.parent.mkdir()
    original = b"original\x00bytes\n"
    source.write_bytes(original)
    created_at = datetime(2026, 7, 18, 12, 34, 56, 789, tzinfo=timezone.utc)

    checkpoint = create_file_checkpoint(
        tmp_path,
        "src/settings.bin",
        created_at=created_at,
    )

    expected = (
        tmp_path
        / ".agent"
        / "checkpoints"
        / "20260718T123456.000789Z"
        / "src"
        / "settings.bin"
    )
    assert checkpoint == expected
    assert checkpoint.read_bytes() == original


def test_checkpoint_rejects_file_outside_project_root(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(PermissionError, match="outside the project root"):
        create_file_checkpoint(project_root, outside)

    assert not (project_root / ".agent" / "checkpoints").exists()
