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
