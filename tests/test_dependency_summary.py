import json

from lunar_forge.tools.dependencies import (
    MAX_DEPENDENCY_ITEMS,
    dependency_summary,
)


def test_dependency_summary_reports_node_vite_metadata_without_reading_lock_body(
    tmp_path,
):
    package = {
        "packageManager": "npm@10.0.0",
        "scripts": {
            "test": "vitest run",
            "lint": "eslint .",
            "build": "vite build",
            "dev": "vite",
        },
        "dependencies": {"react": "^19.0.0"},
        "devDependencies": {"vite": "^7.0.0", "vitest": "^3.0.0"},
    }
    (tmp_path / "package.json").write_text(
        json.dumps(package),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text(
        "this is intentionally not valid lockfile data",
        encoding="utf-8",
    )

    result = dependency_summary(tmp_path)

    assert result["ok"] is True
    assert result["package_manager"] == "pnpm"
    assert result["package_manager_hints"] == ["pnpm"]
    assert result["lockfiles"] == ["pnpm-lock.yaml"]
    assert result["scripts"]["dev"] == "vite"
    assert result["dependencies"] == [
        {
            "name": "react",
            "specifier": "^19.0.0",
            "source": "package.json",
        }
    ]
    assert {item["name"] for item in result["dev_dependencies"]} == {
        "vite",
        "vitest",
    }
    assert result["framework_hints"] == ["vite", "react"]
    assert result["likely_commands"] == {
        "validation": ["pnpm test", "pnpm lint", "pnpm build"],
        "dev": ["pnpm dev"],
        "build": ["pnpm build"],
    }
    assert result["warnings"] == []


def test_dependency_summary_collects_python_manifests_statically(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
dependencies = ["Flask>=3", "requests>=2"]

[project.scripts]
demo = "demo:main"

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "FastAPI>=0.110\nuvicorn>=0.30\n",
        encoding="utf-8",
    )
    (tmp_path / "setup.cfg").write_text(
        """
[options]
install_requires =
    rich>=13

[options.extras_require]
quality =
    mypy>=1

[options.entry_points]
console_scripts =
    cfg-demo = demo:main
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "setup.py").write_text(
        """
from setuptools import setup

setup(
    install_requires=["typer>=0.12"],
    extras_require={"quality": ["ruff>=0.9"]},
    entry_points={"console_scripts": ["setup-demo=demo:main"]},
)
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()

    result = dependency_summary(tmp_path)
    python_names = {item["name"].casefold() for item in result["python_dependencies"]}

    assert result["ok"] is True
    assert result["package_manager"] == "pip"
    assert result["manifests"] == [
        "pyproject.toml",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
    ]
    assert {
        "fastapi",
        "flask",
        "mypy",
        "pytest",
        "requests",
        "rich",
        "ruff",
        "typer",
        "uvicorn",
    }.issubset(python_names)
    assert result["framework_hints"] == ["flask", "fastapi"]
    assert result["python_scripts"] == {
        "cfg-demo": "demo:main",
        "demo": "demo:main",
        "setup-demo": "demo:main",
    }
    assert result["likely_commands"]["validation"] == [
        "python -m pytest",
        "ruff check .",
        "mypy .",
        "python -B -m compileall .",
    ]


def test_dependency_summary_empty_project_is_compact_and_serializable(tmp_path):
    result = dependency_summary(tmp_path)

    assert result["ok"] is True
    assert result["package_manager"] is None
    assert result["package_manager_hints"] == []
    assert result["manifests"] == []
    assert result["scripts"] == {}
    assert result["python_scripts"] == {}
    assert result["dependencies"] == []
    assert result["dev_dependencies"] == []
    assert result["python_dependencies"] == []
    assert result["framework_hints"] == []
    assert result["likely_commands"] == {
        "validation": [],
        "dev": [],
        "build": [],
    }
    assert len(json.dumps(result)) < 5_000


def test_dependency_summary_handles_mixed_markers_and_bounds_results(tmp_path):
    dependencies = {
        f"package-{index:03d}": f"^{index}.0.0"
        for index in range(MAX_DEPENDENCY_ITEMS + 15)
    }
    dependencies["vite"] = "^7"
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"build": "vite build"},
                "dependencies": dependencies,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mixed"\ndependencies = ["Django>=5"]\n',
        encoding="utf-8",
    )
    (tmp_path / "manage.py").write_text(
        "raise RuntimeError('must never execute')\n",
        encoding="utf-8",
    )

    result = dependency_summary(tmp_path)

    assert result["ok"] is True
    assert result["package_manager"] == "npm"
    assert result["package_manager_hints"] == ["npm", "pip"]
    assert result["manifests"] == ["package.json", "pyproject.toml"]
    assert {"vite", "django"}.issubset(result["framework_hints"])
    assert len(result["dependencies"]) == MAX_DEPENDENCY_ITEMS
    assert result["truncation"]["dependencies"] is True
    assert result["truncated"] is True
    assert len(json.dumps(result)) < 40_000


def test_dependency_summary_redacts_credentials_in_returned_metadata(tmp_path):
    secret = "credential-value-that-must-not-escape"
    quoted_secret = "quoted credential value"
    option_secret = "option-secret-value"
    bearer_secret = "bearer.secret.value"
    (tmp_path / "requirements.txt").write_text(
        f"private-lib @ https://{secret}@example.com/archive.whl\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "assignment": f'API_TOKEN="{quoted_secret}" tool check',
                    "check": f"API_TOKEN={secret} tool check",
                    "option": f"tool --api-key {option_secret} check",
                    "bearer": f"tool --header 'Bearer {bearer_secret}'",
                }
            }
        ),
        encoding="utf-8",
    )

    result = dependency_summary(tmp_path)
    serialized = json.dumps(result)

    assert result["ok"] is True
    for value in (secret, quoted_secret, option_secret, bearer_secret):
        assert value not in serialized
    assert "[REDACTED]" in serialized
