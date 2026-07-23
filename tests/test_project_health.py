import json
from importlib import import_module
from types import SimpleNamespace

from lunar_forge.tools.project_health import project_health


project_health_module = import_module("lunar_forge.tools.project_health")


def test_project_health_reports_empty_project_concisely(tmp_path):
    result = project_health(tmp_path, allow_git=False)

    assert result["ok"] is True
    assert result["status"] == "empty"
    assert result["checks"]["readme"]["present"] is False
    assert result["checks"]["agents"]["present"] is False
    assert result["checks"]["agents"]["nested_count"] == 0
    assert result["checks"]["tests"]["present"] is False
    assert result["package_markers"] == []
    assert result["validation_commands"] == []
    assert result["generated_runtime_paths"] == []
    assert result["suspicious_tracked_paths"] == []
    assert result["tracked_path_check"] == "skipped_no_command"
    assert len(json.dumps(result)) < 5_000


def test_project_health_reports_python_readiness_without_secret_contents(tmp_path):
    secret = "health-check-must-not-read-this-secret"
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Root guidance\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text(secret, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
dependencies = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    nested = tmp_path / "src" / "admin"
    nested.mkdir(parents=True)
    (nested / "AGENTS.md").write_text("Nested guidance\n", encoding="utf-8")
    (nested / "__pycache__").mkdir()
    (tmp_path / ".pytest_cache").mkdir()
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "tests.yml").write_text("name: tests\n", encoding="utf-8")

    result = project_health(tmp_path, allow_git=False)
    serialized = json.dumps(result)

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["checks"]["readme"] == {
        "present": True,
        "path": "README.md",
    }
    assert result["checks"]["agents"]["nested_count"] == 1
    assert result["checks"]["tests"]["directories"] == ["tests"]
    assert result["checks"]["tests"]["configs"] == ["pyproject.toml"]
    assert result["checks"]["ci"]["paths"] == [".github/workflows/tests.yml"]
    assert result["package_markers"] == ["pyproject.toml"]
    assert result["validation_commands"] == [
        "python -m pytest",
        "python -B -m compileall .",
    ]
    assert result["generated_runtime_paths"] == [
        ".pytest_cache",
        "src/admin/__pycache__",
    ]
    assert secret not in serialized


def test_project_health_reports_node_vite_and_mixed_package_markers(tmp_path):
    (tmp_path / "README.md").write_text("# Mixed\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Guidance\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("dist\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "vitest run",
                    "build": "vite build",
                    "dev": "vite",
                },
                "devDependencies": {"vite": "^7", "vitest": "^3"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("Flask>=3\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()

    result = project_health(tmp_path, allow_git=False)

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["package_markers"] == [
        "package.json",
        "package-lock.json",
        "requirements.txt",
    ]
    assert result["validation_commands"] == [
        "npm test",
        "npm run build",
        "python -m pytest",
        "python -B -m compileall .",
    ]
    assert result["checks"]["tests"]["configs"] == ["package.json"]
    assert result["generated_runtime_paths"] == ["dist"]


def test_project_health_never_executes_setup_py(tmp_path):
    marker = tmp_path / "executed.txt"
    (tmp_path / "setup.py").write_text(
        (
            "from pathlib import Path\n"
            "from setuptools import setup\n"
            "Path('executed.txt').write_text('bad')\n"
            "setup(install_requires=['requests>=2'])\n"
        ),
        encoding="utf-8",
    )

    result = project_health(tmp_path, allow_git=False)

    assert result["ok"] is True
    assert "setup.py" in result["package_markers"]
    assert not marker.exists()


def test_project_health_reports_safely_available_tracked_runtime_paths(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        project_health_module,
        "_tracked_suspicious_paths",
        lambda root: {
            "status": "checked",
            "paths": [".env", "dist/bundle.js"],
            "truncated": False,
        },
    )

    result = project_health(tmp_path)

    assert result["suspicious_tracked_paths"] == [".env", "dist/bundle.js"]
    assert result["tracked_path_check"] == "checked"


def test_project_health_git_inspection_is_read_only_and_shell_false(
    monkeypatch,
    tmp_path,
):
    captured = {}
    monkeypatch.setattr(
        project_health_module,
        "resolve_executable",
        lambda executable, cwd: "C:/Git/bin/git.exe",
    )

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=b".env\0dist/bundle.js\0",
            stderr=b"",
        )

    monkeypatch.setattr(project_health_module.subprocess, "run", fake_run)

    result = project_health_module._tracked_suspicious_paths(tmp_path)

    assert result == {
        "status": "checked",
        "paths": [".env", "dist/bundle.js"],
        "truncated": False,
    }
    assert captured["arguments"] == [
        "C:/Git/bin/git.exe",
        "ls-files",
        "--cached",
        "-z",
        "--",
        ".",
    ]
    assert captured["cwd"] == tmp_path
    assert captured["shell"] is False
    assert captured["check"] is False
