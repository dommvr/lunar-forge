from datetime import datetime, timezone

from typer.testing import CliRunner

import lunar_forge.cli as cli_module
from lunar_forge.cli import app
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
