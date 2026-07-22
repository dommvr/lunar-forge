"""Disabled-by-default foundations for explicit local plugins."""

from lunar_forge.plugins.loader import (
    InspectedPlugin,
    LoadedPlugin,
    PluginConfig,
    PluginConfigEntry,
    PluginConfigError,
    inspect_configured_plugins,
    load_enabled_plugins,
    load_plugin_config,
    parse_plugin_config,
    plugin_config_path,
)
from lunar_forge.plugins.manifest import (
    PluginManifest,
    PluginManifestError,
    PluginPermissions,
    PluginToolManifest,
    load_plugin_manifest,
    parse_plugin_manifest,
)
from lunar_forge.plugins.registry import (
    EntrypointResolver,
    PluginRegistrationError,
    build_plugin_diagnostic,
    register_plugin_tools,
    resolve_local_plugin_entrypoint,
)
from lunar_forge.plugins.sandbox import PluginHandler, invoke_plugin_handler

__all__ = [
    "EntrypointResolver",
    "InspectedPlugin",
    "LoadedPlugin",
    "PluginConfig",
    "PluginConfigEntry",
    "PluginConfigError",
    "PluginHandler",
    "PluginManifest",
    "PluginManifestError",
    "PluginPermissions",
    "PluginRegistrationError",
    "PluginToolManifest",
    "build_plugin_diagnostic",
    "inspect_configured_plugins",
    "invoke_plugin_handler",
    "load_enabled_plugins",
    "load_plugin_config",
    "load_plugin_manifest",
    "parse_plugin_config",
    "parse_plugin_manifest",
    "plugin_config_path",
    "register_plugin_tools",
    "resolve_local_plugin_entrypoint",
]
