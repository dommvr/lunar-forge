from pathlib import Path

import pytest

import lunar_forge.config as config_module
from lunar_forge.config import MAX_CONFIG_CHARACTERS, load_config


def _isolate_user_config(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(
        config_module.Path,
        "home",
        classmethod(lambda cls: home),
    )


def test_project_config_is_size_limited(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        "#" * (MAX_CONFIG_CHARACTERS + 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="character limit"):
        load_config(project)


def test_project_config_symlink_cannot_escape_project(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    outside_config = tmp_path / "outside.yaml"
    outside_config.write_text("permissions:\n  mode: yes\n", encoding="utf-8")
    try:
        (config_directory / "config.yaml").symlink_to(outside_config)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable on this platform: {exc}")

    with pytest.raises(PermissionError, match="outside the project root"):
        load_config(project)


def test_model_api_defaults_to_chat(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).model.api == "chat"


def test_project_trust_defaults_to_auto(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).runtime.project_trust == "auto"


@pytest.mark.parametrize("project_trust", ("trusted", "untrusted", "unknown"))
def test_project_trust_can_be_marked_in_project_config(
    monkeypatch,
    tmp_path,
    project_trust,
):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        f"runtime:\n  project_trust: {project_trust}\n",
        encoding="utf-8",
    )

    assert load_config(project).runtime.project_trust == project_trust


def test_project_trust_rejects_unknown_config_value(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        "runtime:\n  project_trust: absolutely\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime.project_trust"):
        load_config(project)


def test_subagents_default_to_disabled(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()

    subagents = load_config(project).subagents

    assert subagents.enabled is False
    assert subagents.parallel is False


def test_subagents_can_be_enabled_by_project_config(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        "subagents:\n  enabled: true\n",
        encoding="utf-8",
    )

    assert load_config(project).subagents.enabled is True


def test_parallel_subagents_require_explicit_project_config(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        "subagents:\n  enabled: true\n  parallel: true\n",
        encoding="utf-8",
    )

    subagents = load_config(project).subagents

    assert subagents.enabled is True
    assert subagents.parallel is True


def test_subagents_can_be_enabled_by_environment(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("LUNAR_FORGE_SUBAGENTS", "true")
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).subagents.enabled is True


def test_parallel_subagents_can_be_enabled_by_environment(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("LUNAR_FORGE_SUBAGENTS", "true")
    monkeypatch.setenv("LUNAR_FORGE_PARALLEL_SUBAGENTS", "true")
    project = tmp_path / "project"
    project.mkdir()

    subagents = load_config(project).subagents

    assert subagents.enabled is True
    assert subagents.parallel is True


def test_mcp_integration_defaults_to_disabled(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).mcp.enabled is False


def test_mcp_integration_requires_explicit_config_enablement(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        "mcp:\n  enabled: true\n",
        encoding="utf-8",
    )

    assert load_config(project).mcp.enabled is True


def test_checked_in_playwright_application_config_enables_mcp(
    monkeypatch,
    tmp_path,
):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    example_path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "mcp"
        / "playwright"
        / "config.yaml"
    )
    (config_directory / "config.yaml").write_text(
        example_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert load_config(project).mcp.enabled is True


def test_plugin_integration_defaults_to_disabled(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).plugins.enabled is False


def test_plugin_integration_requires_explicit_config_enablement(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        "plugins:\n  enabled: true\n",
        encoding="utf-8",
    )

    assert load_config(project).plugins.enabled is True


def test_plugin_integration_can_be_enabled_by_environment(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("LUNAR_FORGE_PLUGINS_ENABLED", "true")
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).plugins.enabled is True


@pytest.mark.parametrize("api", ("chat", "responses"))
def test_model_api_loads_supported_modes(monkeypatch, tmp_path, api):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        f"model:\n  api: {api}\n",
        encoding="utf-8",
    )

    assert load_config(project).model.api == api


def test_model_api_rejects_unknown_mode(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    (config_directory / "config.yaml").write_text(
        "model:\n  api: unknown\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model.api"):
        load_config(project)


def test_reasoning_effort_defaults_to_medium(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).model.reasoning.effort == "medium"


def test_user_reasoning_effort_is_loaded(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _isolate_user_config(monkeypatch, home)
    user_config = home / ".lunar-forge" / "config.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        "model:\n  reasoning:\n    effort: low\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).model.reasoning.effort == "low"


def test_project_reasoning_effort_overrides_user_config(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _isolate_user_config(monkeypatch, home)
    user_config = home / ".lunar-forge" / "config.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        "model:\n  reasoning:\n    effort: low\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project_config = project / ".agent" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "model:\n  reasoning:\n    effort: xhigh\n",
        encoding="utf-8",
    )

    assert load_config(project).model.reasoning.effort == "xhigh"


def test_cli_reasoning_effort_overrides_project_and_user_config(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / "home"
    _isolate_user_config(monkeypatch, home)
    user_config = home / ".lunar-forge" / "config.yaml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        "model:\n  reasoning:\n    effort: low\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project_config = project / ".agent" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "model:\n  reasoning:\n    effort: xhigh\n",
        encoding="utf-8",
    )

    config = load_config(
        project,
        cli_overrides={
            "model": {
                "reasoning": {
                    "effort": "high",
                }
            }
        },
    )

    assert config.model.reasoning.effort == "high"


@pytest.mark.parametrize("effort", ("low", "medium", "high", "xhigh", "max"))
def test_reasoning_effort_accepts_supported_values(
    monkeypatch,
    tmp_path,
    effort,
):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project_config = project / ".agent" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        f"model:\n  reasoning:\n    effort: {effort}\n",
        encoding="utf-8",
    )

    assert load_config(project).model.reasoning.effort == effort


def test_reasoning_effort_can_be_loaded_from_environment(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("LUNAR_FORGE_REASONING_EFFORT", "max")
    project = tmp_path / "project"
    project.mkdir()

    assert load_config(project).model.reasoning.effort == "max"


def test_reasoning_effort_rejects_unknown_value(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project_config = project / ".agent" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "model:\n  reasoning:\n    effort: light\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=(
            "model.reasoning.effort must be one of: "
            "low, medium, high, xhigh, max"
        ),
    ):
        load_config(project)


def test_reasoning_config_must_be_a_mapping(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project_config = project / ".agent" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "model:\n  reasoning: high\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model.reasoning"):
        load_config(project)
