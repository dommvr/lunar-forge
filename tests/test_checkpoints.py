from datetime import datetime, timezone

import pytest

from lunar_forge.runtime import create_file_checkpoint
from lunar_forge.runtime.checkpoints import (
    list_checkpoint_directories,
    rollback_file,
)


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


def test_checkpoint_listing_is_newest_first(tmp_path):
    source = tmp_path / "example.txt"
    source.write_text("first", encoding="utf-8")
    create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    source.write_text("second", encoding="utf-8")
    create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    result = list_checkpoint_directories(tmp_path)

    assert result["ok"] is True
    assert [item["id"] for item in result["checkpoints"]] == [
        "20260201T000000.000000Z",
        "20260101T000000.000000Z",
    ]


def test_rollback_restores_latest_checkpoint_and_saves_replaced_state(tmp_path):
    source = tmp_path / "src" / "example.txt"
    source.parent.mkdir()
    source.write_text("version one", encoding="utf-8")
    create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    source.write_text("version two", encoding="utf-8")
    latest = create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    source.write_text("current state", encoding="utf-8")

    result = rollback_file(tmp_path, "src/example.txt")

    assert result["ok"] is True
    assert source.read_text(encoding="utf-8") == "version two"
    assert result["checkpoint_path"] == latest.relative_to(tmp_path).as_posix()
    previous_state = tmp_path / result["previous_state_checkpoint"]
    assert previous_state.read_text(encoding="utf-8") == "current state"


def test_rollback_blocks_outside_paths_and_reports_missing_checkpoint(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    outside_result = rollback_file(project_root, outside)
    missing_result = rollback_file(project_root, "missing.txt")

    assert outside_result["ok"] is False
    assert "outside the project root" in outside_result["error"]
    assert outside.read_text(encoding="utf-8") == "outside"
    assert missing_result == {
        "ok": False,
        "path": "missing.txt",
        "error": "No checkpoint exists for missing.txt.",
    }
