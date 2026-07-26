"""Lightweight, bounded symbol discovery for Python and JavaScript projects."""

from __future__ import annotations

import ast
import codecs
import re
from pathlib import Path
from typing import Any

from lunar_forge.tools.structured_readers import resolve_safe_readable_file


MAX_SYMBOL_SOURCE_BYTES = 128_000
MAX_SYMBOLS = 200
MAX_SYMBOL_NAME_CHARACTERS = 200
MAX_SYMBOL_CONTEXT_CHARACTERS = 300
MAX_SYMBOL_ERROR_CHARACTERS = 500
MAX_SYMBOL_PATH_CHARACTERS = 500

_PYTHON_EXTENSIONS = frozenset({".py", ".pyw"})
_JAVASCRIPT_EXTENSIONS = frozenset({".cjs", ".js", ".jsx", ".mjs"})
_TYPESCRIPT_EXTENSIONS = frozenset({".cts", ".mts", ".ts", ".tsx"})
_JS_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_JS_FUNCTION = re.compile(
    rf"^\s*(?P<export>export\s+)?(?P<default>default\s+)?"
    rf"(?P<declare>declare\s+)?(?P<async>async\s+)?function\s*\*?\s*"
    rf"(?P<name>{_JS_IDENTIFIER})\s*(?:<[^>{{}};]*>)?\s*\("
)
_JS_CLASS = re.compile(
    rf"^\s*(?P<export>export\s+)?(?P<default>default\s+)?"
    rf"(?P<declare>declare\s+)?(?P<abstract>abstract\s+)?class\s+"
    rf"(?P<name>{_JS_IDENTIFIER})\b"
)
_JS_CONSTANT = re.compile(
    rf"^\s*(?P<export>export\s+)?(?P<declare>declare\s+)?"
    rf"(?P<binding>const|let|var)\s+(?P<name>{_JS_IDENTIFIER})\b"
)
_REACT_CLASS = re.compile(
    r"\bextends\s+(?:React\.)?(?:PureComponent|Component)\b"
)
_FUNCTION_LIKE_INITIALIZER = re.compile(
    r"=>|\bfunction\b|\b(?:memo|forwardRef)\s*\("
)


def list_symbols(
    project_root: str | Path,
    path: str | Path,
) -> dict[str, Any]:
    """Return bounded definition metadata without importing or executing a file."""
    requested_language = _language_for_path(path)
    try:
        _, file_path, relative_path = resolve_safe_readable_file(
            project_root,
            path,
        )
    except (OSError, PermissionError, TypeError, ValueError) as exc:
        return _error_result(path, requested_language, exc)

    language = _language_for_path(file_path)
    if language == "unsupported":
        return {
            "ok": False,
            "path": relative_path,
            "language": language,
            "symbols": [],
            "truncated": False,
            "error": (
                "Unsupported file type. list_symbols supports Python, "
                "JavaScript, JSX, TypeScript, and TSX source files."
            ),
        }

    try:
        source, source_truncated = _read_source(file_path)
    except (OSError, UnicodeError, ValueError) as exc:
        return _error_result(relative_path, language, exc)

    if language == "python":
        if source_truncated:
            return {
                "ok": False,
                "path": relative_path,
                "language": language,
                "symbols": [],
                "truncated": True,
                "error": (
                    "Python source exceeds the symbol reader byte limit; "
                    "refusing to parse incomplete syntax."
                ),
            }
        return _python_symbols(relative_path, source)

    symbols, symbols_truncated = _javascript_symbols(source)
    return {
        "ok": True,
        "path": relative_path,
        "language": language,
        "symbols": symbols,
        "truncated": source_truncated or symbols_truncated,
    }


def _read_source(path: Path) -> tuple[str, bool]:
    with path.open("rb") as handle:
        payload = handle.read(MAX_SYMBOL_SOURCE_BYTES + 1)
    truncated = len(payload) > MAX_SYMBOL_SOURCE_BYTES
    payload = payload[:MAX_SYMBOL_SOURCE_BYTES]
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    try:
        source = decoder.decode(payload, final=not truncated)
    except UnicodeDecodeError as exc:
        raise ValueError("Source file is binary or is not valid UTF-8.") from exc
    if b"\x00" in payload:
        raise ValueError("Source file is binary or is not valid UTF-8.")
    return source, truncated


def _python_symbols(path: str, source: str) -> dict[str, Any]:
    try:
        tree = ast.parse(source, filename=path)
    except (RecursionError, SyntaxError) as exc:
        if isinstance(exc, SyntaxError):
            line = exc.lineno
            column = exc.offset
            location = (
                f" at line {line}, column {column}"
                if line is not None and column is not None
                else ""
            )
            result = {
                "ok": False,
                "path": path,
                "language": "python",
                "symbols": [],
                "truncated": False,
                "error": _bounded_text(
                    f"Invalid Python syntax{location}: {exc.msg}",
                    MAX_SYMBOL_ERROR_CHARACTERS,
                ),
            }
            if line is not None:
                result["line"] = line
            if column is not None:
                result["column"] = column
            return result
        return _error_result(
            path,
            "python",
            ValueError("Python syntax exceeds the parser recursion limit."),
        )

    visitor = _PythonSymbolVisitor()
    visitor.visit(tree)
    return {
        "ok": True,
        "path": path,
        "language": "python",
        "symbols": visitor.symbols,
        "truncated": visitor.truncated,
    }


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[dict[str, Any]] = []
        self.truncated = False
        self._containers: list[tuple[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node.name, "class", node.lineno)
        self._containers.append((node.name, "class"))
        self.generic_visit(node)
        self._containers.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, asynchronous=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, asynchronous=True)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        asynchronous: bool,
    ) -> None:
        is_method = bool(
            self._containers and self._containers[-1][1] == "class"
        )
        if is_method:
            kind = "async_method" if asynchronous else "method"
        else:
            kind = "async_function" if asynchronous else "function"
        self._record(node.name, kind, node.lineno)
        self._containers.append((node.name, "function"))
        self.generic_visit(node)
        self._containers.pop()

    def _record(self, name: str, kind: str, line: int) -> None:
        if len(self.symbols) >= MAX_SYMBOLS:
            self.truncated = True
            return
        container = ".".join(name for name, _ in self._containers)
        qualified_name = f"{container}.{name}" if container else name
        symbol: dict[str, Any] = {
            "name": name,
            "kind": kind,
            "line": line,
            "qualified_name": qualified_name,
        }
        if container:
            symbol["container"] = container
        self.truncated = _append_symbol(self.symbols, symbol) or self.truncated


def _javascript_symbols(source: str) -> tuple[list[dict[str, Any]], bool]:
    lines = _strip_javascript_comments(source.splitlines())
    symbols: list[dict[str, Any]] = []
    truncated = False

    for index, line in enumerate(lines):
        line_number = index + 1
        window = " ".join(lines[index : index + 4])
        function_match = _JS_FUNCTION.match(line)
        if function_match is not None:
            name = function_match.group("name")
            symbol: dict[str, Any] = {
                "name": name,
                "kind": (
                    "async_function"
                    if function_match.group("async")
                    else "function"
                ),
                "line": line_number,
            }
            _add_export_metadata(symbol, function_match)
            if _is_pascal_case(name):
                symbol["react_component"] = True
            truncated = _append_symbol(symbols, symbol) or truncated
            continue

        class_match = _JS_CLASS.match(line)
        if class_match is not None:
            symbol = {
                "name": class_match.group("name"),
                "kind": "class",
                "line": line_number,
            }
            _add_export_metadata(symbol, class_match)
            if _REACT_CLASS.search(window):
                symbol["react_component"] = True
            truncated = _append_symbol(symbols, symbol) or truncated
            continue

        constant_match = _JS_CONSTANT.match(line)
        if constant_match is None:
            continue
        name = constant_match.group("name")
        exported = constant_match.group("export") is not None
        declaration = window.split(";", 1)[0]
        react_component = (
            _is_pascal_case(name)
            and _FUNCTION_LIKE_INITIALIZER.search(declaration) is not None
        )
        if not exported and not react_component:
            continue
        symbol = {
            "name": name,
            "kind": "constant",
            "line": line_number,
        }
        if exported:
            symbol["exported"] = True
        if react_component:
            symbol["react_component"] = True
        truncated = _append_symbol(symbols, symbol) or truncated

    return symbols, truncated


def _strip_javascript_comments(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    in_block_comment = False
    in_template = False
    for line in lines:
        output: list[str] = []
        quote: str | None = None
        escaped = False
        index = 0
        while index < len(line):
            character = line[index]
            following = line[index + 1] if index + 1 < len(line) else ""
            if in_block_comment:
                if character == "*" and following == "/":
                    in_block_comment = False
                    index += 2
                else:
                    index += 1
                continue
            if in_template:
                if character == "`" and not escaped:
                    in_template = False
                escaped = character == "\\" and not escaped
                if character != "\\":
                    escaped = False
                index += 1
                continue
            if quote is not None:
                output.append(character)
                if character == quote and not escaped:
                    quote = None
                escaped = character == "\\" and not escaped
                if character != "\\":
                    escaped = False
                index += 1
                continue
            if character in {"'", '"'}:
                quote = character
                output.append(character)
                index += 1
                continue
            if character == "`":
                in_template = True
                index += 1
                continue
            if character == "/" and following == "*":
                in_block_comment = True
                index += 2
                continue
            if character == "/" and following == "/":
                break
            output.append(character)
            index += 1
        cleaned.append("".join(output))
    return cleaned


def _add_export_metadata(
    symbol: dict[str, Any],
    match: re.Match[str],
) -> None:
    if match.group("export") is not None:
        symbol["exported"] = True
    if match.group("default") is not None:
        symbol["default_export"] = True


def _append_symbol(
    symbols: list[dict[str, Any]],
    symbol: dict[str, Any],
) -> bool:
    if len(symbols) >= MAX_SYMBOLS:
        return True
    value_truncated = False
    for key, maximum in (
        ("name", MAX_SYMBOL_NAME_CHARACTERS),
        ("container", MAX_SYMBOL_CONTEXT_CHARACTERS),
        ("qualified_name", MAX_SYMBOL_CONTEXT_CHARACTERS),
    ):
        value = symbol.get(key)
        if not isinstance(value, str):
            continue
        bounded = _bounded_text(value, maximum)
        if bounded != value:
            symbol[key] = bounded
            value_truncated = True
    symbols.append(symbol)
    return value_truncated


def _is_pascal_case(name: str) -> bool:
    return bool(name and name[0].isupper())


def _language_for_path(path: object) -> str:
    try:
        suffix = Path(str(path)).suffix.casefold()
    except (TypeError, ValueError):
        return "unsupported"
    if suffix in _PYTHON_EXTENSIONS:
        return "python"
    if suffix in _JAVASCRIPT_EXTENSIONS:
        return "javascript"
    if suffix in _TYPESCRIPT_EXTENSIONS:
        return "typescript"
    return "unsupported"


def _error_result(
    path: object,
    language: str,
    error: Exception,
) -> dict[str, Any]:
    return {
        "ok": False,
        "path": _bounded_text(str(path), MAX_SYMBOL_PATH_CHARACTERS),
        "language": language,
        "symbols": [],
        "truncated": False,
        "error": _bounded_text(str(error), MAX_SYMBOL_ERROR_CHARACTERS),
    }


def _bounded_text(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else f"{value[: maximum - 3]}..."


__all__ = [
    "MAX_SYMBOLS",
    "MAX_SYMBOL_CONTEXT_CHARACTERS",
    "MAX_SYMBOL_NAME_CHARACTERS",
    "MAX_SYMBOL_SOURCE_BYTES",
    "list_symbols",
]
