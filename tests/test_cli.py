import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

import lunar_forge.cli as cli_module
import lunar_forge.workflows.browser_validation as browser_module
from lunar_forge.cli import app
from lunar_forge.config import AppConfig, RuntimeConfig
from lunar_forge.config import MCPRuntimeConfig, PluginRuntimeConfig
from lunar_forge.runtime.checkpoints import create_file_checkpoint


def _forbid_model_and_config(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Utility commands must not load config or model APIs")

    monkeypatch.setattr(cli_module, "load_config", unexpected)
    monkeypatch.setattr(cli_module, "run_agent", unexpected)


def test_checkpoints_and_sessions_commands_list_runtime_files(
    monkeypatch,
    tmp_path,
):
    _forbid_model_and_config(monkeypatch)
    source = tmp_path / "example.txt"
    source.write_text("original", encoding="utf-8")
    checkpoint = create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    sessions_directory = tmp_path / ".agent" / "sessions"
    sessions_directory.mkdir(parents=True)
    secret = "sk-cli-secret-value-123456"
    session = sessions_directory / "20260301T000000000000Z-abcdef12.jsonl"
    session.write_text(f'{{"api_key":"{secret}"}}\n', encoding="utf-8")
    runner = CliRunner()

    checkpoints_result = runner.invoke(
        app,
        ["checkpoints", "--project", str(tmp_path)],
    )
    sessions_result = runner.invoke(
        app,
        ["sessions", "--project", str(tmp_path)],
    )

    assert checkpoints_result.exit_code == 0
    assert checkpoint.parent.name in checkpoints_result.stdout
    assert sessions_result.exit_code == 0
    assert session.name in sessions_result.stdout
    assert secret not in sessions_result.stdout


def test_rollback_command_restores_without_model_or_shell(monkeypatch, tmp_path):
    _forbid_model_and_config(monkeypatch)
    source = tmp_path / "example.txt"
    source.write_text("checkpoint value", encoding="utf-8")
    checkpoint = create_file_checkpoint(
        tmp_path,
        source,
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    source.write_text("current value", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["rollback", "example.txt", "--project", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert source.read_text(encoding="utf-8") == "checkpoint value"
    assert checkpoint.relative_to(tmp_path).as_posix() in result.stdout


def test_rollback_command_clearly_reports_no_checkpoint(monkeypatch, tmp_path):
    _forbid_model_and_config(monkeypatch)

    result = CliRunner().invoke(
        app,
        ["rollback", "missing.txt", "--project", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "No checkpoint exists for missing.txt" in result.stderr


def test_resume_summary_only_is_redacted_and_model_free(monkeypatch, tmp_path):
    _forbid_model_and_config(monkeypatch)
    sessions_directory = tmp_path / ".agent" / "sessions"
    sessions_directory.mkdir(parents=True)
    secret = "sk-resume-cli-secret-123456789"
    session = sessions_directory / "20260720T100000000000Z-abcdef12.jsonl"
    session.write_text(
        json.dumps(
            {
                "timestamp": "2026-07-20T10:00:00Z",
                "event": "user_prompt",
                "data": {"prompt": f"api_key={secret}"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "resume",
            "abcdef12",
            "--project",
            str(tmp_path),
            "--summary-only",
        ],
    )

    assert result.exit_code == 0
    assert "Session:" in result.stdout
    assert "Historical tool results" in result.stdout
    assert secret not in result.stdout
    assert len(list(sessions_directory.glob("*.jsonl"))) == 1


def test_resume_command_passes_inert_history_to_agent(monkeypatch, tmp_path):
    sessions_directory = tmp_path / ".agent" / "sessions"
    sessions_directory.mkdir(parents=True)
    session = sessions_directory / "previous.jsonl"
    session.write_text(
        json.dumps(
            {
                "timestamp": "2026-07-20T10:00:00Z",
                "event": "tool_result",
                "data": {"name": "read_file", "result": {"ok": True}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    captured = {}

    monkeypatch.setattr(cli_module, "load_config", lambda *args, **kwargs: AppConfig())

    def fake_run_agent(prompt, project_root, **kwargs):
        captured.update(
            {
                "prompt": prompt,
                "project_root": project_root,
                **kwargs,
            }
        )
        return "Resumed safely."

    monkeypatch.setattr(cli_module, "run_agent", fake_run_agent)

    result = CliRunner().invoke(
        app,
        [
            "resume",
            session.name,
            "--project",
            str(tmp_path),
            "--prompt",
            "Continue reviewing",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == "Resumed safely."
    assert captured["prompt"] == "Continue reviewing"
    assert captured["resumed_from"] == session.relative_to(tmp_path).as_posix()
    assert all(
        message["role"] != "tool" for message in captured["resume_messages"]
    )


def test_subagents_flag_is_available_and_sets_cli_override():
    runner = CliRunner()

    for command in ("run", "new", "resume"):
        result = runner.invoke(app, [command, "--help"])

        assert result.exit_code == 0
        assert "--subagents" in result.stdout

    overrides = cli_module._runtime_overrides(
        False,
        False,
        False,
        True,
    )
    assert overrides == {"subagents": {"enabled": True}}


def test_browser_setup_is_model_free_lists_commands_and_uses_approvals(
    monkeypatch,
    tmp_path,
):
    calls = []

    def unexpected_model(*args, **kwargs):
        raise AssertionError("Browser setup must not use model APIs")

    def fake_command_runner(
        project_root,
        command,
        timeout_ms,
        *,
        runtime_mode,
        allow_network,
    ):
        calls.append(command)
        return {
            "ok": True,
            "command": command,
            "exit_code": 0,
            "stdout": "installed\n",
            "stderr": "",
            "truncated": False,
        }

    monkeypatch.setattr(cli_module, "run_agent", unexpected_model)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda project_root: AppConfig(),
    )
    monkeypatch.setattr(browser_module, "run_command", fake_command_runner)

    result = CliRunner().invoke(
        app,
        ["browser-setup", "--project", str(tmp_path)],
        input="y\ny\n",
    )

    assert result.exit_code == 0
    assert calls == [
        'python -m pip install -e ".[browser]"',
        "python -m playwright install chromium",
    ]
    assert result.stdout.index("Browser setup will run these commands:") < (
        result.stdout.index("Allow? [y/N]")
    )
    for command in calls:
        assert f"- {command}" in result.stdout
    assert '"status": "passed"' in result.stdout


def test_browser_setup_honors_configured_no_command_runtime(
    monkeypatch,
    tmp_path,
):
    def unexpected(*args, **kwargs):
        raise AssertionError("No-command setup must not use model or command APIs")

    monkeypatch.setattr(cli_module, "run_agent", unexpected)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda project_root: AppConfig(
            runtime=RuntimeConfig(mode="no-command"),
        ),
    )
    monkeypatch.setattr(browser_module, "run_command", unexpected)

    result = CliRunner().invoke(
        app,
        ["browser-setup", "--project", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "No-command mode blocks command execution" in result.stdout
    assert "Allow? [y/N]" not in result.stdout


def test_browser_validate_command_is_model_free_and_returns_json(
    monkeypatch,
    tmp_path,
):
    _forbid_model_and_config(monkeypatch)
    captured = {}
    screenshot_path = ".agent/artifacts/browser/browser-test.png"

    def fake_browser_validation(
        url,
        screenshot=True,
        checks=None,
        *,
        full_page=False,
        width=1280,
        height=720,
        project_root=".",
    ):
        captured.update(
            url=url,
            screenshot=screenshot,
            checks=checks,
            full_page=full_page,
            width=width,
            height=height,
            project_root=project_root,
        )
        return {
            "ok": True,
            "status": "passed",
            "title": "CLI App",
            "final_url": f"{url}/ready",
            "console_errors": [],
            "failed_requests": [],
            "screenshot_path": screenshot_path,
            "checks": [{"selector": "#root", "passed": True}],
            "truncated": False,
        }

    monkeypatch.setattr(
        cli_module,
        "run_browser_validation",
        fake_browser_validation,
    )

    result = CliRunner().invoke(
        app,
        [
            "browser-validate",
            "http://127.0.0.1:5173",
            "--project",
            str(tmp_path),
            "--check",
            "#root",
        ],
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["status"] == "passed"
    assert output["title"] == "CLI App"
    assert output["final_url"] == "http://127.0.0.1:5173/ready"
    assert output["screenshot_path"] == screenshot_path
    assert output["console_errors"] == []
    assert output["failed_requests"] == []
    assert captured == {
        "url": "http://127.0.0.1:5173",
        "screenshot": True,
        "checks": ["#root"],
        "full_page": False,
        "width": 1280,
        "height": 720,
        "project_root": tmp_path.resolve(),
    }


def test_browser_validate_command_accepts_full_page_and_viewport(
    monkeypatch,
    tmp_path,
):
    _forbid_model_and_config(monkeypatch)
    captured = {}

    def fake_browser_validation(url, **kwargs):
        captured.update(url=url, **kwargs)
        return {
            "ok": True,
            "status": "passed",
            "title": "Long page",
            "final_url": url,
            "console_errors": [],
            "failed_requests": [],
            "screenshot_path": ".agent/artifacts/browser/browser-long.png",
            "checks": [],
            "truncated": False,
        }

    monkeypatch.setattr(
        cli_module,
        "run_browser_validation",
        fake_browser_validation,
    )

    result = CliRunner().invoke(
        app,
        [
            "browser-validate",
            "http://localhost:5173",
            "--project",
            str(tmp_path),
            "--full-page",
            "--width",
            "1440",
            "--height",
            "1200",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "url": "http://localhost:5173",
        "screenshot": True,
        "checks": None,
        "full_page": True,
        "width": 1440,
        "height": 1200,
        "project_root": tmp_path.resolve(),
    }


def test_browser_validate_managed_server_mode_is_model_free_and_routed(
    monkeypatch,
    tmp_path,
):
    _forbid_model_and_config(monkeypatch)
    captured = {}

    monkeypatch.setattr(
        cli_module,
        "run_browser_validation",
        lambda *args, **kwargs: pytest.fail(
            "Managed mode must not use direct browser validation routing"
        ),
    )

    def fake_managed_validation(command, url, **kwargs):
        captured.update(command=command, url=url, **kwargs)
        return {
            "ok": True,
            "status": "passed",
            "title": "Managed app",
            "final_url": url,
            "console_errors": [],
            "failed_requests": [],
            "screenshot_path": None,
            "checks": [],
            "truncated": False,
            "managed_server": {
                "started": True,
                "ready": True,
                "stopped": True,
            },
        }

    monkeypatch.setattr(
        cli_module,
        "run_managed_browser_validation",
        fake_managed_validation,
    )

    result = CliRunner().invoke(
        app,
        [
            "browser-validate",
            "--serve",
            "npm run dev",
            "--url",
            "http://localhost:5173",
            "--project",
            str(tmp_path),
            "--full-page",
            "--width",
            "1440",
            "--height",
            "1200",
            "--startup-timeout-ms",
            "45000",
        ],
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["managed_server"]["stopped"] is True
    assert captured == {
        "command": "npm run dev",
        "url": "http://localhost:5173",
        "screenshot": True,
        "checks": None,
        "full_page": True,
        "width": 1440,
        "height": 1200,
        "startup_timeout_ms": 45000,
        "project_root": tmp_path.resolve(),
    }


def test_mcp_list_command_is_model_free_and_returns_diagnostics(
    monkeypatch,
    tmp_path,
):
    def unexpected_model(*args, **kwargs):
        raise AssertionError("MCP diagnostics must not use model APIs")

    captured = {}
    monkeypatch.setattr(cli_module, "run_agent", unexpected_model)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda project_root: AppConfig(mcp=MCPRuntimeConfig(enabled=True)),
    )

    def fake_diagnostic(project_root, *, globally_enabled):
        captured.update(
            project_root=project_root,
            globally_enabled=globally_enabled,
        )
        return {
            "ok": True,
            "status": "passed",
            "mcp_enabled": True,
            "config_files": [
                {
                    "scope": "project",
                    "path": str(tmp_path / ".agent/mcp.yaml"),
                    "loaded": True,
                }
            ],
            "enabled_servers": ["playwright"],
            "disabled_servers": [],
            "discovered_tools": [
                {
                    "server": "playwright",
                    "name": "mcp.playwright.browser_navigate",
                    "read_only": False,
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr(cli_module, "build_mcp_diagnostic", fake_diagnostic)

    result = CliRunner().invoke(
        app,
        ["mcp", "list", "--project", str(tmp_path)],
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["enabled_servers"] == ["playwright"]
    assert output["discovered_tools"][0]["name"] == (
        "mcp.playwright.browser_navigate"
    )
    assert captured == {
        "project_root": tmp_path.resolve(),
        "globally_enabled": True,
    }


def test_plugins_list_command_is_model_free_and_returns_diagnostics(
    monkeypatch,
    tmp_path,
):
    def unexpected_model(*args, **kwargs):
        raise AssertionError("Plugin diagnostics must not use model APIs")

    captured = {}
    monkeypatch.setattr(cli_module, "run_agent", unexpected_model)
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda project_root: AppConfig(
            plugins=PluginRuntimeConfig(enabled=True)
        ),
    )

    def fake_diagnostic(project_root, *, globally_enabled):
        captured.update(
            project_root=project_root,
            globally_enabled=globally_enabled,
        )
        return {
            "ok": True,
            "status": "passed",
            "plugins_enabled": True,
            "config_files": [],
            "plugins_config": {
                "path": str(tmp_path / ".agent/plugins.yaml"),
                "loaded": True,
            },
            "enabled_plugins": ["example"],
            "disabled_plugins": [],
            "plugins": [],
            "discovered_tools": [
                {
                    "plugin": "example",
                    "internal_tool_name": "example.echo",
                    "model_tool_name": "example_echo",
                }
            ],
            "errors": [],
            "truncated": False,
        }

    monkeypatch.setattr(
        cli_module,
        "build_plugin_diagnostic",
        fake_diagnostic,
    )

    result = CliRunner().invoke(
        app,
        ["plugins", "list", "--project", str(tmp_path)],
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["enabled_plugins"] == ["example"]
    assert output["discovered_tools"][0] == {
        "plugin": "example",
        "internal_tool_name": "example.echo",
        "model_tool_name": "example_echo",
    }
    assert captured == {
        "project_root": tmp_path.resolve(),
        "globally_enabled": True,
    }
