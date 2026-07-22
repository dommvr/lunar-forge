import io
import json
import subprocess
import threading

import pytest

import lunar_forge.mcp.config as mcp_config_module
from lunar_forge.mcp.client import (
    MCPClientError,
    StdioMCPTransport,
    build_mcp_diagnostic,
)
from lunar_forge.mcp.config import MCPServerConfig


class RecordingInput:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, value):
        self.writes.append(value)
        return len(value)

    def flush(self):
        return None

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, messages, *, stdout=None):
        self.stdin = RecordingInput()
        encoded_messages = "".join(
            f"{json.dumps(message)}\n" for message in messages
        )
        self.stdout = io.StringIO(encoded_messages) if stdout is None else stdout
        self.stderr = io.StringIO("")
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def _initialize_response():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0.0"},
        },
    }


def _server(*, env=None):
    return MCPServerConfig(
        name="playwright",
        command="npx",
        args=("-y", "@playwright/mcp@latest", "--isolated"),
        env={} if env is None else env,
        enabled=True,
    )


def test_stdio_transport_initializes_discovers_calls_and_uses_shell_false(
    tmp_path,
):
    process = FakeProcess(
        [
            _initialize_response(),
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "browser_navigate",
                            "description": "Navigate a page.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"url": {"type": "string"}},
                            },
                        }
                    ]
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {
                    "content": [{"type": "text", "text": "navigated"}],
                    "isError": False,
                },
            },
        ]
    )
    captured = {}

    def fake_popen(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return process

    resolved_npx = r"C:\Program Files\nodejs\npx.cmd"
    transport = StdioMCPTransport(
        _server(),
        tmp_path,
        _popen_factory=fake_popen,
        _executable_resolver=lambda executable, cwd: resolved_npx,
        _environ={"PATH": r"C:\Program Files\nodejs"},
    )

    tools = transport.list_tools()
    result = transport.call_tool(
        "browser_navigate",
        {"url": "http://127.0.0.1:5173"},
    )
    transport.close()

    assert captured["arguments"] == [
        resolved_npx,
        "-y",
        "@playwright/mcp@latest",
        "--isolated",
    ]
    assert captured["cwd"] == tmp_path.resolve()
    assert captured["shell"] is False
    assert tools[0]["name"] == "browser_navigate"
    assert result["content"][0]["text"] == "navigated"
    requests = [json.loads(value) for value in process.stdin.writes]
    assert [request.get("method") for request in requests] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert requests[0]["params"]["clientInfo"]["name"] == "lunar-forge"
    assert process.stdin.closed is True


def test_stdio_transport_resolves_configured_environment_without_logging_it(
    tmp_path,
):
    secret = "super-secret-transport-value"
    process = FakeProcess(
        [
            _initialize_response(),
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": []},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {
                    "content": [{"type": "text", "text": secret}],
                },
            },
        ]
    )
    captured = {}

    def fake_popen(arguments, **kwargs):
        captured.update(kwargs)
        return process

    transport = StdioMCPTransport(
        _server(env={"SERVER_TOKEN": "HOST_TOKEN"}),
        tmp_path,
        _popen_factory=fake_popen,
        _executable_resolver=lambda executable, cwd: "npx.cmd",
        _environ={"PATH": "tools", "HOST_TOKEN": secret},
    )
    transport.list_tools()
    result = transport.call_tool("echo", {})
    transport.close()

    assert captured["env"]["SERVER_TOKEN"] == secret
    assert "HOST_TOKEN" not in captured["env"]
    assert secret not in repr(transport)
    assert secret not in json.dumps(result)
    assert result["content"][0]["text"] == "[REDACTED]"


def test_stdio_start_error_does_not_expose_exception_message(tmp_path):
    secret = "private-process-error"

    def broken_popen(*args, **kwargs):
        raise OSError(secret)

    with pytest.raises(MCPClientError) as raised:
        StdioMCPTransport(
            _server(),
            tmp_path,
            _popen_factory=broken_popen,
            _executable_resolver=lambda executable, cwd: "npx.cmd",
            _environ={"PATH": "tools"},
        )

    assert "OSError" in str(raised.value)
    assert secret not in str(raised.value)


class BlockingOutput:
    def __init__(self):
        self.release = threading.Event()

    def readline(self, size=-1):
        self.release.wait(timeout=1)
        return ""


def test_stdio_initialize_has_bounded_startup_timeout(tmp_path):
    output = BlockingOutput()
    process = FakeProcess([], stdout=output)

    with pytest.raises(MCPClientError, match="timed out after 10 ms"):
        StdioMCPTransport(
            _server(),
            tmp_path,
            startup_timeout_ms=10,
            _popen_factory=lambda *args, **kwargs: process,
            _executable_resolver=lambda executable, cwd: "npx.cmd",
            _environ={"PATH": "tools"},
        )

    output.release.set()


def test_stdio_transport_rejects_oversized_server_message(tmp_path):
    from lunar_forge.mcp.client import MAX_RPC_MESSAGE_CHARACTERS

    process = FakeProcess(
        [],
        stdout=io.StringIO("x" * (MAX_RPC_MESSAGE_CHARACTERS + 1) + "\n"),
    )

    with pytest.raises(MCPClientError, match="size limit"):
        StdioMCPTransport(
            _server(),
            tmp_path,
            _popen_factory=lambda *args, **kwargs: process,
            _executable_resolver=lambda executable, cwd: "npx.cmd",
            _environ={"PATH": "tools"},
        )


class DiagnosticTransport:
    def list_tools(self):
        return [
            {
                "name": "browser_navigate",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ]

    def call_tool(self, name, arguments):
        return {"content": []}


def test_diagnostic_honors_global_switch_and_reports_namespaced_tools(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        mcp_config_module.Path,
        "home",
        classmethod(lambda cls: tmp_path / "home"),
    )
    config_dir = tmp_path / ".agent"
    config_dir.mkdir()
    (config_dir / "mcp.yaml").write_text(
        """
servers:
  playwright:
    command: npx.cmd
    args: [-y, "@playwright/mcp@latest", "--isolated"]
    enabled: true
  disabled:
    command: unused
    enabled: false
""".lstrip(),
        encoding="utf-8",
    )
    factory_calls = []

    disabled = build_mcp_diagnostic(
        tmp_path,
        globally_enabled=False,
        transport_factory=lambda server: factory_calls.append(server),
    )
    enabled = build_mcp_diagnostic(
        tmp_path,
        globally_enabled=True,
        transport_factory=lambda server: DiagnosticTransport(),
    )

    assert disabled["status"] == "disabled"
    assert disabled["enabled_servers"] == ["playwright"]
    assert disabled["disabled_servers"] == ["disabled"]
    assert disabled["discovered_tools"] == []
    assert factory_calls == []
    assert enabled["ok"] is True
    assert enabled["discovered_tools"] == [
        {
            "server": "playwright",
            "name": "mcp.playwright.browser_navigate",
            "read_only": False,
        }
    ]
    assert enabled["config_files"][1]["loaded"] is True


def test_stdio_close_escalates_from_wait_to_terminate(tmp_path):
    class SlowProcess(FakeProcess):
        def __init__(self):
            super().__init__([_initialize_response()])
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("npx.cmd", timeout)
            self.returncode = 0
            return 0

    process = SlowProcess()
    transport = StdioMCPTransport(
        _server(),
        tmp_path,
        _popen_factory=lambda *args, **kwargs: process,
        _executable_resolver=lambda executable, cwd: "npx.cmd",
        _environ={"PATH": "tools"},
    )

    transport.close()

    assert process.terminated is True
    assert process.killed is False
