import json
from datetime import datetime, timezone

from typer.testing import CliRunner

import lunar_forge.cli as cli_module
from lunar_forge.cli import app
from lunar_forge.config import AppConfig
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
        project_root=".",
    ):
        captured.update(
            url=url,
            screenshot=screenshot,
            checks=checks,
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
        "project_root": tmp_path.resolve(),
    }
