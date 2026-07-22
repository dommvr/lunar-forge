import json

import pytest

from lunar_forge.project_detection import detect_project, detect_project_type


def test_detect_empty_project(tmp_path):
    project = detect_project(tmp_path)

    assert project == {
        "languages": [],
        "frameworks": [],
        "package_manager": None,
        "routing": None,
        "test_command": None,
        "build_command": None,
        "dev_command": None,
        "local_url": None,
        "is_empty": True,
    }
    assert detect_project_type(tmp_path) == "empty"


def test_detect_python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    project = detect_project(tmp_path)

    assert project["languages"] == ["python"]
    assert project["frameworks"] == []
    assert project["test_command"] == "pytest"
    assert project["is_empty"] is False
    assert detect_project_type(tmp_path) == "python"


def test_detect_vite_react_npm_project(tmp_path):
    package_json = {
        "dependencies": {"react": "latest"},
        "devDependencies": {"vite": "latest"},
        "scripts": {
            "test": "vitest",
            "build": "vite build",
            "dev": "vite",
        },
    }
    (tmp_path / "package.json").write_text(
        json.dumps(package_json),
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "vite.config.ts").write_text("", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("", encoding="utf-8")

    project = detect_project(tmp_path)

    assert project["languages"] == ["javascript", "typescript"]
    assert project["frameworks"] == ["vite", "react"]
    assert project["package_manager"] == "npm"
    assert project["test_command"] == "npm test"
    assert project["build_command"] == "npm run build"
    assert project["dev_command"] == "npm run dev"
    assert project["local_url"] == "http://localhost:5173"


def test_detect_vite_infers_npm_dev_command_without_declared_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vite": "latest"}}),
        encoding="utf-8",
    )

    project = detect_project(tmp_path)

    assert project["package_manager"] == "npm"
    assert project["dev_command"] == "npm run dev"
    assert project["local_url"] == "http://localhost:5173"


def test_detect_nextjs_pnpm_app_router(tmp_path):
    package_json = {
        "dependencies": {"next": "latest", "react": "latest"},
        "scripts": {"build": "next build", "dev": "next dev"},
    }
    (tmp_path / "package.json").write_text(
        json.dumps(package_json),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "next.config.mjs").write_text("", encoding="utf-8")
    (tmp_path / "app").mkdir()

    project = detect_project(tmp_path)

    assert project["frameworks"] == ["nextjs", "react"]
    assert project["package_manager"] == "pnpm"
    assert project["routing"] == "app_router"
    assert project["build_command"] == "pnpm build"
    assert project["dev_command"] == "pnpm dev"
    assert project["local_url"] == "http://localhost:3000"


def test_detect_nextjs_pages_router(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "latest"}}),
        encoding="utf-8",
    )
    (tmp_path / "pages").mkdir()

    assert detect_project(tmp_path)["routing"] == "pages_router"


def test_detect_yarn_package_manager(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")

    project = detect_project(tmp_path)

    assert project["languages"] == ["javascript"]
    assert project["package_manager"] == "yarn"


@pytest.mark.parametrize(
    ("lock_file", "expected_command"),
    (
        ("package-lock.json", "npm run dev"),
        ("pnpm-lock.yaml", "pnpm dev"),
        ("yarn.lock", "yarn dev"),
    ),
)
def test_vite_dev_command_uses_detected_package_manager(
    tmp_path,
    lock_file,
    expected_command,
):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {"vite": "latest"},
                "scripts": {"dev": "vite"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / lock_file).write_text("", encoding="utf-8")

    project = detect_project(tmp_path)

    assert project["dev_command"] == expected_command
    assert project["local_url"] == "http://localhost:5173"


def test_detect_django_from_manage_py_and_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text("Django\n", encoding="utf-8")
    (tmp_path / "manage.py").write_text("", encoding="utf-8")

    project = detect_project(tmp_path)

    assert project["languages"] == ["python"]
    assert project["frameworks"] == ["django"]
    assert project["test_command"] == "pytest"


def test_detect_flask_like_app_py(tmp_path):
    (tmp_path / "app.py").write_text("from flask import Flask\n", encoding="utf-8")

    project = detect_project(tmp_path)

    assert project["languages"] == ["python"]
    assert project["frameworks"] == ["flask"]
