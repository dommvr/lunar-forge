"""Register explicitly enabled local plugin tools without bypassing permissions."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from lunar_forge.permissions import PermissionLevel
from lunar_forge.plugins.loader import (
    LoadedPlugin,
    PluginConfigError,
    inspect_configured_plugins,
    load_plugin_config,
    plugin_config_path,
)
from lunar_forge.plugins.manifest import PluginPermissions, PluginToolManifest
from lunar_forge.plugins.sandbox import PluginHandler, invoke_plugin_handler
from lunar_forge.tools.files import safe_path
from lunar_forge.tools.registry import (
    MAX_REGISTERED_TOOLS,
    Tool,
    ToolRegistry,
    provider_safe_tool_name,
)


EntrypointResolver = Callable[[LoadedPlugin, PluginToolManifest], PluginHandler]
MAX_PLUGIN_SOURCE_CHARACTERS = 1_000_000
MAX_PLUGIN_DIAGNOSTIC_ERROR_CHARACTERS = 2_000


class PluginRegistrationError(ValueError):
    """Raised before any invalid plugin tool is exposed to the model."""


def build_plugin_diagnostic(
    project_root: str | Path,
    *,
    globally_enabled: bool,
) -> dict[str, Any]:
    """Inspect explicit plugin manifests without importing or executing code."""
    root = Path(project_root).expanduser().resolve()
    try:
        plugins_path = plugin_config_path(root)
        config_files = [
            {
                "scope": scope,
                "path": str(path),
                "loaded": path.is_file(),
            }
            for scope, path in (
                ("user", Path.home() / ".lunar-forge" / "config.yaml"),
                ("project", safe_path(root, ".agent/config.yaml")),
            )
        ]
    except (OSError, PluginConfigError, PermissionError) as exc:
        return _plugin_diagnostic_error(exc)

    base: dict[str, Any] = {
        "ok": True,
        "status": "passed" if globally_enabled else "disabled",
        "plugins_enabled": globally_enabled,
        "config_files": config_files,
        "plugins_config": {
            "path": str(plugins_path),
            "loaded": plugins_path.is_file(),
        },
        "enabled_plugins": [],
        "disabled_plugins": [],
        "plugins": [],
        "discovered_tools": [],
        "errors": [],
        "truncated": False,
    }
    try:
        config = load_plugin_config(root)
    except (OSError, PluginConfigError, PermissionError) as exc:
        base["ok"] = False
        base["status"] = "failed"
        base["errors"].append(
            {
                "stage": "config",
                "error": _bounded_plugin_error(exc),
            }
        )
        return base

    base["enabled_plugins"] = [
        entry.name
        for _, entry in sorted(config.plugins.items())
        if entry.enabled
    ]
    base["disabled_plugins"] = [
        entry.name
        for _, entry in sorted(config.plugins.items())
        if not entry.enabled
    ]

    model_names: dict[str, str] = {}
    for inspected in inspect_configured_plugins(root, config):
        entry = inspected.entry
        manifest_path = (
            str(inspected.manifest_path)
            if inspected.manifest_path is not None
            else entry.manifest
        )
        plugin_summary: dict[str, Any] = {
            "name": entry.name,
            "enabled": entry.enabled,
            "effective_enabled": globally_enabled and entry.enabled,
            "manifest_path": manifest_path,
            "manifest_loaded": inspected.manifest is not None,
        }
        base["plugins"].append(plugin_summary)
        if inspected.error is not None:
            base["errors"].append(
                {
                    "stage": "manifest",
                    "plugin": entry.name,
                    "manifest_path": manifest_path,
                    "error": inspected.error[
                        :MAX_PLUGIN_DIAGNOSTIC_ERROR_CHARACTERS
                    ],
                }
            )
            continue
        assert inspected.manifest is not None
        for definition in inspected.manifest.tools:
            if len(base["discovered_tools"]) >= MAX_REGISTERED_TOOLS:
                base["truncated"] = True
                base["errors"].append(
                    {
                        "stage": "discovery",
                        "error": (
                            "Plugin diagnostics support at most "
                            f"{MAX_REGISTERED_TOOLS} discovered tools."
                        ),
                    }
                )
                break
            model_name = provider_safe_tool_name(definition.name)
            colliding_internal_name = model_names.get(model_name)
            if (
                colliding_internal_name is not None
                and colliding_internal_name != definition.name
            ):
                base["errors"].append(
                    {
                        "stage": "discovery",
                        "plugin": entry.name,
                        "error": (
                            "Provider-safe tool name collision: "
                            f"{colliding_internal_name!r} and "
                            f"{definition.name!r} both map to {model_name!r}."
                        ),
                    }
                )
            else:
                model_names[model_name] = definition.name
            base["discovered_tools"].append(
                {
                    "plugin": entry.name,
                    "plugin_enabled": entry.enabled,
                    "effective_enabled": globally_enabled and entry.enabled,
                    "internal_tool_name": definition.name,
                    "model_tool_name": model_name,
                }
            )
        if base["truncated"]:
            break

    if base["errors"]:
        base["ok"] = False
        base["status"] = "failed"
    elif not globally_enabled:
        base["note"] = (
            "Plugins are disabled in config.yaml; manifests were inspected but "
            "plugin code was not loaded."
        )
    return base


def _plugin_diagnostic_error(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "failed",
        "plugins_enabled": False,
        "config_files": [],
        "plugins_config": {"path": None, "loaded": False},
        "enabled_plugins": [],
        "disabled_plugins": [],
        "plugins": [],
        "discovered_tools": [],
        "errors": [
            {"stage": "config", "error": _bounded_plugin_error(exc)}
        ],
        "truncated": False,
    }


def _bounded_plugin_error(exc: Exception) -> str:
    if isinstance(exc, (PluginConfigError, PermissionError, OSError)):
        message = str(exc)
    else:
        message = f"Plugin diagnostic failed with {type(exc).__name__}."
    return message[:MAX_PLUGIN_DIAGNOSTIC_ERROR_CHARACTERS]


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
    return _bind_project_root_context(handler, plugin, definition)


def _bind_project_root_context(
    handler: PluginHandler,
    plugin: LoadedPlugin,
    definition: PluginToolManifest,
) -> PluginHandler:
    """Bind a confined root for filesystem tools that explicitly request it."""
    if definition.permissions.filesystem == "none":
        return handler
    try:
        root_parameter = inspect.signature(handler).parameters.get("_project_root")
    except (TypeError, ValueError):
        return handler
    if root_parameter is None or root_parameter.kind not in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }:
        return handler
    if plugin.project_root is None:
        raise PluginRegistrationError(
            "Plugin project-root context is unavailable."
        )

    project_root = plugin.project_root.expanduser().resolve()

    def handler_with_project_root(**arguments: Any) -> Any:
        if "_project_root" in arguments:
            raise TypeError("Plugin project-root context cannot be overridden.")
        return handler(_project_root=project_root, **arguments)

    return handler_with_project_root


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
        read_only_extension=_declares_read_only_capabilities(
            definition.permissions
        ),
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
