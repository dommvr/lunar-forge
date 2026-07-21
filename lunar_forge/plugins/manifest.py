"""Strict declarative manifests for local LunarForge plugins."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml


MAX_MANIFEST_CHARACTERS = 500_000
MAX_DESCRIPTION_CHARACTERS = 2_000
MAX_SCHEMA_CHARACTERS = 50_000
MAX_TOOLS = 50
MAX_TOOL_NAME_CHARACTERS = 128

FilesystemPermission = Literal["none", "read", "write"]

_PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_TOOL_NAME_PATTERN = re.compile(
    r"^[a-z][a-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)+$"
)
_VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
)
_ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r":[A-Za-z_][A-Za-z0-9_]*$"
)
_MANIFEST_KEYS = frozenset({"name", "version", "description", "tools"})
_TOOL_KEYS = frozenset(
    {"name", "description", "entrypoint", "parameters", "permissions"}
)
_REQUIRED_TOOL_KEYS = frozenset({"name", "entrypoint", "permissions"})
_PERMISSION_KEYS = frozenset({"filesystem", "commands", "network"})


class PluginManifestError(ValueError):
    """Raised when a plugin manifest is malformed or unsafe."""


@dataclass(frozen=True)
class PluginPermissions:
    """Capabilities a plugin tool declares before it can be registered."""

    filesystem: FilesystemPermission
    commands: bool
    network: bool


@dataclass(frozen=True)
class PluginToolManifest:
    """One namespaced plugin tool and its model-facing schema."""

    name: str
    description: str
    entrypoint: str
    parameters: dict[str, Any]
    permissions: PluginPermissions


@dataclass(frozen=True)
class PluginManifest:
    """Validated metadata for an explicitly configured local plugin."""

    name: str
    version: str
    description: str
    tools: tuple[PluginToolManifest, ...]


def load_plugin_manifest(path: str | Path) -> PluginManifest:
    """Read and validate one explicitly named local YAML manifest."""
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise PluginManifestError(f"Plugin manifest is not a file: {manifest_path}")
    try:
        content = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PluginManifestError(
            f"Could not read plugin manifest: {manifest_path}"
        ) from exc
    if len(content) > MAX_MANIFEST_CHARACTERS:
        raise PluginManifestError("Plugin manifest exceeds the size limit.")
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise PluginManifestError("Plugin manifest contains invalid YAML.") from exc
    return parse_plugin_manifest(document)


def parse_plugin_manifest(document: Any) -> PluginManifest:
    """Validate an already-decoded plugin manifest mapping."""
    if not isinstance(document, Mapping):
        raise PluginManifestError("Plugin manifest must be a mapping.")
    data = dict(document)
    _require_exact_keys(data, _MANIFEST_KEYS, "Plugin manifest")

    name = data["name"]
    if not isinstance(name, str) or not _PLUGIN_NAME_PATTERN.fullmatch(name):
        raise PluginManifestError(
            "Plugin name must be a lowercase namespace identifier."
        )
    version = data["version"]
    if not isinstance(version, str) or not _VERSION_PATTERN.fullmatch(version):
        raise PluginManifestError("Plugin version must use semantic versioning.")
    description = _description(data["description"], "Plugin description")

    raw_tools = data["tools"]
    if (
        isinstance(raw_tools, (str, bytes))
        or not isinstance(raw_tools, Sequence)
        or not raw_tools
    ):
        raise PluginManifestError("Plugin tools must be a non-empty list.")
    if len(raw_tools) > MAX_TOOLS:
        raise PluginManifestError(f"Plugin manifests support at most {MAX_TOOLS} tools.")

    tools = tuple(_parse_tool(name, raw_tool) for raw_tool in raw_tools)
    tool_names = [tool.name for tool in tools]
    if len(set(tool_names)) != len(tool_names):
        raise PluginManifestError("Plugin tool names must be unique.")
    return PluginManifest(
        name=name,
        version=version,
        description=description,
        tools=tools,
    )


def _parse_tool(plugin_name: str, raw_tool: Any) -> PluginToolManifest:
    if not isinstance(raw_tool, Mapping):
        raise PluginManifestError("Each plugin tool must be a mapping.")
    data = dict(raw_tool)
    missing = _REQUIRED_TOOL_KEYS - set(data)
    if missing:
        raise PluginManifestError("Plugin tool is missing required keys.")
    if set(data) - _TOOL_KEYS:
        raise PluginManifestError("Plugin tool contains unknown keys.")

    name = data["name"]
    if (
        not isinstance(name, str)
        or len(name) > MAX_TOOL_NAME_CHARACTERS
        or not _TOOL_NAME_PATTERN.fullmatch(name)
        or not name.startswith(f"{plugin_name}.")
    ):
        raise PluginManifestError(
            f"Plugin tool names must use the '{plugin_name}.' namespace."
        )
    description = _description(
        data.get("description", f"Plugin tool {name}."),
        f"Plugin tool '{name}' description",
    )

    entrypoint = data["entrypoint"]
    if not isinstance(entrypoint, str) or not _ENTRYPOINT_PATTERN.fullmatch(entrypoint):
        raise PluginManifestError(
            f"Plugin tool '{name}' entrypoint must use local module:function syntax."
        )
    parameters = _parameters(data.get("parameters", {}), name)
    permissions = _permissions(data["permissions"], name)
    return PluginToolManifest(
        name=name,
        description=description,
        entrypoint=entrypoint,
        parameters=parameters,
        permissions=permissions,
    )


def _parameters(raw_parameters: Any, tool_name: str) -> dict[str, Any]:
    if not isinstance(raw_parameters, Mapping):
        raise PluginManifestError(
            f"Plugin tool '{tool_name}' parameters must be a JSON-schema object."
        )
    parameters = dict(raw_parameters)
    schema_type = parameters.get("type")
    if schema_type is None:
        parameters["type"] = "object"
    elif schema_type != "object":
        raise PluginManifestError(
            f"Plugin tool '{tool_name}' parameters must describe an object."
        )
    properties = parameters.setdefault("properties", {})
    if not isinstance(properties, Mapping):
        raise PluginManifestError(
            f"Plugin tool '{tool_name}' schema properties must be an object."
        )
    required = parameters.get("required", [])
    if (
        isinstance(required, (str, bytes))
        or not isinstance(required, Sequence)
        or not all(isinstance(item, str) for item in required)
    ):
        raise PluginManifestError(
            f"Plugin tool '{tool_name}' schema required must be a list of names."
        )
    try:
        encoded = json.dumps(
            parameters,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise PluginManifestError(
            f"Plugin tool '{tool_name}' schema must be JSON-serializable."
        ) from exc
    if len(encoded) > MAX_SCHEMA_CHARACTERS:
        raise PluginManifestError(f"Plugin tool '{tool_name}' schema is too large.")
    if _contains_remote_reference(parameters):
        raise PluginManifestError("Plugin schemas may not contain remote references.")
    return parameters


def _permissions(raw_permissions: Any, tool_name: str) -> PluginPermissions:
    if not isinstance(raw_permissions, Mapping):
        raise PluginManifestError(
            f"Plugin tool '{tool_name}' permissions must be a mapping."
        )
    data = dict(raw_permissions)
    _require_exact_keys(data, _PERMISSION_KEYS, "Plugin permissions")
    filesystem = data["filesystem"]
    if not isinstance(filesystem, str) or filesystem not in {
        "none",
        "read",
        "write",
    }:
        raise PluginManifestError(
            "Plugin filesystem permission must be one of: none, read, write."
        )
    commands = data["commands"]
    network = data["network"]
    if not isinstance(commands, bool) or not isinstance(network, bool):
        raise PluginManifestError(
            "Plugin command and network permissions must be booleans."
        )
    return PluginPermissions(
        filesystem=filesystem,
        commands=commands,
        network=network,
    )


def _description(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PluginManifestError(f"{label} must be a non-empty string.")
    description = value.strip()
    if len(description) > MAX_DESCRIPTION_CHARACTERS:
        raise PluginManifestError(f"{label} exceeds the size limit.")
    return description


def _require_exact_keys(
    data: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    missing = expected - set(data)
    unknown = set(data) - expected
    if missing:
        raise PluginManifestError(f"{label} is missing required keys.")
    if unknown:
        raise PluginManifestError(f"{label} contains unknown keys.")


def _contains_remote_reference(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                key == "$ref"
                and isinstance(item, str)
                and item.lower().startswith(("http://", "https://"))
            ):
                return True
            if _contains_remote_reference(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_remote_reference(item) for item in value)
    return False
