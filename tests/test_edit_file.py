import json

import pytest

from lunar_forge.tools.files import (
    MAX_FILE_CHARACTERS,
    MAX_FILE_LINES,
    create_dir,
    edit_file,
    insert_lines,
    read_file_with_line_numbers,
    read_text,
    replace_lines,
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


def test_read_file_with_line_numbers_uses_source_line_numbers(tmp_path):
    (tmp_path / "example.txt").write_text(
        "alpha\nbeta\ngamma\n",
        encoding="utf-8",
    )

    result = read_file_with_line_numbers(
        tmp_path,
        "example.txt",
        start_line=2,
        end_line=3,
    )

    assert result["ok"] is True
    assert result["content"] == "2: beta\n3: gamma\n"
    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["line_numbers"] is True
    assert result["truncated"] is False


def test_numbered_read_respects_line_and_character_limits(tmp_path):
    (tmp_path / "many.txt").write_text(
        "".join(f"line {number}\n" for number in range(MAX_FILE_LINES + 1)),
        encoding="utf-8",
    )

    line_limited = read_file_with_line_numbers(tmp_path, "many.txt")

    assert line_limited["ok"] is True
    assert line_limited["end_line"] == MAX_FILE_LINES
    assert line_limited["truncated"] is True
    assert f"{MAX_FILE_LINES}: line {MAX_FILE_LINES - 1}\n" in (
        line_limited["content"]
    )

    (tmp_path / "long.txt").write_text(
        "x" * (MAX_FILE_CHARACTERS + 100),
        encoding="utf-8",
    )
    character_limited = read_file_with_line_numbers(tmp_path, "long.txt")

    assert character_limited["ok"] is True
    assert len(character_limited["content"]) == MAX_FILE_CHARACTERS
    assert character_limited["truncated"] is True


def test_replace_lines_returns_diff_checkpoint_and_preserves_crlf(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_bytes(b"one\r\ntwo\r\nthree\r\nfour\r\n")

    result = replace_lines(
        tmp_path,
        "example.txt",
        2,
        3,
        "replacement\nsecond replacement",
    )

    assert result["ok"] is True
    assert "-two" in result["diff"]
    assert "+replacement" in result["diff"]
    checkpoint = tmp_path / result["checkpoint_path"]
    assert checkpoint.read_bytes() == b"one\r\ntwo\r\nthree\r\nfour\r\n"
    assert file_path.read_bytes() == (
        b"one\r\nreplacement\r\nsecond replacement\r\nfour\r\n"
    )
    json.dumps(result)


@pytest.mark.parametrize(
    ("start_line", "end_line", "message"),
    (
        (0, 1, "start_line must be at least 1"),
        (2, 1, "end_line must be greater"),
        (4, 4, "outside the file"),
        (2, 4, "outside the file"),
    ),
)
def test_replace_lines_rejects_invalid_ranges_without_writing(
    tmp_path,
    start_line,
    end_line,
    message,
):
    file_path = tmp_path / "example.txt"
    file_path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = replace_lines(
        tmp_path,
        "example.txt",
        start_line,
        end_line,
        "replacement",
    )

    assert result["ok"] is False
    assert message in result["error"]
    assert file_path.read_text(encoding="utf-8") == "one\ntwo\nthree\n"
    assert not (tmp_path / ".agent" / "checkpoints").exists()


def test_insert_lines_supports_top_and_returns_diff_and_checkpoint(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_text("second\nthird\n", encoding="utf-8")

    result = insert_lines(tmp_path, "example.txt", 0, "first")

    assert result["ok"] is True
    assert "+first" in result["diff"]
    checkpoint = tmp_path / result["checkpoint_path"]
    assert checkpoint.read_text(encoding="utf-8") == "second\nthird\n"
    assert file_path.read_text(encoding="utf-8") == "first\nsecond\nthird\n"


def test_insert_lines_preserves_crlf_and_final_newline_state(tmp_path):
    crlf_path = tmp_path / "crlf.txt"
    crlf_path.write_bytes(b"one\r\nthree\r\n")

    middle = insert_lines(tmp_path, "crlf.txt", 1, "two")

    assert middle["ok"] is True
    assert crlf_path.read_bytes() == b"one\r\ntwo\r\nthree\r\n"

    no_final_newline = tmp_path / "no-final-newline.txt"
    no_final_newline.write_bytes(b"one")

    end = insert_lines(tmp_path, "no-final-newline.txt", 1, "two")

    assert end["ok"] is True
    assert no_final_newline.read_bytes() == b"one\ntwo"


def test_insert_lines_supports_empty_file_top(tmp_path):
    file_path = tmp_path / "empty.txt"
    file_path.write_text("", encoding="utf-8")

    result = insert_lines(tmp_path, "empty.txt", 0, "first")

    assert result["ok"] is True
    assert file_path.read_text(encoding="utf-8") == "first"


@pytest.mark.parametrize("after_line", (-1, 4))
def test_insert_lines_rejects_invalid_positions_without_writing(
    tmp_path,
    after_line,
):
    file_path = tmp_path / "example.txt"
    file_path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = insert_lines(
        tmp_path,
        "example.txt",
        after_line,
        "replacement",
    )

    assert result["ok"] is False
    assert "after_line" in result["error"]
    assert file_path.read_text(encoding="utf-8") == "one\ntwo\nthree\n"
    assert not (tmp_path / ".agent" / "checkpoints").exists()
