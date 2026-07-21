"""Disabled-by-default foundations for explicit local plugins."""

from lunar_forge.plugins.loader import (
    LoadedPlugin,
    PluginConfig,
    PluginConfigEntry,
    PluginConfigError,
    load_enabled_plugins,
    load_plugin_config,
    parse_plugin_config,
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
    register_plugin_tools,
    resolve_local_plugin_entrypoint,
)
from lunar_forge.plugins.sandbox import PluginHandler, invoke_plugin_handler

__all__ = [
    "EntrypointResolver",
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
    "invoke_plugin_handler",
    "load_enabled_plugins",
    "load_plugin_config",
    "load_plugin_manifest",
    "parse_plugin_config",
    "parse_plugin_manifest",
    "register_plugin_tools",
    "resolve_local_plugin_entrypoint",
]
