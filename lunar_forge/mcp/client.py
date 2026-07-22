"""Bounded MCP discovery and invocation over local stdio transports."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

from lunar_forge import __version__
from lunar_forge.mcp.config import (
    MCPConfig,
    MCPConfigError,
    MCPServerConfig,
    load_mcp_config,
    mcp_config_paths,
    resolve_server_environment,
)
from lunar_forge.permissions import dangerous_command_reason
from lunar_forge.runtime.local_runner import (
    executable_path_summary,
    resolve_executable,
)


MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {MCP_PROTOCOL_VERSION, "2025-03-26", "2024-11-05"}
)
DEFAULT_STARTUP_TIMEOUT_MS = 10_000
DEFAULT_REQUEST_TIMEOUT_MS = 120_000
SHUTDOWN_TIMEOUT_SECONDS = 2.0
MAX_RPC_MESSAGE_CHARACTERS = 1_000_000
MAX_PENDING_MESSAGES = 200
MAX_IGNORED_MESSAGES_PER_REQUEST = 200
MAX_TOOL_LIST_PAGES = 50
MAX_ERROR_CHARACTERS = 500
MAX_TOOL_DESCRIPTION_CHARACTERS = 2_000
MAX_TOOL_SCHEMA_CHARACTERS = 50_000
MAX_CALL_ARGUMENT_CHARACTERS = 50_000
MAX_OUTPUT_CHARACTERS = 50_000
MAX_OUTPUT_STRING_CHARACTERS = 20_000
MAX_OUTPUT_COLLECTION_ITEMS = 100
MAX_OUTPUT_DEPTH = 12
MAX_DISCOVERED_TOOLS = 100
SAFE_CHILD_ENVIRONMENT_NAMES = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
)

_STREAM_EOF = object()
_STREAM_MESSAGE_TOO_LARGE = object()


class MCPClientError(RuntimeError):
    """Raised for invalid discovery data or unavailable MCP transports."""


class MCPTransport(Protocol):
    """Small synchronous transport seam used by stdio and mocked tests."""

    def list_tools(self) -> Sequence[Mapping[str, Any]]:
        """Return raw MCP tool definitions."""

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Invoke one remote tool and return its raw result."""


TransportFactory = Callable[[MCPServerConfig], MCPTransport]
PopenFactory = Callable[..., Any]
ExecutableResolver = Callable[[str, str | Path], str | None]


@dataclass(frozen=True)
class MCPToolDefinition:
    """Validated model-facing parts of one discovered MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False


class StdioMCPTransport:
    """Launch one configured local server and exchange newline JSON-RPC."""

    def __init__(
        self,
        server: MCPServerConfig,
        project_root: str | Path,
        *,
        startup_timeout_ms: int = DEFAULT_STARTUP_TIMEOUT_MS,
        request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        _popen_factory: PopenFactory | None = None,
        _executable_resolver: ExecutableResolver | None = None,
        _environ: Mapping[str, str] | None = None,
    ) -> None:
        self.server = server
        self.project_root = Path(project_root).expanduser().resolve()
        self.startup_timeout_ms = _validated_timeout(
            startup_timeout_ms,
            "startup_timeout_ms",
        )
        self.request_timeout_ms = _validated_timeout(
            request_timeout_ms,
            "request_timeout_ms",
        )
        self._popen_factory = _popen_factory or subprocess.Popen
        self._executable_resolver = _executable_resolver or resolve_executable
        self._base_environment = dict(os.environ if _environ is None else _environ)
        self._secret_values: tuple[str, ...] = ()
        self._process: Any | None = None
        self._messages: queue.Queue[object] = queue.Queue(
            maxsize=MAX_PENDING_MESSAGES
        )
        self._message_overflowed = threading.Event()
        self._request_lock = threading.Lock()
        self._next_request_id = 1
        self._initialized = False
        self._closed = False
        self.protocol_version: str | None = None

        try:
            self._start_and_initialize()
        except Exception:
            self.close()
            raise

    def list_tools(self) -> Sequence[Mapping[str, Any]]:
        """Request all bounded tool-list pages from the initialized server."""
        tools: list[Mapping[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_TOOL_LIST_PAGES):
            params = {} if cursor is None else {"cursor": cursor}
            result = self._request("tools/list", params)
            if not isinstance(result, Mapping):
                raise MCPClientError("MCP tools/list result must be an object.")
            page_tools = result.get("tools")
            if (
                isinstance(page_tools, (str, bytes))
                or not isinstance(page_tools, Sequence)
                or not all(isinstance(tool, Mapping) for tool in page_tools)
            ):
                raise MCPClientError("MCP tools/list must return a tool list.")
            if len(tools) + len(page_tools) > MAX_DISCOVERED_TOOLS:
                raise MCPClientError(
                    f"MCP servers may expose at most {MAX_DISCOVERED_TOOLS} tools."
                )
            tools.extend(page_tools)

            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return tuple(tools)
            if not isinstance(next_cursor, str) or not next_cursor:
                raise MCPClientError("MCP tools/list returned an invalid cursor.")
            if next_cursor in seen_cursors:
                raise MCPClientError("MCP tools/list repeated a pagination cursor.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise MCPClientError("MCP tools/list exceeded the page limit.")

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Invoke one tool through the initialized stdio session."""
        result = self._request(
            "tools/call",
            {"name": name, "arguments": dict(arguments)},
        )
        if not isinstance(result, Mapping):
            raise MCPClientError("MCP tools/call result must be an object.")
        return dict(result)

    def close(self) -> None:
        """Close stdin and terminate a server that does not exit promptly."""
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()
            except (OSError, ValueError):
                pass
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            return
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            process.terminate()
            process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            return
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            process.kill()
            process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            pass

    def __enter__(self) -> StdioMCPTransport:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _start_and_initialize(self) -> None:
        if not self.project_root.is_dir():
            raise MCPClientError("MCP project root must be an existing directory.")
        command_summary = " ".join((self.server.command, *self.server.args))
        dangerous_pattern = dangerous_command_reason(command_summary)
        if dangerous_pattern is not None:
            raise MCPClientError(
                "MCP server command was blocked by the dangerous-command policy."
            )
        executable = self._executable_resolver(
            self.server.command,
            self.project_root,
        )
        if executable is None:
            raise MCPClientError(
                f"MCP server {self.server.name!r} executable "
                f"{self.server.command!r} was not found. "
                f"{executable_path_summary()}"
            )

        resolved_environment = resolve_server_environment(
            self.server,
            self._base_environment,
        )
        self._secret_values = tuple(
            value for value in resolved_environment.values() if value
        )
        child_environment = _safe_child_environment(
            self._base_environment,
            resolved_environment,
        )
        started = time.monotonic()
        try:
            self._process = self._popen_factory(
                [executable, *self.server.args],
                cwd=self.project_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=child_environment,
                shell=False,
            )
        except OSError as exc:
            raise MCPClientError(
                "MCP server process could not start with "
                f"{type(exc).__name__}."
            ) from exc
        finally:
            self._base_environment.clear()
        if (
            getattr(self._process, "stdin", None) is None
            or getattr(self._process, "stdout", None) is None
            or getattr(self._process, "stderr", None) is None
        ):
            raise MCPClientError("MCP server process pipes were unavailable.")

        threading.Thread(
            target=self._read_stdout,
            name=f"lunar-forge-mcp-{self.server.name}-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._drain_stderr,
            name=f"lunar-forge-mcp-{self.server.name}-stderr",
            daemon=True,
        ).start()

        elapsed_ms = round((time.monotonic() - started) * 1_000)
        remaining_ms = self.startup_timeout_ms - elapsed_ms
        if remaining_ms <= 0:
            raise MCPClientError(
                f"MCP server {self.server.name!r} startup timed out after "
                f"{self.startup_timeout_ms} ms."
            )
        try:
            result = self._request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "lunar-forge",
                        "version": __version__,
                    },
                },
                timeout_ms=remaining_ms,
            )
        except MCPClientError as exc:
            if "timed out" in str(exc):
                raise MCPClientError(
                    f"MCP server {self.server.name!r} startup timed out after "
                    f"{self.startup_timeout_ms} ms."
                ) from exc
            raise
        if not isinstance(result, Mapping):
            raise MCPClientError("MCP initialize result must be an object.")
        protocol_version = result.get("protocolVersion")
        if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise MCPClientError("MCP server selected an unsupported protocol version.")
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, Mapping):
            raise MCPClientError("MCP initialize capabilities must be an object.")
        if not isinstance(capabilities.get("tools"), Mapping):
            raise MCPClientError("MCP server does not declare the tools capability.")
        self.protocol_version = str(protocol_version)
        self._send_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self._initialized = True

    def _request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        timeout = self.request_timeout_ms if timeout_ms is None else timeout_ms
        timeout = _validated_timeout(timeout, "timeout_ms")
        with self._request_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            self._send_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": dict(params),
                }
            )
            deadline = time.monotonic() + (timeout / 1_000)
            ignored_messages = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._cancel_request(request_id, method)
                    raise MCPClientError(
                        f"MCP {method} request timed out after {timeout} ms."
                    )
                if self._message_overflowed.is_set():
                    raise MCPClientError("MCP server produced too many messages.")
                try:
                    item = self._messages.get(timeout=remaining)
                except queue.Empty as exc:
                    self._cancel_request(request_id, method)
                    raise MCPClientError(
                        f"MCP {method} request timed out after {timeout} ms."
                    ) from exc
                if item is _STREAM_EOF:
                    exit_code = self._process.poll() if self._process is not None else None
                    suffix = (
                        f" with exit code {exit_code}"
                        if isinstance(exit_code, int)
                        else ""
                    )
                    raise MCPClientError(f"MCP server closed stdout{suffix}.")
                if item is _STREAM_MESSAGE_TOO_LARGE:
                    raise MCPClientError("MCP server message exceeded the size limit.")
                message = _decode_rpc_message(item)
                if "method" in message and "id" in message:
                    self._handle_server_request(message)
                    ignored_messages += 1
                elif message.get("id") == request_id:
                    if "error" in message:
                        error = message.get("error")
                        code = error.get("code") if isinstance(error, Mapping) else None
                        code_text = f" code {code}" if isinstance(code, int) else ""
                        raise MCPClientError(
                            f"MCP server returned a JSON-RPC error{code_text} "
                            f"for {method}."
                        )
                    if "result" not in message:
                        raise MCPClientError("MCP response did not contain a result.")
                    return _redact_secret_values(
                        message["result"],
                        self._secret_values,
                    )
                else:
                    ignored_messages += 1
                if ignored_messages > MAX_IGNORED_MESSAGES_PER_REQUEST:
                    raise MCPClientError(
                        "MCP request received too many unrelated messages."
                    )

    def _cancel_request(self, request_id: int, method: str) -> None:
        if not self._initialized:
            return
        try:
            self._send_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {
                        "requestId": request_id,
                        "reason": f"LunarForge timed out waiting for {method}.",
                    },
                }
            )
        except MCPClientError:
            pass

    def _handle_server_request(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "ping":
            response: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {},
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "Client method is not supported.",
                },
            }
        self._send_message(response)

    def _send_message(self, message: Mapping[str, Any]) -> None:
        if self._closed:
            raise MCPClientError("MCP transport is closed.")
        try:
            encoded = json.dumps(
                dict(message),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise MCPClientError("MCP request was not JSON-serializable.") from exc
        if len(encoded) > MAX_RPC_MESSAGE_CHARACTERS:
            raise MCPClientError("MCP request exceeded the message size limit.")
        stdin = getattr(self._process, "stdin", None)
        if stdin is None:
            raise MCPClientError("MCP server stdin is unavailable.")
        try:
            stdin.write(f"{encoded}\n")
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise MCPClientError("MCP server stdin is unavailable.") from exc

    def _read_stdout(self) -> None:
        stdout = getattr(self._process, "stdout", None)
        if stdout is None:
            self._queue_message(_STREAM_EOF)
            return
        while True:
            try:
                line = stdout.readline(MAX_RPC_MESSAGE_CHARACTERS + 2)
            except (OSError, ValueError):
                self._queue_message(_STREAM_EOF)
                return
            if line == "":
                self._queue_message(_STREAM_EOF)
                return
            if len(line) > MAX_RPC_MESSAGE_CHARACTERS or not line.endswith("\n"):
                self._queue_message(_STREAM_MESSAGE_TOO_LARGE)
                _drain_line(stdout, line)
                continue
            self._queue_message(line)

    def _drain_stderr(self) -> None:
        stderr = getattr(self._process, "stderr", None)
        if stderr is None:
            return
        while True:
            try:
                line = stderr.readline(MAX_RPC_MESSAGE_CHARACTERS + 2)
            except (OSError, ValueError):
                return
            if line == "":
                return
            if not line.endswith("\n"):
                _drain_line(stderr, line)

    def _queue_message(self, item: object) -> None:
        try:
            self._messages.put_nowait(item)
        except queue.Full:
            self._message_overflowed.set()


class MCPClient:
    """Discover and invoke tools through configured or injectable transports."""

    def __init__(
        self,
        config: MCPConfig,
        transport_factory: TransportFactory | None = None,
        *,
        project_root: str | Path | None = None,
        startup_timeout_ms: int = DEFAULT_STARTUP_TIMEOUT_MS,
    ) -> None:
        self.config = config
        self.project_root = Path(
            Path.cwd() if project_root is None else project_root
        ).expanduser().resolve()
        self._transport_factory = transport_factory or (
            lambda server: StdioMCPTransport(
                server,
                self.project_root,
                startup_timeout_ms=startup_timeout_ms,
            )
        )
        self._transports: dict[str, MCPTransport] = {}

    def discover_tools(self, server_name: str) -> tuple[MCPToolDefinition, ...]:
        """Validate tool schemas exposed by an explicitly enabled server."""
        transport = self._transport_for(server_name)
        try:
            raw_tools = transport.list_tools()
        except MCPClientError:
            raise
        except Exception as exc:
            raise MCPClientError(
                f"MCP tool discovery failed with {type(exc).__name__}."
            ) from exc
        if isinstance(raw_tools, (str, bytes)) or not isinstance(raw_tools, Sequence):
            raise MCPClientError("MCP tool discovery must return a sequence.")
        if len(raw_tools) > MAX_DISCOVERED_TOOLS:
            raise MCPClientError(
                f"MCP servers may expose at most {MAX_DISCOVERED_TOOLS} tools."
            )

        definitions: list[MCPToolDefinition] = []
        seen_names: set[str] = set()
        for raw_tool in raw_tools:
            definition = _parse_tool_definition(raw_tool)
            if definition.name in seen_names:
                raise MCPClientError(
                    f"MCP server returned duplicate tool name: {definition.name}"
                )
            seen_names.add(definition.name)
            definitions.append(definition)
        return tuple(definitions)

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Call an enabled MCP tool and return a bounded serializable envelope."""
        try:
            normalized_arguments, argument_truncated = _normalize_json(arguments)
            if argument_truncated or not isinstance(normalized_arguments, dict):
                return {"ok": False, "error": "MCP tool arguments are too large."}
            encoded_arguments = json.dumps(
                normalized_arguments,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            if len(encoded_arguments) > MAX_CALL_ARGUMENT_CHARACTERS:
                return {"ok": False, "error": "MCP tool arguments are too large."}
            transport = self._transport_for(server_name)
            raw_result = transport.call_tool(tool_name, normalized_arguments)
            return _bounded_result(raw_result)
        except MCPClientError as exc:
            return {"ok": False, "error": str(exc)[:MAX_ERROR_CHARACTERS]}
        except Exception as exc:
            return {
                "ok": False,
                "error": f"MCP tool call failed with {type(exc).__name__}.",
            }

    def close(self) -> None:
        """Close all transports created during discovery or invocation."""
        for transport in tuple(self._transports.values()):
            close = getattr(transport, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._transports.clear()

    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _transport_for(self, server_name: str) -> MCPTransport:
        server = self.config.servers.get(server_name)
        if server is None:
            raise MCPClientError(f"Unknown MCP server: {server_name}")
        if not server.enabled:
            raise MCPClientError(f"MCP server is disabled: {server_name}")
        transport = self._transports.get(server_name)
        if transport is None:
            transport = self._transport_factory(server)
            self._transports[server_name] = transport
        return transport


def build_mcp_diagnostic(
    project_root: str | Path,
    *,
    globally_enabled: bool,
    transport_factory: TransportFactory | None = None,
) -> dict[str, Any]:
    """Inspect configured servers and discover tools without model/API access."""
    root = Path(project_root).expanduser().resolve()
    try:
        user_path, project_path = mcp_config_paths(root)
    except (MCPConfigError, PermissionError) as exc:
        return _diagnostic_error_result(exc)
    config_files = [
        {
            "scope": scope,
            "path": str(path),
            "loaded": path.is_file(),
        }
        for scope, path in (("user", user_path), ("project", project_path))
    ]
    try:
        config = load_mcp_config(root)
    except (MCPConfigError, PermissionError) as exc:
        result = _diagnostic_error_result(exc)
        result["config_files"] = config_files
        return result

    enabled_servers = [server.name for server in config.enabled_servers]
    enabled_set = set(enabled_servers)
    disabled_servers = [
        name for name in sorted(config.servers) if name not in enabled_set
    ]
    result: dict[str, Any] = {
        "ok": True,
        "status": "disabled" if not globally_enabled else "passed",
        "mcp_enabled": globally_enabled,
        "config_files": config_files,
        "enabled_servers": enabled_servers,
        "disabled_servers": disabled_servers,
        "discovered_tools": [],
        "errors": [],
    }
    if not globally_enabled:
        result["note"] = (
            "MCP is disabled in config.yaml; configured servers were not started."
        )
        return result

    client = MCPClient(
        config,
        transport_factory=transport_factory,
        project_root=root,
    )
    discovered_count = 0
    try:
        for server in config.enabled_servers:
            try:
                definitions = client.discover_tools(server.name)
                if discovered_count + len(definitions) > MAX_DISCOVERED_TOOLS:
                    raise MCPClientError(
                        "Enabled MCP servers expose too many tools in total."
                    )
                from lunar_forge.mcp.registry import namespace_mcp_tool

                for definition in definitions:
                    result["discovered_tools"].append(
                        {
                            "server": server.name,
                            "name": namespace_mcp_tool(
                                server.name,
                                definition.name,
                            ),
                            "read_only": definition.read_only,
                        }
                    )
                discovered_count += len(definitions)
            except MCPClientError as exc:
                result["errors"].append(
                    {
                        "server": server.name,
                        "stage": "startup/discovery",
                        "error": str(exc)[:MAX_ERROR_CHARACTERS],
                    }
                )
            except Exception as exc:
                result["errors"].append(
                    {
                        "server": server.name,
                        "stage": "startup/discovery",
                        "error": (
                            "MCP startup/discovery failed with "
                            f"{type(exc).__name__}."
                        ),
                    }
                )
    finally:
        client.close()
    if result["errors"]:
        result["ok"] = False
        result["status"] = "failed"
    return result


def _parse_tool_definition(raw_tool: Mapping[str, Any]) -> MCPToolDefinition:
    if not isinstance(raw_tool, Mapping):
        raise MCPClientError("MCP tool definitions must be mappings.")
    name = raw_tool.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MCPClientError("MCP tool definitions require a name.")
    name = name.strip()

    description = raw_tool.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise MCPClientError(f"MCP tool '{name}' description must be a string.")
    description = description[:MAX_TOOL_DESCRIPTION_CHARACTERS]

    input_schema = raw_tool.get("inputSchema", raw_tool.get("input_schema"))
    if input_schema is None:
        input_schema = {"type": "object", "properties": {}}
    if not isinstance(input_schema, Mapping):
        raise MCPClientError(f"MCP tool '{name}' input schema must be an object.")
    schema = dict(input_schema)
    try:
        encoded_schema = json.dumps(
            schema,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise MCPClientError(
            f"MCP tool '{name}' input schema must be JSON-serializable."
        ) from exc
    if len(encoded_schema) > MAX_TOOL_SCHEMA_CHARACTERS:
        raise MCPClientError(f"MCP tool '{name}' input schema is too large.")

    annotations = raw_tool.get("annotations", {})
    if annotations is None:
        annotations = {}
    if not isinstance(annotations, Mapping):
        raise MCPClientError(f"MCP tool '{name}' annotations must be an object.")
    read_only = annotations.get("readOnlyHint", False)
    if not isinstance(read_only, bool):
        raise MCPClientError(
            f"MCP tool '{name}' annotations.readOnlyHint must be a boolean."
        )
    return MCPToolDefinition(
        name=name,
        description=description,
        input_schema=schema,
        read_only=read_only,
    )


def _bounded_result(raw_result: Any) -> dict[str, Any]:
    try:
        result, truncated = _normalize_json(raw_result)
        envelope: dict[str, Any] = {
            "ok": True,
            "result": result,
            "truncated": truncated,
        }
        encoded = json.dumps(
            envelope,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        return {"ok": False, "error": "MCP tool returned a non-serializable result."}
    if len(encoded) <= MAX_OUTPUT_CHARACTERS:
        return envelope
    return {
        "ok": True,
        "result": {"preview": encoded[: MAX_OUTPUT_CHARACTERS - 200]},
        "truncated": True,
    }


def _normalize_json(value: Any, depth: int = 0) -> tuple[Any, bool]:
    if depth > MAX_OUTPUT_DEPTH:
        return "[truncated: maximum depth]", True
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, str):
        if len(value) <= MAX_OUTPUT_STRING_CHARACTERS:
            return value, False
        return value[:MAX_OUTPUT_STRING_CHARACTERS], True
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        truncated = len(value) > MAX_OUTPUT_COLLECTION_ITEMS
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_OUTPUT_COLLECTION_ITEMS:
                break
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings.")
            normalized_item, item_truncated = _normalize_json(item, depth + 1)
            normalized[key] = normalized_item
            truncated = truncated or item_truncated
        return normalized, truncated
    if isinstance(value, (list, tuple)):
        normalized_items: list[Any] = []
        truncated = len(value) > MAX_OUTPUT_COLLECTION_ITEMS
        for item in value[:MAX_OUTPUT_COLLECTION_ITEMS]:
            normalized_item, item_truncated = _normalize_json(item, depth + 1)
            normalized_items.append(normalized_item)
            truncated = truncated or item_truncated
        return normalized_items, truncated
    raise TypeError(f"Unsupported MCP result type: {type(value).__name__}")


def _decode_rpc_message(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        raise MCPClientError("MCP server returned an invalid stream message.")
    try:
        message = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise MCPClientError("MCP server returned invalid JSON.") from exc
    if not isinstance(message, Mapping) or message.get("jsonrpc") != "2.0":
        raise MCPClientError("MCP server returned an invalid JSON-RPC message.")
    return dict(message)


def _redact_secret_values(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    if isinstance(value, Mapping):
        return {
            key: _redact_secret_values(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_secret_values(item, secrets) for item in value]
    return value


def _safe_child_environment(
    source: Mapping[str, str],
    configured: Mapping[str, str],
) -> dict[str, str]:
    environment = {
        name: value
        for name, value in source.items()
        if name.upper() in SAFE_CHILD_ENVIRONMENT_NAMES
    }
    environment.update(configured)
    return environment


def _drain_line(stream: TextIO, first_chunk: str) -> None:
    chunk = first_chunk
    while chunk and not chunk.endswith("\n"):
        try:
            chunk = stream.readline(MAX_RPC_MESSAGE_CHARACTERS + 2)
        except (OSError, ValueError):
            return


def _validated_timeout(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return value


def _diagnostic_error_result(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, (MCPConfigError, MCPClientError, PermissionError)):
        error = str(exc)[:MAX_ERROR_CHARACTERS]
    else:
        error = f"MCP diagnostic failed with {type(exc).__name__}."
    return {
        "ok": False,
        "status": "failed",
        "mcp_enabled": False,
        "config_files": [],
        "enabled_servers": [],
        "disabled_servers": [],
        "discovered_tools": [],
        "errors": [{"server": None, "stage": "config", "error": error}],
    }
