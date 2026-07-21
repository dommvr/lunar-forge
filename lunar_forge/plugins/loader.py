"""Explicit project-local plugin configuration and manifest loading."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from lunar_forge.plugins.manifest import PluginManifest, PluginManifestError
from lunar_forge.plugins.manifest import load_plugin_manifest
from lunar_forge.tools.files import safe_path


PLUGIN_CONFIG_PATH = ".agent/plugins.yaml"
MAX_PLUGIN_CONFIG_CHARACTERS = 500_000
MAX_CONFIGURED_PLUGINS = 50
_CONFIG_KEYS = frozenset({"plugins"})
_PLUGIN_ENTRY_KEYS = frozenset({"manifest", "enabled"})
_PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class PluginConfigError(ValueError):
    """Raised when explicit plugin configuration is invalid or unsafe."""


@dataclass(frozen=True)
class PluginConfigEntry:
    """An explicit manifest path and its disabled-by-default switch."""

    name: str
    manifest: str
    enabled: bool = False


@dataclass(frozen=True)
class PluginConfig:
    """Project plugin configuration. An empty instance loads nothing."""

    plugins: Mapping[str, PluginConfigEntry] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "plugins", MappingProxyType(dict(self.plugins)))

    @property
    def enabled_plugins(self) -> tuple[PluginConfigEntry, ...]:
        return tuple(
            plugin
            for _, plugin in sorted(self.plugins.items())
            if plugin.enabled is True
        )


@dataclass(frozen=True)
class LoadedPlugin:
    """A validated manifest loaded from one configured local path."""

    manifest: PluginManifest
    manifest_path: Path


def load_plugin_config(project_root: str | Path) -> PluginConfig:
    """Load only the project's explicit .agent/plugins.yaml configuration."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise PluginConfigError("Project root must be an existing directory.")
    config_path = safe_path(root, PLUGIN_CONFIG_PATH)
    if not config_path.exists():
        return PluginConfig()
    if not config_path.is_file():
        raise PluginConfigError("Plugin config must be a file.")
    try:
        content = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PluginConfigError("Could not read plugin config.") from exc
    if len(content) > MAX_PLUGIN_CONFIG_CHARACTERS:
        raise PluginConfigError("Plugin config exceeds the size limit.")
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise PluginConfigError("Plugin config contains invalid YAML.") from exc
    return parse_plugin_config(document)


def parse_plugin_config(document: Any) -> PluginConfig:
    """Validate decoded config without scanning for manifests."""
    if document is None:
        return PluginConfig()
    if not isinstance(document, Mapping):
        raise PluginConfigError("Plugin config must be a mapping.")
    data = dict(document)
    if set(data) - _CONFIG_KEYS:
        raise PluginConfigError("Plugin config contains unknown keys.")
    raw_plugins = data.get("plugins", {})
    if not isinstance(raw_plugins, Mapping):
        raise PluginConfigError("Plugin config plugins must be a mapping.")
    if len(raw_plugins) > MAX_CONFIGURED_PLUGINS:
        raise PluginConfigError(
            f"Plugin config supports at most {MAX_CONFIGURED_PLUGINS} plugins."
        )

    plugins: dict[str, PluginConfigEntry] = {}
    for name, raw_entry in raw_plugins.items():
        if not isinstance(name, str) or not _PLUGIN_NAME_PATTERN.fullmatch(name):
            raise PluginConfigError(
                "Configured plugin names must be lowercase namespace identifiers."
            )
        if not isinstance(raw_entry, Mapping):
            raise PluginConfigError(f"Plugin config entry '{name}' must be a mapping.")
        entry_data = dict(raw_entry)
        if set(entry_data) - _PLUGIN_ENTRY_KEYS:
            raise PluginConfigError(
                f"Plugin config entry '{name}' contains unknown keys."
            )
        if "manifest" not in entry_data:
            raise PluginConfigError(
                f"Plugin config entry '{name}' requires a manifest path."
            )
        manifest = entry_data["manifest"]
        if not isinstance(manifest, str) or not manifest.strip():
            raise PluginConfigError("Plugin manifest paths must be non-empty strings.")
        manifest = manifest.strip()
        _validate_manifest_reference(manifest)
        enabled = entry_data.get("enabled", False)
        if not isinstance(enabled, bool):
            raise PluginConfigError("Plugin enabled settings must be booleans.")
        plugins[name] = PluginConfigEntry(
            name=name,
            manifest=manifest,
            enabled=enabled,
        )
    return PluginConfig(plugins=plugins)


def load_enabled_plugins(
    project_root: str | Path,
    config: PluginConfig | None = None,
) -> tuple[LoadedPlugin, ...]:
    """Load validated manifests only for explicitly enabled config entries."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise PluginConfigError("Project root must be an existing directory.")
    resolved_config = config or load_plugin_config(root)
    loaded: list[LoadedPlugin] = []
    for entry in resolved_config.enabled_plugins:
        if not _PLUGIN_NAME_PATTERN.fullmatch(entry.name):
            raise PluginConfigError(
                "Configured plugin names must be lowercase namespace identifiers."
            )
        _validate_manifest_reference(entry.manifest)
        try:
            manifest_path = safe_path(root, entry.manifest)
            manifest = load_plugin_manifest(manifest_path)
        except PermissionError:
            raise
        except PluginManifestError as exc:
            raise PluginConfigError(
                f"Configured plugin '{entry.name}' has an invalid manifest."
            ) from exc
        if manifest.name != entry.name:
            raise PluginConfigError(
                f"Configured plugin '{entry.name}' does not match its manifest name."
            )
        loaded.append(
            LoadedPlugin(
                manifest=manifest,
                manifest_path=manifest_path,
            )
        )
    return tuple(loaded)


def _validate_manifest_reference(manifest: str) -> None:
    if not isinstance(manifest, str) or not manifest.strip():
        raise PluginConfigError("Plugin manifest paths must be non-empty strings.")
    if manifest != manifest.strip():
        raise PluginConfigError("Plugin manifest paths must not contain outer spaces.")
    manifest_path = Path(manifest)
    if manifest_path.is_absolute() or "://" in manifest:
        raise PluginConfigError(
            "Plugin manifest paths must be project-relative local paths."
        )
    if manifest_path.suffix.lower() not in {".yaml", ".yml"}:
        raise PluginConfigError("Plugin manifests must be YAML files.")
