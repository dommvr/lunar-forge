import pytest

from lunar_forge.permissions import is_subpath
from lunar_forge.tools.files import read_file, safe_path


def test_is_subpath_accepts_child(tmp_path):
    child = tmp_path / "src" / "main.py"
    child.parent.mkdir()
    child.write_text("", encoding="utf-8")

    assert is_subpath(child, tmp_path)


def test_is_subpath_rejects_sibling(tmp_path):
    root = tmp_path / "root"
    sibling = tmp_path / "sibling"
    root.mkdir()
    sibling.mkdir()

    assert not is_subpath(sibling, root)


def test_safe_path_accepts_project_child(tmp_path):
    expected = (tmp_path / "src" / "main.py").resolve()

    assert safe_path(tmp_path, "src/main.py") == expected


def test_safe_path_blocks_parent_traversal(tmp_path):
    with pytest.raises(PermissionError, match="outside the project root"):
        safe_path(tmp_path, "../outside.txt")


def test_safe_path_blocks_absolute_path_outside_root(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()

    with pytest.raises(PermissionError, match="outside the project root"):
        safe_path(project_root, tmp_path / "outside.txt")


def test_read_file_returns_error_for_parent_traversal(tmp_path):
    result = read_file(tmp_path, "../outside.txt")

    assert result["ok"] is False
    assert "outside the project root" in result["error"]
