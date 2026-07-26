import json

import pytest

from lunar_forge.tools.structured_readers import (
    MAX_MANY_FILES,
    MAX_REQUEST_PATH_CHARACTERS,
    read_json,
    read_many_files,
    read_yaml,
)


def test_read_json_returns_parsed_data_and_top_level_keys(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name": "demo", "scripts": {"test": "pytest"}}',
        encoding="utf-8",
    )

    result = read_json(tmp_path, "package.json")

    assert result == {
        "ok": True,
        "path": "package.json",
        "data": {
            "name": "demo",
            "scripts": {"test": "pytest"},
        },
        "truncated": False,
        "top_level_keys": ["name", "scripts"],
        "top_level_keys_truncated": False,
    }
    json.dumps(result, allow_nan=False)


def test_read_json_reports_malformed_location_without_preview(tmp_path):
    (tmp_path / "broken.json").write_text(
        '{\n  "name": "demo",\n  "scripts": ]\n}\n',
        encoding="utf-8",
    )

    result = read_json(tmp_path, "broken.json")

    assert result["ok"] is False
    assert result["line"] == 3
    assert result["column"] > 0
    assert "Invalid JSON at line 3, column" in result["error"]
    assert "preview" not in result
    assert "data" not in result


def test_read_yaml_uses_safe_structured_data_and_normalizes_dates(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "name: demo\nreleased: 2026-07-26\nchecks:\n  - test\n  - lint\n",
        encoding="utf-8",
    )

    result = read_yaml(tmp_path, "config.yaml")

    assert result["ok"] is True
    assert result["path"] == "config.yaml"
    assert result["data"] == {
        "name": "demo",
        "released": "2026-07-26",
        "checks": ["test", "lint"],
    }
    assert result["top_level_keys"] == ["name", "released", "checks"]
    assert result["truncated"] is False
    json.dumps(result, allow_nan=False)


def test_read_yaml_reports_malformed_location_without_preview(tmp_path):
    (tmp_path / "broken.yaml").write_text(
        "name: demo\nchecks: [test,\n",
        encoding="utf-8",
    )

    result = read_yaml(tmp_path, "broken.yaml")

    assert result["ok"] is False
    assert result["line"] >= 2
    assert result["column"] > 0
    assert "Invalid YAML at line" in result["error"]
    assert "preview" not in result
    assert "data" not in result


def test_read_yaml_rejects_python_specific_tags(tmp_path):
    (tmp_path / "unsafe.yaml").write_text(
        "!!python/object/apply:builtins.str [unsafe]\n",
        encoding="utf-8",
    )

    result = read_yaml(tmp_path, "unsafe.yaml")

    assert result["ok"] is False
    assert "Invalid YAML at line" in result["error"]
    assert "data" not in result


def test_structured_readers_block_path_traversal(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text('{"canary": "must-not-leak"}', encoding="utf-8")

    result = read_json(tmp_path, f"../{outside.name}")

    assert result["ok"] is False
    assert "outside the project root" in result["error"]
    assert "must-not-leak" not in json.dumps(result)


@pytest.mark.parametrize(
    "relative_path",
    (
        ".env",
        ".env.local",
        ".envrc",
        ".agent/session.json",
        ".git/config.json",
        ".ssh/config.json",
        "secrets/config.json",
        "credentials/service.json",
        "node_modules/pkg/data.json",
        ".venv/data.json",
        "venv/data.json",
        "custom_pycache_folder/data.json",
        "dist/data.json",
        "build/data.json",
        "coverage/data.json",
        "server.key",
        "certificate.pem",
        "credentials.json",
        "private-key.json",
        "service-account.json",
    ),
)
def test_structured_readers_block_secret_runtime_and_generated_paths(
    tmp_path,
    relative_path,
):
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"canary": "must-not-leak"}', encoding="utf-8")

    result = read_json(tmp_path, relative_path)

    assert result["ok"] is False
    assert "must-not-leak" not in json.dumps(result)
    assert "blocked" in result["error"] or "secret" in result["error"]


def test_large_json_returns_bounded_preview_instead_of_partial_data(tmp_path):
    (tmp_path / "large.json").write_text(
        json.dumps({"items": ["x" * 40 for _ in range(20)]}),
        encoding="utf-8",
    )

    result = read_json(tmp_path, "large.json", max_bytes=80)

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["preview"].encode("utf-8")) <= 80
    assert "data" not in result
    assert "top_level_keys" not in result
    json.dumps(result)


def test_large_yaml_returns_bounded_preview_instead_of_partial_data(tmp_path):
    (tmp_path / "large.yaml").write_text(
        "items:\n" + "".join(f"  - {'x' * 40}\n" for _ in range(20)),
        encoding="utf-8",
    )

    result = read_yaml(tmp_path, "large.yaml", max_bytes=80)

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["preview"].encode("utf-8")) <= 80
    assert "data" not in result
    assert "top_level_keys" not in result
    json.dumps(result)


def test_structured_reader_rejects_overlong_path_before_filesystem_access(
    tmp_path,
):
    result = read_json(
        tmp_path,
        "a" * (MAX_REQUEST_PATH_CHARACTERS + 1),
    )

    assert result["ok"] is False
    assert f"at most {MAX_REQUEST_PATH_CHARACTERS} characters" in result["error"]
    assert len(result["path"]) <= 500


def test_structured_reader_blocks_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.yaml"
    outside.write_text("canary: must-not-leak\n", encoding="utf-8")
    link = tmp_path / "linked.yaml"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this platform.")

    result = read_yaml(tmp_path, "linked.yaml")

    assert result["ok"] is False
    assert "outside the project root" in result["error"]
    assert "must-not-leak" not in json.dumps(result)


def test_read_many_files_returns_partial_success_and_skips_binary(tmp_path):
    (tmp_path / "good.txt").write_bytes(b"first\nsecond\n")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01secret-binary")
    (tmp_path / ".env").write_text("TOKEN=must-not-leak\n", encoding="utf-8")
    secret_directory = tmp_path / "secrets"
    secret_directory.mkdir()
    (secret_directory / "notes.txt").write_text(
        "directory-secret-must-not-leak",
        encoding="utf-8",
    )

    result = read_many_files(
        tmp_path,
        ["good.txt", "binary.bin", "missing.txt", ".env", "secrets/notes.txt"],
    )
    by_path = {item["path"]: item for item in result["files"]}

    assert result["ok"] is True
    assert result["success_count"] == 1
    assert result["error_count"] == 4
    assert by_path["good.txt"] == {
        "path": "good.txt",
        "content": "first\nsecond\n",
        "line_count": 2,
        "truncated": False,
    }
    assert "binary" in by_path["binary.bin"]["error"]
    assert "does not exist" in by_path["missing.txt"]["error"]
    assert "secret" in by_path[".env"]["error"]
    assert "secret" in by_path["secrets/notes.txt"]["error"]
    assert "must-not-leak" not in json.dumps(result)
    assert "secret-binary" not in json.dumps(result)
    assert "directory-secret-must-not-leak" not in json.dumps(result)


def test_read_many_files_caps_file_count_and_total_bytes(tmp_path):
    paths = []
    for index in range(MAX_MANY_FILES + 2):
        path = f"file-{index:02}.txt"
        paths.append(path)
        (tmp_path / path).write_text("abcdefghij\n", encoding="utf-8")

    result = read_many_files(
        tmp_path,
        paths,
        max_bytes_per_file=10,
        max_total_bytes=15,
    )

    assert result["ok"] is True
    assert result["requested_count"] == MAX_MANY_FILES + 2
    assert result["returned_count"] == MAX_MANY_FILES
    assert result["paths_truncated"] is True
    assert result["omitted_count"] == 2
    assert result["total_bytes"] == 15
    assert result["truncated"] is True
    assert sum(
        len(item["content"].encode("utf-8"))
        for item in result["files"]
    ) <= 15
    assert any(
        item.get("error") == "Total byte limit reached before reading file."
        for item in result["files"]
    )
    json.dumps(result)


def test_read_many_files_does_not_expand_globs_or_read_directories(tmp_path):
    (tmp_path / "private.txt").write_text(
        "whole-project-canary",
        encoding="utf-8",
    )
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "app.py").write_text("print('canary')\n", encoding="utf-8")

    result = read_many_files(tmp_path, ["**/*", ".", "src"])

    assert result["ok"] is True
    assert result["success_count"] == 0
    assert result["error_count"] == 3
    assert all(item["content"] == "" for item in result["files"])
    assert "whole-project-canary" not in json.dumps(result)


def test_read_many_files_rejects_unbounded_or_invalid_requests(tmp_path):
    assert read_many_files(tmp_path, [])["ok"] is False
    assert read_many_files(tmp_path, ["one.txt"], max_total_bytes=0)["ok"] is False
    assert (
        read_many_files(tmp_path, ["one.txt"], max_bytes_per_file=True)["ok"]
        is False
    )
