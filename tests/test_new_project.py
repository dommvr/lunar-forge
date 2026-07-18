from typer.testing import CliRunner

import lunar_forge.workflows.new_project as new_project_module
from lunar_forge.cli import app
from lunar_forge.permissions import PermissionLevel, PermissionRequest
from lunar_forge.workflows.new_project import run_new_project, select_template


def test_new_command_exists():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "new" in result.stdout
    assert "Create a small starter project" in result.stdout


def test_empty_project_can_receive_static_html_files(tmp_path):
    requests: list[PermissionRequest] = []

    def approve(request: PermissionRequest) -> bool:
        requests.append(request)
        return True

    result = run_new_project(
        "Build a simple website for a local bakery",
        tmp_path,
        approval_callback=approve,
    )

    assert result["ok"] is True
    assert result["template"] == "static_html"
    assert result["changed_files"] == ["index.html", "styles.css", "README.md"]
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "styles.css").is_file()
    assert (tmp_path / "README.md").is_file()
    assert "http://localhost:8000" in " ".join(result["run_instructions"])
    assert [request.permission for request in requests] == [
        PermissionLevel.WRITE,
        PermissionLevel.WRITE,
        PermissionLevel.WRITE,
    ]
    assert (tmp_path / result["session_log"]).is_file()


def test_empty_project_can_receive_python_tkinter_files(tmp_path):
    result = run_new_project(
        "Build a calculator app in Python with UI",
        tmp_path,
        approval_callback=lambda request: True,
    )

    assert result["ok"] is True
    assert result["template"] == "python_tkinter"
    assert result["changed_files"] == ["app.py", "README.md"]
    app_source = (tmp_path / "app.py").read_text(encoding="utf-8")
    assert "import tkinter as tk" in app_source
    assert "python app.py" in result["run_instructions"][0]
    assert not (tmp_path / "requirements.txt").exists()


def test_non_empty_project_is_rejected_without_template_changes(tmp_path):
    original = tmp_path / "existing.txt"
    original.write_text("keep me", encoding="utf-8")

    def unexpected_approval(request: PermissionRequest) -> bool:
        raise AssertionError("Non-empty projects must not request scaffold approval")

    result = run_new_project(
        "Build a simple website",
        tmp_path,
        approval_callback=unexpected_approval,
    )

    assert result["ok"] is False
    assert "not empty" in result["message"]
    assert original.read_text(encoding="utf-8") == "keep me"
    assert not (tmp_path / "index.html").exists()
    assert not (tmp_path / ".agent").exists()


def test_plan_mode_selects_template_without_writing(tmp_path):
    def unexpected_approval(request: PermissionRequest) -> bool:
        raise AssertionError("Plan mode must not request write approval")

    result = run_new_project(
        "Build a calculator app in Python with UI",
        tmp_path,
        mode="plan",
        approval_callback=unexpected_approval,
    )

    assert result["ok"] is True
    assert result["planned"] is True
    assert result["template"] == "python_tkinter"
    assert result["changed_files"] == []
    assert result["commands_run"] == []
    assert list(tmp_path.iterdir()) == []


def test_vite_scaffolding_requires_command_approval(tmp_path):
    requests: list[PermissionRequest] = []

    def deny(request: PermissionRequest) -> bool:
        requests.append(request)
        return False

    result = run_new_project(
        "Build a Vite React portfolio",
        tmp_path,
        approval_callback=deny,
    )

    assert result["ok"] is False
    assert result["template"] == "vite_react"
    assert result["commands_run"] == []
    assert len(requests) == 1
    assert requests[0].tool_name == "run_command"
    assert requests[0].permission is PermissionLevel.EXECUTE
    assert "npm create vite@latest" in requests[0].description
    assert result["command_results"][0]["permission_denied"] is True


def test_vite_plan_contains_separately_gated_scaffold_and_install(monkeypatch, tmp_path):
    commands: list[str] = []

    class SuccessfulRegistry:
        def execute(self, name, arguments):
            assert name == "run_command"
            commands.append(arguments["command"])
            return {
                "ok": True,
                "command": arguments["command"],
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "duration_ms": 1,
                "truncated": False,
            }

    monkeypatch.setattr(
        new_project_module,
        "create_tool_registry",
        lambda *args, **kwargs: SuccessfulRegistry(),
    )

    result = run_new_project("Build a React app with Vite", tmp_path)

    assert result["ok"] is True
    assert commands == [
        "npm create vite@latest . -- --template react",
        "npm install",
    ]
    assert result["commands_run"] == commands
    assert any("network" in step.lower() for step in result["plan"])


def test_template_selection_prefers_explicit_framework_then_python_ui():
    assert select_template("Build a React calculator") == "vite_react"
    assert select_template("Build a calculator app in Python with UI") == (
        "python_tkinter"
    )
    assert select_template("Build a simple marketing website") == "static_html"
