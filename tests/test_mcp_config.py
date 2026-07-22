from pathlib import Path

import pytest

import lunar_forge.mcp.config as mcp_config_module
from lunar_forge.mcp.config import (
    MCPConfigError,
    load_mcp_config,
    resolve_server_environment,
)


def _isolate_user_config(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(
        mcp_config_module.Path,
        "home",
        classmethod(lambda cls: home),
    )


def _write_project_config(project: Path, content: str) -> None:
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True, exist_ok=True)
    (config_directory / "mcp.yaml").write_text(content, encoding="utf-8")


def test_mcp_config_defaults_to_no_servers(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()

    config = load_mcp_config(project)

    assert dict(config.servers) == {}
    assert config.enabled_servers == ()


def test_mcp_server_is_disabled_when_enabled_is_omitted(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(
        project,
        """
mcp:
  servers:
    github:
      command: github-mcp-server
      args: []
""".lstrip(),
    )

    config = load_mcp_config(project)

    assert config.servers["github"].enabled is False
    assert config.enabled_servers == ()


def test_explicitly_enabled_server_loads_supported_shape(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(
        project,
        """
mcp:
  servers:
    github:
      command: github-mcp-server
      args:
        - --stdio
      env:
        GITHUB_TOKEN: ${GITHUB_TOKEN}
      enabled: true
""".lstrip(),
    )

    config = load_mcp_config(project)
    server = config.servers["github"]

    assert server.command == "github-mcp-server"
    assert server.args == ("--stdio",)
    assert dict(server.env) == {"GITHUB_TOKEN": "GITHUB_TOKEN"}
    assert server.enabled is True
    assert config.enabled_servers == (server,)


def test_project_config_overrides_user_server_fields(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _isolate_user_config(monkeypatch, home)
    user_directory = home / ".lunar-forge"
    user_directory.mkdir(parents=True)
    (user_directory / "mcp.yaml").write_text(
        """
servers:
  github:
    command: user-github-server
    args: [--stdio]
    enabled: false
""".lstrip(),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(
        project,
        """
mcp:
  servers:
    github:
      command: project-github-server
      enabled: true
""".lstrip(),
    )

    server = load_mcp_config(project).servers["github"]

    assert server.command == "project-github-server"
    assert server.args == ("--stdio",)
    assert server.enabled is True


def test_raw_environment_secret_is_rejected(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(
        project,
        """
servers:
  github:
    command: github-mcp-server
    env:
      GITHUB_TOKEN: raw-secret-value
    enabled: true
""".lstrip(),
    )

    with pytest.raises(MCPConfigError, match="references"):
        load_mcp_config(project)


def test_unknown_server_keys_are_rejected_clearly(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(
        project,
        """
servers:
  github:
    command: github-mcp-server
    transport: stdio
    enabled: true
""".lstrip(),
    )

    with pytest.raises(
        MCPConfigError,
        match="MCP server 'github' contains unknown keys",
    ):
        load_mcp_config(project)


@pytest.mark.parametrize(
    "args_yaml",
    (
        "      - --token=credential-value\n",
        "      - --token\n      - credential-value\n",
    ),
)
def test_inline_secret_argument_is_rejected(monkeypatch, tmp_path, args_yaml):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(
        project,
        (
            """
servers:
  github:
    command: github-mcp-server
    args:
{args_yaml}
    enabled: true
""".lstrip()
        ).format(args_yaml=args_yaml.rstrip()),
    )

    with pytest.raises(MCPConfigError, match="inline secrets"):
        load_mcp_config(project)


def test_server_environment_is_resolved_only_when_requested(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(
        project,
        """
servers:
  github:
    command: github-mcp-server
    env:
      GITHUB_TOKEN: ${HOST_GITHUB_TOKEN}
    enabled: true
""".lstrip(),
    )
    server = load_mcp_config(project).servers["github"]

    assert "secret-value" not in repr(server)
    assert resolve_server_environment(
        server,
        {"HOST_GITHUB_TOKEN": "secret-value"},
    ) == {"GITHUB_TOKEN": "secret-value"}


def test_missing_server_environment_variable_is_reported(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    _write_project_config(
        project,
        """
servers:
  github:
    command: github-mcp-server
    env:
      GITHUB_TOKEN: ${HOST_GITHUB_TOKEN}
    enabled: true
""".lstrip(),
    )
    server = load_mcp_config(project).servers["github"]

    with pytest.raises(MCPConfigError, match="HOST_GITHUB_TOKEN"):
        resolve_server_environment(server, {})


def test_project_mcp_config_symlink_cannot_escape(monkeypatch, tmp_path):
    _isolate_user_config(monkeypatch, tmp_path / "home")
    project = tmp_path / "project"
    config_directory = project / ".agent"
    config_directory.mkdir(parents=True)
    outside_config = tmp_path / "outside.yaml"
    outside_config.write_text("servers: {}\n", encoding="utf-8")
    try:
        (config_directory / "mcp.yaml").symlink_to(outside_config)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable on this platform: {exc}")

    with pytest.raises(PermissionError, match="outside the project root"):
        load_mcp_config(project)
