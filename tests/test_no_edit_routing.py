import json

import pytest

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig, RuntimeConfig, SubagentConfig
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.tools.registry import WRITE_TOOL_NAMES


class CommandCallingModel:
    def __init__(self, command, final_text="Command request handled."):
        self.command = command
        self.final_text = final_text
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": list(tools or []),
            }
        )
        if len(self.calls) == 1:
            return ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="explicit-command",
                        name="run_command",
                        arguments={"command": self.command},
                    ),
                ),
            )
        return ModelResponse(text=self.final_text)


def _schema_names(call):
    return {
        schema["function"]["name"]
        for schema in call["tools"]
    }


def _tool_result(model):
    tool_message = next(
        message
        for message in model.calls[-1]["messages"]
        if message["role"] == "tool"
    )
    return json.loads(tool_message["content"])


def test_no_edit_explicit_command_uses_local_runner_after_approval(
    monkeypatch,
    tmp_path,
):
    runner_calls = []
    approvals = []

    def fake_local(project_root, command, timeout_ms):
        runner_calls.append((project_root, command, timeout_ms))
        return {
            "ok": True,
            "runtime": "local",
            "command": command,
            "exit_code": 0,
            "stdout": "Python 3.12.0\n",
            "stderr": "",
            "duration_ms": 2,
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(
        "lunar_forge.tools.shell.run_local_command",
        fake_local,
    )
    model = CommandCallingModel(
        "python --version",
        final_text="Inspection phase blocked command execution.",
    )

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode="local"),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        "Use run_command to run python --version. Do not edit files.",
        tmp_path,
    )

    first_schema_names = _schema_names(model.calls[0])
    assert "run_command" in first_schema_names
    assert WRITE_TOOL_NAMES.isdisjoint(first_schema_names)
    assert runner_calls == [(tmp_path.resolve(), "python --version", 120000)]
    assert len(approvals) == 1
    assert approvals[0].tool_name == "run_command"
    assert "Inspection phase blocked command execution." not in output
    assert "Commands run:" in output
    assert "python --version: passed" in output
    assert "via run_command" in output
    assert "Subagents run:\n- tester" in output


def test_no_edit_explicit_command_uses_docker_runner_after_approval(
    monkeypatch,
    tmp_path,
):
    runner_calls = []
    approvals = []

    def fake_docker(
        project_root,
        command,
        timeout_ms,
        *,
        allow_network=False,
    ):
        runner_calls.append(
            (project_root, command, timeout_ms, allow_network)
        )
        return {
            "ok": True,
            "runtime": "docker",
            "command": command,
            "exit_code": 0,
            "stdout": "/workspace\n",
            "stderr": "",
            "duration_ms": 3,
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(
        "lunar_forge.tools.shell.run_docker_command",
        fake_docker,
    )
    model = CommandCallingModel("pwd")

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode="docker"),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        "Use run_command to run pwd. Do not edit files.",
        tmp_path,
    )

    first_schema_names = _schema_names(model.calls[0])
    assert "run_command" in first_schema_names
    assert WRITE_TOOL_NAMES.isdisjoint(first_schema_names)
    assert runner_calls == [(tmp_path.resolve(), "pwd", 120000, False)]
    assert len(approvals) == 1
    assert approvals[0].tool_name == "run_command"
    assert "Commands run:" in output
    assert "pwd: passed" in output
    assert "via run_command" in output
    assert "Subagents run:\n- tester" in output


def test_each_no_edit_command_call_uses_normal_approval_flow(
    monkeypatch,
    tmp_path,
):
    runner_calls = []
    approvals = []

    def fake_local(project_root, command, timeout_ms):
        runner_calls.append(command)
        return {
            "ok": True,
            "runtime": "local",
            "command": command,
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "duration_ms": 1,
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(
        "lunar_forge.tools.shell.run_local_command",
        fake_local,
    )

    class TwoCommandModel:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    text="",
                    tool_calls=(
                        ToolCall(
                            id="pwd",
                            name="run_command",
                            arguments={"command": "pwd"},
                        ),
                        ToolCall(
                            id="version",
                            name="run_command",
                            arguments={"command": "python --version"},
                        ),
                    ),
                )
            return ModelResponse(text="Both requested commands completed.")

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=TwoCommandModel(),
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        (
            "Use run_command to run pwd, then use run_command to run "
            "python --version. Do not edit files."
        ),
        tmp_path,
    )

    assert runner_calls == ["pwd", "python --version"]
    assert [request.tool_name for request in approvals] == [
        "run_command",
        "run_command",
    ]
    assert "pwd: passed" in output
    assert "python --version: passed" in output


@pytest.mark.parametrize("mode", ("plan", "no-command"))
def test_restricted_modes_block_explicit_command_before_approval(
    monkeypatch,
    tmp_path,
    mode,
):
    approvals = []

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Restricted mode must not reach a command runner")

    monkeypatch.setattr(
        "lunar_forge.tools.shell.run_local_command",
        unexpected_runner,
    )
    model = CommandCallingModel(
        "python --version",
        final_text=f"{mode} mode kept command execution blocked.",
    )

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(
                mode="no-command" if mode == "no-command" else "local"
            ),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        "Use run_command to run python --version. Do not edit files.",
        tmp_path,
        mode=mode,
    )

    assert "run_command" not in _schema_names(model.calls[0])
    assert WRITE_TOOL_NAMES.isdisjoint(_schema_names(model.calls[0]))
    assert _tool_result(model)["ok"] is False
    assert approvals == []
    assert "Commands run:" not in output
    assert "blocked" in output.casefold()


def test_prompt_level_no_command_blocks_explicit_tool_call(
    monkeypatch,
    tmp_path,
):
    approvals = []

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Prompt prohibition must not reach a command runner")

    monkeypatch.setattr(
        "lunar_forge.tools.shell.run_local_command",
        unexpected_runner,
    )
    model = CommandCallingModel("python --version")

    CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        (
            "Use run_command to run python --version. Do not edit files "
            "and do not run commands."
        ),
        tmp_path,
    )

    assert "run_command" not in _schema_names(model.calls[0])
    result = _tool_result(model)
    assert result["ok"] is False
    assert result["blocked_by_task_profile"] is True
    assert approvals == []


@pytest.mark.parametrize("runtime_mode", ("local", "docker"))
def test_dangerous_command_remains_blocked_in_no_edit_profile(
    monkeypatch,
    tmp_path,
    runtime_mode,
):
    approvals = []

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Dangerous command must not reach a runner")

    monkeypatch.setattr(
        (
            "lunar_forge.tools.shell.run_docker_command"
            if runtime_mode == "docker"
            else "lunar_forge.tools.shell.run_local_command"
        ),
        unexpected_runner,
    )
    model = CommandCallingModel(
        "sudo python --version",
        final_text="The dangerous command was blocked by safety policy.",
    )

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode=runtime_mode),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        (
            "Use run_command to run sudo python --version. "
            "Do not edit files."
        ),
        tmp_path,
    )

    assert "run_command" in _schema_names(model.calls[0])
    result = _tool_result(model)
    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert "blocked by safety policy" in result["error"]
    assert approvals == []
    assert "blocked by safety policy" in output
