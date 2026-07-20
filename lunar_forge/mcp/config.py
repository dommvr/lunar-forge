"""Strict loading for disabled-by-default MCP server configuration."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from lunar_forge.tools.files import safe_path


MAX_CONFIG_CHARACTERS = 500_000
_SERVER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_ENVIRONMENT_REFERENCE_PATTERN = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|token|secret|password)\s*(?:=|:)\s*\S+"
)
_SECRET_OPTION_PATTERN = re.compile(
    r"(?i)(?:^|\s)--?(?:api[_-]?key|access[_-]?token|token|secret|password)"
    r"(?:\s|=|:|$)"
)
_SECRET_PREFIX_PATTERN = re.compile(
    r"(?i)(?:^|[=:\s])(?:sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]+"
)
_SERVER_KEYS = frozenset({"command", "args", "env", "enabled"})


class MCPConfigError(ValueError):
    """Raised when MCP configuration is invalid or unsafe."""


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for one explicitly declared MCP server."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(frozen=True)
class MCPConfig:
    """Merged MCP configuration. An empty instance enables nothing."""

    servers: Mapping[str, MCPServerConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "servers", MappingProxyType(dict(self.servers)))

    @property
    def enabled_servers(self) -> tuple[MCPServerConfig, ...]:
        """Return only servers that opted in with ``enabled: true``."""
        return tuple(
            server
            for _, server in sorted(self.servers.items())
            if server.enabled
        )


def load_mcp_config(project_root: str | Path) -> MCPConfig:
    """Load user defaults followed by project MCP overrides.

    Configuration is declarative only. Loading it never starts a process and
    environment entries retain variable names rather than resolved secret values.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise MCPConfigError(f"Project root is not a directory: {root}")

    user_path = Path.home() / ".lunar-forge" / "mcp.yaml"
    project_path = safe_path(root, ".agent/mcp.yaml")
    user_servers = _read_server_definitions(user_path)
    project_servers = _read_server_definitions(project_path)
    merged = _merge_server_definitions(user_servers, project_servers)
    return MCPConfig(
        servers={
            name: _parse_server(name, definition)
            for name, definition in sorted(merged.items())
        }
    )


def resolve_server_environment(
    server: MCPServerConfig,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve only the named environment variables needed by a server.

    Raw values are deliberately absent from ``MCPServerConfig`` so config
    objects, reprs, and logs do not retain credentials.
    """
    source = os.environ if environ is None else environ
    missing = sorted({name for name in server.env.values() if name not in source})
    if missing:
        joined = ", ".join(missing)
        raise MCPConfigError(f"Missing MCP environment variables: {joined}")
    return {target: source[source_name] for target, source_name in server.env.items()}


def _read_server_definitions(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise MCPConfigError(f"MCP config is not a file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MCPConfigError(f"Could not read MCP config: {path}") from exc
    if len(text) > MAX_CONFIG_CHARACTERS:
        raise MCPConfigError(f"MCP config is too large: {path}")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MCPConfigError(f"Invalid MCP YAML: {path}") from exc
    if document is None:
        return {}
    if not isinstance(document, Mapping):
        raise MCPConfigError(f"MCP config must contain a mapping: {path}")

    top_level = dict(document)
    if "mcp" in top_level:
        if set(top_level) != {"mcp"}:
            raise MCPConfigError("MCP config contains unknown top-level keys.")
        mcp_section = top_level["mcp"]
        if not isinstance(mcp_section, Mapping):
            raise MCPConfigError("The mcp setting must be a mapping.")
        if set(mcp_section) - {"servers"}:
            raise MCPConfigError("The mcp setting contains unknown keys.")
        servers = mcp_section.get("servers", {})
    else:
        if set(top_level) - {"servers"}:
            raise MCPConfigError("MCP config contains unknown top-level keys.")
        servers = top_level.get("servers", {})

    if not isinstance(servers, Mapping):
        raise MCPConfigError("The MCP servers setting must be a mapping.")
    definitions: dict[str, dict[str, Any]] = {}
    for name, definition in servers.items():
        if not isinstance(name, str) or not _SERVER_NAME_PATTERN.fullmatch(name):
            raise MCPConfigError("MCP server names must be lowercase namespace names.")
        if not isinstance(definition, Mapping):
            raise MCPConfigError(f"MCP server '{name}' must be a mapping.")
        definitions[name] = dict(definition)
    return definitions


def _merge_server_definitions(
    user_servers: Mapping[str, Mapping[str, Any]],
    project_servers: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = {name: dict(definition) for name, definition in user_servers.items()}
    for name, definition in project_servers.items():
        merged[name] = {**merged.get(name, {}), **definition}
    return merged


def _parse_server(name: str, definition: Mapping[str, Any]) -> MCPServerConfig:
    unknown_keys = set(definition) - _SERVER_KEYS
    if unknown_keys:
        raise MCPConfigError(f"MCP server '{name}' contains unknown keys.")

    command = definition.get("command")
    if not isinstance(command, str) or not command.strip():
        raise MCPConfigError(f"MCP server '{name}' requires a command.")
    command = command.strip()
    _reject_inline_secret(command, name)

    raw_args = definition.get("args", [])
    if not isinstance(raw_args, list) or not all(
        isinstance(argument, str) for argument in raw_args
    ):
        raise MCPConfigError(f"MCP server '{name}' args must be a list of strings.")
    args = tuple(raw_args)
    for argument in args:
        _reject_inline_secret(argument, name)

    raw_env = definition.get("env", {})
    if not isinstance(raw_env, Mapping):
        raise MCPConfigError(f"MCP server '{name}' env must be a mapping.")
    env: dict[str, str] = {}
    for target, reference in raw_env.items():
        if not isinstance(target, str) or not _ENVIRONMENT_NAME_PATTERN.fullmatch(target):
            raise MCPConfigError(f"MCP server '{name}' has an invalid env name.")
        if not isinstance(reference, str):
            raise MCPConfigError(
                f"MCP server '{name}' env values must use ${{VARIABLE}} references."
            )
        match = _ENVIRONMENT_REFERENCE_PATTERN.fullmatch(reference)
        if match is None:
            raise MCPConfigError(
                f"MCP server '{name}' env values must use ${{VARIABLE}} references."
            )
        env[target] = match.group(1)

    enabled = definition.get("enabled", False)
    if not isinstance(enabled, bool):
        raise MCPConfigError(f"MCP server '{name}' enabled must be a boolean.")
    return MCPServerConfig(
        name=name,
        command=command,
        args=args,
        env=env,
        enabled=enabled,
    )


def _reject_inline_secret(value: str, server_name: str) -> None:
    if (
        _SECRET_ASSIGNMENT_PATTERN.search(value)
        or _SECRET_OPTION_PATTERN.search(value)
        or _SECRET_PREFIX_PATTERN.search(value)
    ):
        raise MCPConfigError(
            f"MCP server '{server_name}' may not contain inline secrets; use env references."
        )
