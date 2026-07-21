"""Register explicitly enabled local plugin tools without bypassing permissions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from lunar_forge.permissions import PermissionLevel
from lunar_forge.plugins.loader import LoadedPlugin
from lunar_forge.plugins.manifest import PluginPermissions, PluginToolManifest
from lunar_forge.plugins.sandbox import PluginHandler, invoke_plugin_handler
from lunar_forge.tools.files import safe_path
from lunar_forge.tools.registry import Tool, ToolRegistry


EntrypointResolver = Callable[[LoadedPlugin, PluginToolManifest], PluginHandler]
MAX_PLUGIN_SOURCE_CHARACTERS = 1_000_000


class PluginRegistrationError(ValueError):
    """Raised before any invalid plugin tool is exposed to the model."""


def resolve_local_plugin_entrypoint(
    plugin: LoadedPlugin,
    definition: PluginToolManifest,
) -> PluginHandler:
    """Load an explicitly configured Python entrypoint from its plugin bundle.

    Loading is deliberately deferred until after ToolRegistry approval. The
    module path is derived from the validated entrypoint and confined to the
    manifest's directory; no directory is added to ``sys.path`` and no import
    cache is mutated.
    """
    module_name, function_name = definition.entrypoint.split(":", maxsplit=1)
    bundle_root = plugin.manifest_path.parent.resolve()
    module_parts = module_name.split(".")
    module_file = safe_path(
        bundle_root,
        Path(*module_parts).with_suffix(".py"),
    )
    package_file = safe_path(
        bundle_root,
        Path(*module_parts) / "__init__.py",
    )
    source_file = module_file if module_file.is_file() else package_file
    if not source_file.is_file():
        raise PluginRegistrationError(
            f"Plugin module was not found for entrypoint: {definition.entrypoint}"
        )
    try:
        source = source_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PluginRegistrationError("Plugin module could not be read.") from exc
    if len(source) > MAX_PLUGIN_SOURCE_CHARACTERS:
        raise PluginRegistrationError("Plugin module exceeds the source size limit.")

    digest = hashlib.sha256(str(source_file).encode("utf-8")).hexdigest()[:16]
    module = ModuleType(f"_lunar_forge_plugin_{digest}")
    module.__file__ = str(source_file)
    module.__package__ = ""
    try:
        code = compile(source, str(source_file), "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        raise PluginRegistrationError(
            f"Plugin module loading failed with {type(exc).__name__}."
        ) from exc
    handler = getattr(module, function_name, None)
    if not callable(handler):
        raise PluginRegistrationError("Plugin entrypoint is not callable.")
    return handler


def register_plugin_tools(
    registry: ToolRegistry,
    plugins: tuple[LoadedPlugin, ...],
    resolver: EntrypointResolver,
    *,
    read_only_only: bool = False,
) -> tuple[str, ...]:
    """Register configured tools while deferring code resolution until approval."""
    pending: list[Tool] = []
    pending_names: set[str] = set()
    existing_names = set(registry.names())
    for plugin in plugins:
        for definition in plugin.manifest.tools:
            declared_read_only = _declares_read_only_capabilities(
                definition.permissions
            )
            if read_only_only and not declared_read_only:
                continue
            if definition.name in existing_names or definition.name in pending_names:
                raise PluginRegistrationError(
                    f"Plugin tool name is already registered: {definition.name}"
                )
            pending.append(
                _registry_tool(
                    plugin,
                    definition,
                    resolver,
                )
            )
            pending_names.add(definition.name)

    for tool in pending:
        registry.register(tool)
    return tuple(tool.name for tool in pending)


def _registry_tool(
    plugin: LoadedPlugin,
    definition: PluginToolManifest,
    resolver: EntrypointResolver,
) -> Tool:
    def call_plugin_tool(**arguments: Any) -> dict[str, Any]:
        try:
            handler = resolver(plugin, definition)
        except Exception as exc:
            return {
                "ok": False,
                "error": (
                    "Plugin tool resolution failed with "
                    f"{type(exc).__name__}."
                ),
            }
        if not callable(handler):
            return {
                "ok": False,
                "error": "Plugin tool did not resolve to a callable.",
            }
        return invoke_plugin_handler(handler, arguments)

    return Tool(
        name=definition.name,
        description=(
            f"{definition.description} "
            f"Declared permissions: {_permission_summary(definition.permissions)}."
        ),
        parameters=dict(definition.parameters),
        handler=call_plugin_tool,
        # Calling local plugin code is always treated as execution. This keeps
        # no-command restrictions and normal approval prompts intact even when
        # the plugin declares no additional capabilities. Plugin code is never
        # plan-safe because declarations cannot enforce in-process behavior.
        permission=PermissionLevel.EXECUTE,
        plan_safe=False,
    )


def _permission_summary(permissions: PluginPermissions) -> str:
    return (
        f"filesystem={permissions.filesystem}, "
        f"commands={str(permissions.commands).lower()}, "
        f"network={str(permissions.network).lower()}"
    )


def _declares_read_only_capabilities(permissions: PluginPermissions) -> bool:
    return (
        permissions.filesystem in {"none", "read"}
        and not permissions.commands
        and not permissions.network
    )
