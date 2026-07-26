import json

import pytest

from lunar_forge.subagents import PLANNER_ROLE, TESTER_ROLE
from lunar_forge.tools.ci import (
    MAX_CI_COMMANDS,
    MAX_CI_JOBS,
    ci_summary,
)
from lunar_forge.tools.registry import create_tool_registry


def test_ci_summary_returns_clear_empty_result(tmp_path):
    result = ci_summary(tmp_path)

    assert result == {
        "ok": True,
        "ci_files": [],
        "providers": [],
        "workflows": [],
        "runtime_hints": [],
        "setup_hints": [],
        "package_manager_hints": [],
        "commands": [],
        "suggested_validation_commands": [],
        "parse_errors": [],
        "truncated": False,
        "message": "No supported CI configuration files were found.",
    }
    json.dumps(result, allow_nan=False)


def test_ci_summary_reports_github_python_and_node_validation(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "quality.yml").write_text(
        """
name: Quality
on: [push]
jobs:
  test:
    name: Python and Node
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
        node-version: ["20"]
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
      - run: |
          python -m pip install -e .
          python -m pytest -q
          npm test
          npm run build
""".strip(),
        encoding="utf-8",
    )

    result = ci_summary(tmp_path)

    assert result["ok"] is True
    assert result["ci_files"] == [".github/workflows/quality.yml"]
    assert result["providers"] == ["github_actions"]
    assert result["workflows"][0]["name"] == "Quality"
    assert result["workflows"][0]["jobs"] == [
        {
            "name": "test",
            "commands": [
                "python -m pip install -e .",
                "python -m pytest -q",
                "npm test",
                "npm run build",
            ],
            "display_name": "Python and Node",
            "runner": "ubuntu-latest",
        }
    ]
    assert {
        "python 3.11",
        "python 3.12",
        "node 20",
        "runner ubuntu-latest",
    }.issubset(result["runtime_hints"])
    assert {"setup-python 3.12", "setup-node 20"}.issubset(
        result["setup_hints"]
    )
    assert result["package_manager_hints"] == ["pip", "npm"]
    assert result["suggested_validation_commands"] == [
        "python -m pytest -q",
        "npm test",
        "npm run build",
    ]


def test_ci_summary_reports_gitlab_jobs_commands_and_images(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text(
        """
stages: [test, build]
variables:
  PRIVATE_TOKEN: must-not-leak
before_script:
  - python -m pip install -e .
unit_tests:
  stage: test
  image: python:3.12
  script:
    - python -m pytest
    - ruff check .
frontend:
  stage: build
  image: node:20
  script:
    - npm ci
    - npm run build
""".strip(),
        encoding="utf-8",
    )

    result = ci_summary(tmp_path)
    jobs = {
        item["name"]: item
        for item in result["workflows"][0]["jobs"]
    }

    assert result["ok"] is True
    assert result["providers"] == ["gitlab_ci"]
    assert set(jobs) == {"frontend", "unit_tests"}
    assert jobs["frontend"]["stage"] == "build"
    assert jobs["unit_tests"]["stage"] == "test"
    assert {"image python:3.12", "image node:20"}.issubset(
        result["runtime_hints"]
    )
    assert result["package_manager_hints"] == ["pip", "npm"]
    assert result["suggested_validation_commands"] == [
        "npm run build",
        "python -m pytest",
        "ruff check .",
    ]
    assert "must-not-leak" not in json.dumps(result)


def test_ci_summary_reports_minimal_azure_pipeline(tmp_path):
    (tmp_path / "azure-pipelines.yml").write_text(
        """
variables:
  ACCESS_TOKEN: must-not-leak
jobs:
  - job: test
    displayName: Test
    pool:
      vmImage: ubuntu-latest
    steps:
      - task: UsePythonVersion@0
        inputs:
          versionSpec: "3.12"
      - script: python -m pytest
""".strip(),
        encoding="utf-8",
    )

    result = ci_summary(tmp_path)

    assert result["ok"] is True
    assert result["ci_files"] == ["azure-pipelines.yml"]
    assert result["providers"] == ["azure_pipelines"]
    assert result["runtime_hints"] == [
        "runner ubuntu-latest",
        "python 3.12",
    ]
    assert result["setup_hints"] == ["UsePythonVersion"]
    assert result["commands"] == ["python -m pytest"]
    assert result["suggested_validation_commands"] == [
        "python -m pytest"
    ]
    assert "must-not-leak" not in json.dumps(result)


def test_ci_summary_returns_bounded_yaml_parse_error(tmp_path):
    circle = tmp_path / ".circleci"
    circle.mkdir()
    (circle / "config.yml").write_text(
        "version: 2.1\njobs:\n  build: [\n",
        encoding="utf-8",
    )

    result = ci_summary(tmp_path)

    assert result["ok"] is False
    assert result["ci_files"] == [".circleci/config.yml"]
    assert result["providers"] == ["circleci"]
    assert result["workflows"] == []
    assert len(result["parse_errors"]) == 1
    assert result["parse_errors"][0]["path"] == ".circleci/config.yml"
    assert result["parse_errors"][0]["line"] > 0
    assert result["parse_errors"][0]["column"] > 0
    assert len(result["parse_errors"][0]["error"]) <= 500
    assert "could not be parsed safely" in result["error"]
    assert "preview" not in result


def test_ci_summary_redacts_env_and_secret_values_from_commands(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    secrets = (
        "global-env-secret",
        "job-env-secret",
        "literal-assignment-secret",
        "authorization-secret",
    )
    (workflow_dir / "security.yaml").write_text(
        f"""
name: Security
env:
  GLOBAL_SECRET: {secrets[0]}
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      JOB_PASSWORD: {secrets[1]}
    steps:
      - run: |
          API_TOKEN={secrets[2]} python -m pytest
          echo "${{{{ secrets.DEPLOY_TOKEN }}}}"
          curl -H "Authorization: Bearer {secrets[3]}" https://example.test
""".strip(),
        encoding="utf-8",
    )

    result = ci_summary(tmp_path)
    serialized = json.dumps(result)

    assert result["ok"] is True
    assert "[REDACTED]" in serialized
    for secret in secrets:
        assert secret not in serialized
    assert "DEPLOY_TOKEN" not in serialized
    assert result["suggested_validation_commands"] == []


def test_ci_summary_redacts_env_literals_reused_through_yaml_aliases(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "aliases.yml").write_text(
        """
name: &public_name Alias safety
env:
  DEPLOY_TOKEN: &deploy_token anchored-secret-canary
jobs:
  test:
    name: *deploy_token
    runs-on: ubuntu-latest
    steps:
      - name: *public_name
        run: *deploy_token
      - run: echo "$(RELEASE_CREDENTIAL)"
      - run: echo "${{ github.token }}"
""".strip(),
        encoding="utf-8",
    )

    result = ci_summary(tmp_path)
    serialized = json.dumps(result)

    assert result["ok"] is True
    assert "anchored-secret-canary" not in serialized
    assert "RELEASE_CREDENTIAL" not in serialized
    assert "github.token" not in serialized
    assert "[REDACTED]" in serialized
    assert result["suggested_validation_commands"] == []


def test_ci_summary_never_executes_discovered_commands(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "unsafe.yml").write_text(
        """
name: Static only
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: python -c "from pathlib import Path; Path('executed.txt').write_text('ran')"
""".strip(),
        encoding="utf-8",
    )

    result = ci_summary(tmp_path)

    assert result["ok"] is True
    assert result["commands"]
    assert not (tmp_path / "executed.txt").exists()


def test_ci_summary_skips_workflow_symlink_outside_project(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.yml"
    outside.write_text(
        "name: outside-secret-canary\njobs: {}\n",
        encoding="utf-8",
    )
    try:
        (workflow_dir / "linked.yml").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this platform.")

    result = ci_summary(tmp_path)

    assert result["ok"] is True
    assert result["ci_files"] == []
    assert "outside-secret-canary" not in json.dumps(result)


def test_ci_summary_bounds_jobs_commands_and_serialized_output(tmp_path):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    jobs = []
    for index in range(MAX_CI_JOBS + 15):
        commands = "\n".join(
            f"echo command-{index}-{command_index}"
            for command_index in range(4)
        )
        jobs.append(
            f"  job_{index}:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: |\n"
            + "\n".join(f"          {line}" for line in commands.splitlines())
        )
    (workflow_dir / "large.yml").write_text(
        "name: Large\njobs:\n" + "\n".join(jobs),
        encoding="utf-8",
    )

    result = ci_summary(tmp_path)

    assert result["ok"] is True
    assert len(result["workflows"][0]["jobs"]) == MAX_CI_JOBS
    assert len(result["commands"]) == MAX_CI_COMMANDS
    assert result["truncated"] is True
    assert len(json.dumps(result)) < 100_000


def test_plan_mode_planner_and_tester_can_use_ci_without_commands(tmp_path):
    registry = create_tool_registry(tmp_path, mode="plan")

    for role in (PLANNER_ROLE, TESTER_ROLE):
        restricted = role.restrict(registry)
        assert "ci_summary" in restricted.names()
        assert restricted.execute("ci_summary", {})["ok"] is True
        assert "run_command" not in restricted.names()
        assert "run_validation" not in restricted.names()

    assert "write_file" not in PLANNER_ROLE.restrict(registry).names()
    assert not (tmp_path / ".agent").exists()
