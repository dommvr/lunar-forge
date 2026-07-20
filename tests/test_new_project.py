from typer.testing import CliRunner

import lunar_forge.tools.registry as registry_module
from lunar_forge.cli import app
from lunar_forge.permissions import PermissionLevel, PermissionRequest
from lunar_forge.workflows.new_project import (
    SUPPORTED_TEMPLATES,
    TEMPLATE_SPECS,
    TemplateSpec,
    run_new_project,
    select_template,
)


def test_new_command_exists():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "new" in result.stdout
    assert "Create a small starter project" in result.stdout

    run_help = CliRunner().invoke(app, ["run", "--help"])
    assert run_help.exit_code == 0
    assert "--docker" in run_help.stdout
    assert "--allow-network" in run_help.stdout


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


def test_vite_dependency_install_requires_approval_even_in_yes_mode(tmp_path):
    requests: list[PermissionRequest] = []

    def deny(request: PermissionRequest) -> bool:
        requests.append(request)
        return False

    result = run_new_project(
        "Build a Vite React portfolio",
        tmp_path,
        mode="yes",
        approval_callback=deny,
    )

    assert result["ok"] is False
    assert result["template"] == "vite_react"
    assert result["changed_files"] == [
        "index.html",
        "package.json",
        "vite.config.js",
        "src/main.jsx",
        "src/App.jsx",
        "src/App.css",
        "README.md",
    ]
    assert result["commands_run"] == []
    assert len(requests) == 1
    assert requests[0].tool_name == "run_command"
    assert requests[0].permission is PermissionLevel.EXECUTE
    assert "npm install" in requests[0].description
    assert result["command_results"][0]["permission_denied"] is True
    assert (tmp_path / "package.json").is_file()
    assert (tmp_path / "src" / "App.jsx").is_file()


def test_vite_direct_scaffold_runs_approved_install_and_build(monkeypatch, tmp_path):
    commands: list[str] = []

    def successful_command(
        project_root,
        command,
        timeout_ms=120_000,
        *,
        runtime_mode="local",
        allow_network=False,
    ):
        commands.append(command)
        return {
            "ok": True,
            "command": command,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 1,
            "truncated": False,
        }

    monkeypatch.setattr(registry_module, "run_command", successful_command)

    result = run_new_project(
        "Build a React app with Vite",
        tmp_path,
        approval_callback=lambda request: True,
    )

    assert result["ok"] is True
    assert commands == ["npm install", "npm run build"]
    assert result["commands_run"] == commands
    assert any("network" in step.lower() for step in result["plan"])
    package = (tmp_path / "package.json").read_text(encoding="utf-8")
    assert '"dev": "vite"' in package
    assert '"build": "vite build"' in package
    assert '"preview": "vite preview"' in package
    assert (tmp_path / "src" / "main.jsx").is_file()
    assert (tmp_path / "src" / "App.jsx").is_file()
    assert (tmp_path / "src" / "App.css").is_file()
    assert (tmp_path / "README.md").is_file()


def test_vite_plan_mode_lists_files_without_writing(tmp_path):
    def unexpected_approval(request: PermissionRequest) -> bool:
        raise AssertionError("Vite plan mode must not request approval")

    result = run_new_project(
        "Build a Vite React website",
        tmp_path,
        mode="plan",
        approval_callback=unexpected_approval,
    )

    assert result["ok"] is True
    assert result["planned"] is True
    assert result["template"] == "vite_react"
    assert result["planned_files"] == [
        "index.html",
        "package.json",
        "vite.config.js",
        "src/main.jsx",
        "src/App.jsx",
        "src/App.css",
        "README.md",
    ]
    assert result["planned_commands"] == ["npm install"]
    assert result["validation_commands"] == ["npm run build"]
    assert list(tmp_path.iterdir()) == []


def test_template_selection_prefers_explicit_framework_then_python_ui():
    assert select_template("Build a React calculator") == "vite_react"
    assert select_template("Build a Vite website for a bakery") == "vite_react"
    assert select_template("Create a React portfolio website") == "vite_react"
    assert select_template("Build a JSON API with FastAPI") == "fastapi"
    assert select_template("Build a small Flask website") == "flask"
    assert select_template("Build a calculator app in Python with UI") == (
        "python_tkinter"
    )
    assert select_template("Build a Python CLI for notes") == "python_cli"
    assert select_template("Build a simple marketing website") == "static_html"


def test_template_specs_describe_all_supported_starters():
    assert SUPPORTED_TEMPLATES == (
        "static_html",
        "python_tkinter",
        "vite_react",
        "python_cli",
        "flask",
        "fastapi",
    )

    for name in SUPPORTED_TEMPLATES:
        spec = TEMPLATE_SPECS[name]

        assert isinstance(spec, TemplateSpec)
        assert spec.name == name
        assert isinstance(spec.files, tuple)
        assert isinstance(spec.commands, tuple)
        assert isinstance(spec.validation, tuple)
        assert isinstance(spec.dependencies, tuple)
        assert spec.run_instructions

    assert TEMPLATE_SPECS["python_cli"].dependencies == ()
    assert TEMPLATE_SPECS["flask"].dependencies == ("Flask>=3.0,<4.0",)
    assert "fastapi>=0.110,<1.0" in TEMPLATE_SPECS["fastapi"].dependencies
    assert "npm install" in TEMPLATE_SPECS["vite_react"].commands
    assert "npm run build" in TEMPLATE_SPECS["vite_react"].validation
    assert "package.json" in TEMPLATE_SPECS["vite_react"].files


def test_python_cli_starter_is_generated_and_validated(tmp_path):
    requests: list[PermissionRequest] = []

    def approve(request: PermissionRequest) -> bool:
        requests.append(request)
        return True

    result = run_new_project(
        "Build a Python CLI for greeting people",
        tmp_path,
        approval_callback=approve,
    )

    assert result["ok"] is True
    assert result["template"] == "python_cli"
    assert result["changed_files"] == ["app.py", "test_app.py", "README.md"]
    assert result["commands_run"] == ["python -m unittest -q"]
    assert result["validation"] == ["python -m unittest -q passed"]
    assert "argparse" in (tmp_path / "app.py").read_text(encoding="utf-8")
    assert (tmp_path / "test_app.py").is_file()
    assert (tmp_path / "README.md").is_file()
    assert [request.permission for request in requests] == [
        PermissionLevel.WRITE,
        PermissionLevel.WRITE,
        PermissionLevel.WRITE,
        PermissionLevel.EXECUTE,
    ]


def test_flask_and_fastapi_starters_install_and_validate_with_approval(
    monkeypatch,
    tmp_path,
):
    commands: list[str] = []

    def successful_command(
        project_root,
        command,
        timeout_ms=120_000,
        *,
        runtime_mode="local",
        allow_network=False,
    ):
        commands.append(command)
        return {
            "ok": True,
            "command": command,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 1,
            "truncated": False,
        }

    monkeypatch.setattr(registry_module, "run_command", successful_command)

    for prompt, template, import_name in (
        ("Build a small Flask service", "flask", "from flask import Flask"),
        ("Build a small FastAPI service", "fastapi", "from fastapi import FastAPI"),
    ):
        project_root = tmp_path / template
        project_root.mkdir()
        requests: list[PermissionRequest] = []

        def approve(request: PermissionRequest) -> bool:
            requests.append(request)
            return True

        result = run_new_project(
            prompt,
            project_root,
            approval_callback=approve,
        )

        assert result["ok"] is True
        assert result["template"] == template
        assert result["changed_files"] == [
            "app.py",
            "test_app.py",
            "requirements.txt",
            "README.md",
        ]
        assert result["commands_run"] == [
            "python -m pip install -r requirements.txt",
            "python -m unittest -q",
        ]
        assert import_name in (project_root / "app.py").read_text(encoding="utf-8")
        assert (project_root / "README.md").is_file()
        assert [request.permission for request in requests] == [
            PermissionLevel.WRITE,
            PermissionLevel.WRITE,
            PermissionLevel.WRITE,
            PermissionLevel.WRITE,
            PermissionLevel.EXECUTE,
            PermissionLevel.EXECUTE,
        ]

    assert commands == [
        "python -m pip install -r requirements.txt",
        "python -m unittest -q",
        "python -m pip install -r requirements.txt",
        "python -m unittest -q",
    ]


def test_dependency_install_is_denied_before_validation(tmp_path):
    requests: list[PermissionRequest] = []

    def approve_writes_only(request: PermissionRequest) -> bool:
        requests.append(request)
        return request.permission is PermissionLevel.WRITE

    result = run_new_project(
        "Build a Flask API",
        tmp_path,
        approval_callback=approve_writes_only,
    )

    assert result["ok"] is False
    assert result["changed_files"] == [
        "app.py",
        "test_app.py",
        "requirements.txt",
        "README.md",
    ]
    assert result["commands_run"] == []
    assert result["validation"] == []
    assert result["command_results"][0]["permission_denied"] is True
    assert requests[-1].permission is PermissionLevel.EXECUTE
    assert "pip install" in requests[-1].description


def test_fastapi_plan_mode_reports_metadata_without_writing(tmp_path):
    def unexpected_approval(request: PermissionRequest) -> bool:
        raise AssertionError("Plan mode must not request approval")

    result = run_new_project(
        "Build a FastAPI service",
        tmp_path,
        mode="plan",
        approval_callback=unexpected_approval,
    )

    assert result["ok"] is True
    assert result["planned"] is True
    assert result["planned_files"] == [
        "app.py",
        "test_app.py",
        "requirements.txt",
        "README.md",
    ]
    assert result["planned_commands"] == [
        "python -m pip install -r requirements.txt"
    ]
    assert result["validation_commands"] == ["python -m unittest -q"]
    assert list(tmp_path.iterdir()) == []
