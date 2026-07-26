import json

import pytest

from lunar_forge.tools.symbols import (
    MAX_SYMBOLS,
    MAX_SYMBOL_SOURCE_BYTES,
    list_symbols,
)


def test_list_symbols_reports_python_functions_classes_and_methods(tmp_path):
    (tmp_path / "example.py").write_bytes(
        b"def top_level():\n"
        b"    return True\n"
        b"\n"
        b"async def fetch_data():\n"
        b"    return None\n"
        b"\n"
        b"class Greeter:\n"
        b"    def greet(self):\n"
        b"        return 'hello'\n"
        b"\n"
        b"    async def stream(self):\n"
        b"        return None\n"
        b"\n"
        b"def outer():\n"
        b"    def nested():\n"
        b"        return None\n"
        b"    return nested\n"
    )

    result = list_symbols(tmp_path, "example.py")
    symbols = {
        (item["name"], item["kind"]): item
        for item in result["symbols"]
    }

    assert result["ok"] is True
    assert result["path"] == "example.py"
    assert result["language"] == "python"
    assert result["truncated"] is False
    assert symbols[("top_level", "function")]["line"] == 1
    assert symbols[("fetch_data", "async_function")]["line"] == 4
    assert symbols[("Greeter", "class")]["line"] == 7
    assert symbols[("greet", "method")]["container"] == "Greeter"
    assert symbols[("greet", "method")]["qualified_name"] == "Greeter.greet"
    assert symbols[("stream", "async_method")]["line"] == 11
    assert symbols[("nested", "function")]["container"] == "outer"
    json.dumps(result, allow_nan=False)


def test_list_symbols_reports_python_syntax_error_location(tmp_path):
    (tmp_path / "broken.py").write_text(
        "def broken(:\n    pass\n",
        encoding="utf-8",
    )

    result = list_symbols(tmp_path, "broken.py")

    assert result["ok"] is False
    assert result["language"] == "python"
    assert result["symbols"] == []
    assert result["line"] == 1
    assert result["column"] > 0
    assert "Invalid Python syntax at line 1, column" in result["error"]
    assert result["truncated"] is False


@pytest.mark.parametrize(
    ("extension", "language"),
    (
        ("js", "javascript"),
        ("jsx", "javascript"),
        ("ts", "typescript"),
        ("tsx", "typescript"),
    ),
)
def test_list_symbols_detects_javascript_typescript_exports_and_components(
    tmp_path,
    extension,
    language,
):
    path = tmp_path / f"example.{extension}"
    path.write_bytes(
        b"// function ignored() {}\n"
        b"function helper() {}\n"
        b"export async function loadData() {}\n"
        b"export default function App() {}\n"
        b"export class Service {}\n"
        b"export const API_URL = '/api';\n"
        b"const Card = () => null;\n"
        b"class Legacy extends React.Component {}\n"
        b"/*\n"
        b"function hidden() {}\n"
        b"*/\n"
    )

    result = list_symbols(tmp_path, path.name)
    by_name = {item["name"]: item for item in result["symbols"]}

    assert result["ok"] is True
    assert result["language"] == language
    assert result["truncated"] is False
    assert set(by_name) == {
        "helper",
        "loadData",
        "App",
        "Service",
        "API_URL",
        "Card",
        "Legacy",
    }
    assert by_name["helper"] == {
        "name": "helper",
        "kind": "function",
        "line": 2,
    }
    assert by_name["loadData"]["kind"] == "async_function"
    assert by_name["loadData"]["exported"] is True
    assert by_name["App"]["default_export"] is True
    assert by_name["App"]["react_component"] is True
    assert by_name["Service"]["kind"] == "class"
    assert by_name["Service"]["exported"] is True
    assert by_name["API_URL"]["kind"] == "constant"
    assert by_name["API_URL"]["exported"] is True
    assert by_name["Card"]["react_component"] is True
    assert by_name["Legacy"]["react_component"] is True


def test_list_symbols_returns_clear_unsupported_file_result(tmp_path):
    (tmp_path / "notes.txt").write_text("plain text", encoding="utf-8")

    result = list_symbols(tmp_path, "notes.txt")

    assert result == {
        "ok": False,
        "path": "notes.txt",
        "language": "unsupported",
        "symbols": [],
        "truncated": False,
        "error": (
            "Unsupported file type. list_symbols supports Python, "
            "JavaScript, JSX, TypeScript, and TSX source files."
        ),
    }


def test_list_symbols_blocks_path_traversal_without_reading_content(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("CANARY = 'must-not-leak'\n", encoding="utf-8")

    result = list_symbols(tmp_path, f"../{outside.name}")

    assert result["ok"] is False
    assert result["symbols"] == []
    assert "outside the project root" in result["error"]
    assert "must-not-leak" not in json.dumps(result)


@pytest.mark.parametrize(
    "relative_path",
    (
        ".env.py",
        ".agent/session.py",
        ".git/hooks/example.py",
        ".ssh/helper.py",
        "secrets/private.py",
        "node_modules/package/index.js",
        ".venv/library.py",
        "venv/library.py",
        "__pycache__/cached.py",
        "dist/bundle.js",
        "build/generated.ts",
        "coverage/report.js",
        "private.pem",
    ),
)
def test_list_symbols_blocks_secret_runtime_and_generated_paths(
    tmp_path,
    relative_path,
):
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("CANARY = 'must-not-leak'\n", encoding="utf-8")

    result = list_symbols(tmp_path, relative_path)

    assert result["ok"] is False
    assert result["symbols"] == []
    assert "must-not-leak" not in json.dumps(result)
    assert "blocked" in result["error"] or "secret" in result["error"]


def test_list_symbols_caps_symbol_count(tmp_path):
    source = "".join(
        f"def symbol_{index}():\n    pass\n"
        for index in range(MAX_SYMBOLS + 25)
    )
    (tmp_path / "many.py").write_text(source, encoding="utf-8")

    result = list_symbols(tmp_path, "many.py")

    assert result["ok"] is True
    assert len(result["symbols"]) == MAX_SYMBOLS
    assert result["truncated"] is True
    json.dumps(result)


def test_list_symbols_refuses_to_parse_truncated_python_source(tmp_path):
    (tmp_path / "large.py").write_bytes(
        b"def visible():\n    return True\n#"
        + (b"x" * MAX_SYMBOL_SOURCE_BYTES)
    )

    result = list_symbols(tmp_path, "large.py")

    assert result["ok"] is False
    assert result["symbols"] == []
    assert result["truncated"] is True
    assert "refusing to parse incomplete syntax" in result["error"]


def test_list_symbols_marks_large_javascript_prefix_as_truncated(tmp_path):
    (tmp_path / "large.js").write_bytes(
        b"export function visible() {}\n//"
        + (b"x" * MAX_SYMBOL_SOURCE_BYTES)
    )

    result = list_symbols(tmp_path, "large.js")

    assert result["ok"] is True
    assert [item["name"] for item in result["symbols"]] == ["visible"]
    assert result["truncated"] is True


def test_list_symbols_does_not_execute_python_code(tmp_path):
    (tmp_path / "unsafe.py").write_text(
        "from pathlib import Path\n"
        "Path('executed.txt').write_text('ran')\n"
        "def safe_definition():\n"
        "    return True\n",
        encoding="utf-8",
    )

    result = list_symbols(tmp_path, "unsafe.py")

    assert result["ok"] is True
    assert [item["name"] for item in result["symbols"]] == [
        "safe_definition"
    ]
    assert not (tmp_path / "executed.txt").exists()
