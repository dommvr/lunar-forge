import json

import pytest

from lunar_forge.tools.files import (
    create_dir,
    edit_file,
    read_text,
    write_file,
    write_text,
)


def test_write_and_read_text(tmp_path):
    write_text(tmp_path, "notes/example.txt", "hello")

    assert read_text(tmp_path, "notes/example.txt") == "hello"


def test_write_text_rejects_parent_escape(tmp_path):
    with pytest.raises(PermissionError):
        write_text(tmp_path, "../outside.txt", "nope")


def test_create_dir_stays_inside_project_root(tmp_path):
    result = create_dir(tmp_path, "src/components")

    assert result == {
        "ok": True,
        "path": "src/components",
        "created": True,
    }
    assert (tmp_path / "src" / "components").is_dir()

    escaped = create_dir(tmp_path, "../outside")
    assert escaped["ok"] is False


def test_write_file_creates_file_and_returns_diff(tmp_path):
    result = write_file(tmp_path, "notes/example.txt", "hello\n")

    assert result["ok"] is True
    assert result["created"] is True
    assert "--- /dev/null" in result["diff"]
    assert "+++ b/notes/example.txt" in result["diff"]
    assert result["checkpoint_path"] is None
    assert not (tmp_path / ".agent" / "checkpoints").exists()
    assert (tmp_path / "notes" / "example.txt").read_text(encoding="utf-8") == "hello\n"
    json.dumps(result)


def test_write_file_refuses_overwrite_by_default(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_text("original", encoding="utf-8")

    result = write_file(tmp_path, "example.txt", "replacement")

    assert result["ok"] is False
    assert "overwrite=true" in result["error"]
    assert file_path.read_text(encoding="utf-8") == "original"


def test_write_file_overwrites_only_when_explicit(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_text("original\n", encoding="utf-8")

    result = write_file(
        tmp_path,
        "example.txt",
        "replacement\n",
        overwrite=True,
    )

    assert result["ok"] is True
    assert result["overwritten"] is True
    assert "-original" in result["diff"]
    assert "+replacement" in result["diff"]
    checkpoint_path = tmp_path / result["checkpoint_path"]
    assert checkpoint_path.name == "example.txt"
    assert checkpoint_path.relative_to(tmp_path).parts[:2] == (
        ".agent",
        "checkpoints",
    )
    assert checkpoint_path.read_text(encoding="utf-8") == "original\n"
    assert file_path.read_text(encoding="utf-8") == "replacement\n"


def test_edit_file_fails_when_old_text_has_zero_matches(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_text("hello world\n", encoding="utf-8")

    result = edit_file(tmp_path, "example.txt", "missing", "replacement")

    assert result["ok"] is False
    assert "not found" in result["error"]
    assert file_path.read_text(encoding="utf-8") == "hello world\n"


def test_edit_file_fails_when_old_text_has_multiple_matches(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_text("repeat repeat\n", encoding="utf-8")

    result = edit_file(tmp_path, "example.txt", "repeat", "replacement")

    assert result["ok"] is False
    assert "matched 2 times" in result["error"]
    assert file_path.read_text(encoding="utf-8") == "repeat repeat\n"


def test_edit_file_succeeds_when_old_text_matches_exactly_once(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_text("before\nunique line\nafter\n", encoding="utf-8")

    result = edit_file(
        tmp_path,
        "example.txt",
        "unique line",
        "replacement line",
    )

    assert result["ok"] is True
    assert "-unique line" in result["diff"]
    assert "+replacement line" in result["diff"]
    checkpoint_path = tmp_path / result["checkpoint_path"]
    assert checkpoint_path.read_text(encoding="utf-8") == (
        "before\nunique line\nafter\n"
    )
    assert file_path.read_text(encoding="utf-8") == (
        "before\nreplacement line\nafter\n"
    )
    json.dumps(result)
