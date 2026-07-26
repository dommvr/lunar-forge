import json

import pytest

import lunar_forge.workflows.validation as validation_module
from lunar_forge.permissions import PermissionLevel, PermissionRequest
from lunar_forge.tools.registry import create_tool_registry
from lunar_forge.workflows.validation import run_validation


def _record_successful_commands(monkeypatch):
    commands: list[str] = []

    def successful_runner(project_root, command, timeout_ms):
        commands.append(command)
        return {
            "ok": True,
            "command": command,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 1,
            "truncated": False,
            "timed_out": False,
        }

    monkeypatch.setattr(
        validation_module,
        "run_local_command",
        successful_runner,
    )
    return commands


def test_python_validation_always_chooses_compileall_but_not_unneeded_pytest(
    monkeypatch,
    tmp_path,
):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n",
        encoding="utf-8",
    )
    executed = _record_successful_commands(monkeypatch)

    result = run_validation(tmp_path)

    assert result["ok"] is True
    assert result["commands"] == ["python -B -m compileall ."]
    assert executed == result["commands"]
    json.dumps(result)


@pytest.mark.parametrize("pytest_marker", ("tests", "pytest.ini", "pyproject"))
def test_python_validation_includes_pytest_for_tests_or_config(
    monkeypatch,
    tmp_path,
    pytest_marker,
):
    pyproject_content = "[project]\nname = 'demo'\n"
    if pytest_marker == "pyproject":
        pyproject_content += "\n[tool.pytest.ini_options]\naddopts = '-q'\n"
    (tmp_path / "pyproject.toml").write_text(
        pyproject_content,
        encoding="utf-8",
    )
    if pytest_marker == "tests":
        (tmp_path / "tests").mkdir()
    elif pytest_marker == "pytest.ini":
        (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    _record_successful_commands(monkeypatch)

    result = run_validation(tmp_path)

    assert result["commands"] == ["python -B -m compileall .", "pytest"]


@pytest.mark.parametrize(
    ("lock_file", "package_manager", "expected"),
    (
        (
            "package-lock.json",
            "npm",
            ["npm test", "npm run lint", "npm run build"],
        ),
        (
            "pnpm-lock.yaml",
            "pnpm",
            ["pnpm test", "pnpm lint", "pnpm build"],
        ),
        (
            "yarn.lock",
            "yarn",
            ["yarn test", "yarn lint", "yarn build"],
        ),
    ),
)
def test_node_validation_uses_detected_package_manager(
    monkeypatch,
    tmp_path,
    lock_file,
    package_manager,
    expected,
):
    package_json = {
        "scripts": {
            "test": "test-command",
            "lint": "lint-command",
            "build": "build-command",
            "start": "start-command",
        }
    }
    (tmp_path / "package.json").write_text(
        json.dumps(package_json),
        encoding="utf-8",
    )
    (tmp_path / lock_file).write_text("", encoding="utf-8")
    executed = _record_successful_commands(monkeypatch)

    result = run_validation(tmp_path)

    assert result["commands"] == expected
    assert executed == expected
    assert package_manager in expected[0]


def test_no_detected_validation_commands_is_clear_no_op(monkeypatch, tmp_path):
    def unexpected_runner(project_root, command, timeout_ms):
        raise AssertionError("No command should run")

    monkeypatch.setattr(
        validation_module,
        "run_local_command",
        unexpected_runner,
    )

    result = run_validation(tmp_path)

    assert result == {
        "ok": True,
        "message": "No validation commands were found for this project.",
        "commands": [],
        "results": [],
    }


def test_validation_fails_when_any_command_fails(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()

    def runner(project_root, command, timeout_ms):
        return {"ok": command != "pytest", "command": command}

    monkeypatch.setattr(validation_module, "run_local_command", runner)

    result = run_validation(tmp_path)

    assert result["ok"] is False
    assert result["commands"] == ["python -B -m compileall .", "pytest"]
    assert "failed" in result["message"]


def test_validation_tool_requires_execution_permission_and_plan_hides_it(tmp_path):
    plan_registry = create_tool_registry(tmp_path, mode="plan")
    requests: list[PermissionRequest] = []

    def deny(request: PermissionRequest) -> bool:
        requests.append(request)
        return False

    default_registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=deny,
    )

    assert "run_validation" not in plan_registry.names()
    assert "run_validation" in default_registry.names()

    result = default_registry.execute("run_validation", {})

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert len(requests) == 1
    assert requests[0].tool_name == "run_validation"
    assert requests[0].permission is PermissionLevel.EXECUTE
    assert requests[0].description == "Run detected validation commands."


@pytest.mark.parametrize(
    "command",
    ("python", "python.exe", "py", "py.exe"),
)
def test_validation_command_candidates_reject_bare_python_interpreters(command):
    assert validation_module._is_validation_command_candidate(command) is False


@pytest.mark.parametrize(
    "command",
    ("python", "python.exe", "py", "py.exe"),
)
def test_model_suggested_bare_interpreters_are_denied_before_approval(
    tmp_path,
    command,
):
    approval_requests = []
    registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=lambda request: approval_requests.append(request) or True,
    )

    result = registry.execute("run_command", {"command": command})

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert "not meaningful checks" in result["error"]
    assert approval_requests == []


@pytest.mark.parametrize(
    "command",
    (
        "python -m pytest",
        "python -B -m compileall .",
        "python app.py",
    ),
)
def test_validation_command_candidates_allow_meaningful_python_commands(command):
    assert validation_module._is_validation_command_candidate(command) is True


def test_single_python_file_prefers_compileall_validation(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    executed = _record_successful_commands(monkeypatch)

    result = run_validation(tmp_path)

    assert result["commands"] == ["python -B -m compileall ."]
    assert executed == result["commands"]
