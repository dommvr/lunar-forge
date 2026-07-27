import json

import pytest

from lunar_forge.agent import CodeAgent
from lunar_forge.config import (
    AppConfig,
    ModelConfig,
    ReasoningConfig,
    RuntimeConfig,
    SubagentConfig,
)
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
            model=ModelConfig(
                reasoning=ReasoningConfig(effort="high"),
            ),
            runtime=RuntimeConfig(mode="local"),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        (
            "Use run_command to run python --version. Then include stdout. "
            "Do not edit files."
        ),
        tmp_path,
    )

    first_schema_names = _schema_names(model.calls[0])
    assert "run_command" in first_schema_names
    assert WRITE_TOOL_NAMES.isdisjoint(first_schema_names)
    assert runner_calls == [(tmp_path.resolve(), "python --version", 120000)]
    assert len(approvals) == 1
    assert approvals[0].tool_name == "run_command"
    assert approvals[0].description.startswith(
        "Run local command: python --version\n\n"
    )
    assert "Inspection phase blocked command execution." not in output
    assert "Commands run:" in output
    assert "python --version: passed" in output
    assert "via run_command" in output
    assert "stdout:\n    Python 3.12.0" in output
    assert "Subagents run:\n- tester" in output
    assert "Local commands run as your user account" not in output


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
            model=ModelConfig(
                reasoning=ReasoningConfig(effort="xhigh"),
            ),
            runtime=RuntimeConfig(mode="docker"),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        "Use run_command to run pwd. Then include stdout. Do not edit files.",
        tmp_path,
    )

    first_schema_names = _schema_names(model.calls[0])
    assert "run_command" in first_schema_names
    assert WRITE_TOOL_NAMES.isdisjoint(first_schema_names)
    assert runner_calls == [(tmp_path.resolve(), "pwd", 120000, False)]
    assert len(approvals) == 1
    assert approvals[0].tool_name == "run_command"
    assert approvals[0].description == (
        "Run Docker command: pwd\n"
        "This runs inside lunar-forge-sandbox with the project mounted at "
        "/workspace."
    )
    assert "Commands run:" in output
    assert "pwd: passed" in output
    assert "via run_command" in output
    assert "stdout:\n    /workspace" in output
    assert "Subagents run:\n- tester" in output


@pytest.mark.parametrize("command", ("python --version", "python -V"))
def test_docker_python_version_inspection_runs_after_approval(
    monkeypatch,
    tmp_path,
    command,
):
    runner_calls = []
    approvals = []

    def fake_docker(
        project_root,
        selected_command,
        timeout_ms,
        *,
        allow_network=False,
    ):
        runner_calls.append(selected_command)
        return {
            "ok": True,
            "runtime": "docker",
            "command": selected_command,
            "exit_code": 0,
            "stdout": "Python 3.12.0\n",
            "stderr": "",
            "duration_ms": 1,
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(
        "lunar_forge.tools.shell.run_docker_command",
        fake_docker,
    )

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode="docker"),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=CommandCallingModel(command),
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        f"Use run_command to run {command}. Do not edit files.",
        tmp_path,
    )

    assert runner_calls == [command]
    assert len(approvals) == 1
    assert approvals[0].description.startswith(
        f"Run Docker command: {command}\n"
    )
    assert f"{command}: passed" in output


def test_local_python_help_inspection_runs_after_approval(
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
            "stdout": "usage: python [option]\n",
            "stderr": "",
            "duration_ms": 1,
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(
        "lunar_forge.tools.shell.run_local_command",
        fake_local,
    )

    CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode="local"),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=CommandCallingModel("python --help"),
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        "Use run_command to run python --help. Do not edit files.",
        tmp_path,
    )

    assert runner_calls == ["python --help"]
    assert len(approvals) == 1
    assert approvals[0].description.startswith(
        "Run local command: python --help\n\n"
    )


@pytest.mark.parametrize("runtime_mode", ("local", "docker"))
def test_explicit_bare_python_is_blocked_before_approval_or_dispatch(
    monkeypatch,
    tmp_path,
    runtime_mode,
):
    approvals = []

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Bare Python must not reach a command runner")

    monkeypatch.setattr(
        (
            "lunar_forge.tools.shell.run_docker_command"
            if runtime_mode == "docker"
            else "lunar_forge.tools.shell.run_local_command"
        ),
        unexpected_runner,
    )
    model = CommandCallingModel(
        "python",
        final_text="No command ran because bare Python is blocked.",
    )

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode=runtime_mode),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        "Use run_command to run python. Do not edit files.",
        tmp_path,
    )

    result = _tool_result(model)
    assert result["ok"] is False
    assert "Bare Python interpreter" in result["error"]
    assert approvals == []
    assert "Commands run:" not in output
    assert "No command ran" in output


@pytest.mark.parametrize("runtime_mode", ("local", "docker"))
def test_explicit_run_command_preserves_literal_instead_of_compileall(
    monkeypatch,
    tmp_path,
    runtime_mode,
):
    approvals = []

    def unexpected_runner(*args, **kwargs):
        raise AssertionError("Blocked literal command must not be rewritten")

    monkeypatch.setattr(
        (
            "lunar_forge.tools.shell.run_docker_command"
            if runtime_mode == "docker"
            else "lunar_forge.tools.shell.run_local_command"
        ),
        unexpected_runner,
    )
    model = CommandCallingModel(
        "python -B -m compileall .",
        final_text="No command ran because bare Python is blocked.",
    )

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode=runtime_mode),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        "Use run_command to run python. Do not edit files.",
        tmp_path,
    )

    result = _tool_result(model)
    assert result["ok"] is False
    assert "Bare Python interpreter" in result["error"]
    assert result.get("command") != "python -B -m compileall ."
    assert approvals == []
    assert "compileall" not in output


@pytest.mark.parametrize("runtime_mode", ("local", "docker"))
def test_explicit_run_command_preserves_safe_requested_command_string(
    monkeypatch,
    tmp_path,
    runtime_mode,
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
            "stdout": "Python 3.12.0\n",
            "stderr": "",
            "duration_ms": 1,
            "timed_out": False,
            "truncated": False,
        }

    def fake_docker(
        project_root,
        command,
        timeout_ms,
        *,
        allow_network=False,
    ):
        result = fake_local(project_root, command, timeout_ms)
        result["runtime"] = "docker"
        return result

    monkeypatch.setattr(
        (
            "lunar_forge.tools.shell.run_docker_command"
            if runtime_mode == "docker"
            else "lunar_forge.tools.shell.run_local_command"
        ),
        fake_docker if runtime_mode == "docker" else fake_local,
    )
    model = CommandCallingModel("python -B -m compileall .")

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode=runtime_mode),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=model,
        approval_callback=lambda request: approvals.append(request) or True,
    ).run(
        "Use run_command to run python --version. Do not edit files.",
        tmp_path,
    )

    assert runner_calls == ["python --version"]
    assert len(approvals) == 1
    assert "python --version" in approvals[0].description
    assert "python -B -m compileall ." not in approvals[0].description
    assert "python --version: passed" in output


def test_explicit_command_stdout_is_bounded(monkeypatch, tmp_path):
    def fake_local(project_root, command, timeout_ms):
        return {
            "ok": True,
            "runtime": "local",
            "command": command,
            "exit_code": 0,
            "stdout": "x" * 10_000,
            "stderr": "",
            "duration_ms": 1,
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(
        "lunar_forge.tools.shell.run_local_command",
        fake_local,
    )

    output = CodeAgent(
        AppConfig(
            runtime=RuntimeConfig(mode="local", project_trust="trusted"),
            subagents=SubagentConfig(enabled=True),
        ),
        model_client=CommandCallingModel('python -c "print(\'x\')"'),
        approval_callback=lambda request: True,
    ).run(
        (
            "Use run_command to run the requested Python command and include "
            "stdout. Do not edit files."
        ),
        tmp_path,
    )

    assert "stdout:" in output
    assert "...[stdout truncated]" in output
    assert output.count("x") < 5_000


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
        AppConfig(
            runtime=RuntimeConfig(
                mode="local",
                project_trust="trusted",
            ),
            subagents=SubagentConfig(enabled=True),
        ),
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
    assert "not OS-level isolation" in approvals[0].description
    assert approvals[1].description == (
        "Run local command: python --version."
    )
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
