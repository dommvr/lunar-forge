import copy
import json
from pathlib import Path

import pytest
import yaml

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig, PluginRuntimeConfig
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.permissions import PermissionLevel, PermissionManager
from lunar_forge.plugins.loader import (
    MAX_CONFIGURED_PLUGINS,
    LoadedPlugin,
    PluginConfig,
    PluginConfigEntry,
    PluginConfigError,
    load_enabled_plugins,
    load_plugin_config,
    parse_plugin_config,
)
from lunar_forge.plugins.manifest import (
    PluginManifestError,
    parse_plugin_manifest,
)
from lunar_forge.plugins.registry import (
    register_plugin_tools,
    resolve_local_plugin_entrypoint,
)
from lunar_forge.plugins.sandbox import (
    MAX_ARGUMENT_CHARACTERS,
    MAX_OUTPUT_CHARACTERS,
    invoke_plugin_handler,
)
from lunar_forge.tools.registry import ToolRegistry, create_tool_registry


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.tool_schemas = []

    def complete(self, messages, tools=None):
        self.tool_schemas.append(list(tools or []))
        return self.responses.pop(0)


def _manifest():
    return {
        "name": "example",
        "version": "0.1.0",
        "description": "Example local plugin.",
        "tools": [
            {
                "name": "example.echo",
                "description": "Echo a message.",
                "entrypoint": "example_plugin:echo",
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                    "additionalProperties": False,
                },
                "permissions": {
                    "filesystem": "read",
                    "commands": False,
                    "network": False,
                },
            }
        ],
    }


def _write_manifest(project: Path, manifest=None) -> Path:
    path = project / "plugin_packs" / "example" / "plugin.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(_manifest() if manifest is None else manifest),
        encoding="utf-8",
    )
    return path


def _write_config(
    project: Path,
    *,
    manifest="plugin_packs/example/plugin.yaml",
    enabled=None,
) -> Path:
    entry = {"manifest": manifest}
    if enabled is not None:
        entry["enabled"] = enabled
    config_path = project / ".agent" / "plugins.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"plugins": {"example": entry}}),
        encoding="utf-8",
    )
    return config_path


def _loaded_plugin(tmp_path) -> LoadedPlugin:
    manifest_path = _write_manifest(tmp_path)
    return LoadedPlugin(
        manifest=parse_plugin_manifest(_manifest()),
        manifest_path=manifest_path,
    )


def test_plugins_default_to_disabled_and_are_not_auto_discovered(tmp_path):
    _write_manifest(tmp_path)

    config = load_plugin_config(tmp_path)
    loaded = load_enabled_plugins(tmp_path, config)

    assert dict(config.plugins) == {}
    assert config.enabled_plugins == ()
    assert loaded == ()


def test_configured_plugin_is_still_disabled_when_enabled_is_omitted(tmp_path):
    _write_config(tmp_path, enabled=None)
    # A disabled plugin manifest is not even read.
    manifest_path = tmp_path / "plugin_packs/example/plugin.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("not: a valid manifest\n", encoding="utf-8")

    config = load_plugin_config(tmp_path)

    assert config.plugins["example"].enabled is False
    assert load_enabled_plugins(tmp_path, config) == ()


def test_explicitly_enabled_local_manifest_is_loaded(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    _write_config(tmp_path, enabled=True)

    loaded = load_enabled_plugins(tmp_path)

    assert len(loaded) == 1
    assert loaded[0].manifest.name == "example"
    assert loaded[0].manifest_path == manifest_path.resolve()
    assert loaded[0].manifest.tools[0].name == "example.echo"


def test_plugin_config_rejects_remote_and_absolute_manifest_paths(tmp_path):
    for manifest in ("https://example.com/plugin.yaml", str(tmp_path / "plugin.yaml")):
        with pytest.raises(PluginConfigError, match="project-relative local"):
            parse_plugin_config(
                {
                    "plugins": {
                        "example": {
                            "manifest": manifest,
                            "enabled": True,
                        }
                    }
                }
            )


def test_plugin_config_bounds_explicit_plugin_count():
    plugins = {
        f"plugin-{index}": {
            "manifest": f"plugins/plugin-{index}/plugin.yaml",
            "enabled": False,
        }
        for index in range(MAX_CONFIGURED_PLUGINS + 1)
    }

    with pytest.raises(PluginConfigError, match="at most"):
        parse_plugin_config({"plugins": plugins})


def test_programmatic_config_cannot_bypass_local_manifest_path_check(tmp_path):
    config = PluginConfig(
        plugins={
            "example": PluginConfigEntry(
                name="example",
                manifest=str(tmp_path / "plugin.yaml"),
                enabled=True,
            )
        }
    )

    with pytest.raises(PluginConfigError, match="project-relative local"):
        load_enabled_plugins(tmp_path, config)


def test_plugin_manifest_cannot_escape_project_root(tmp_path):
    outside = tmp_path.parent / "outside-plugin.yaml"
    outside.write_text(yaml.safe_dump(_manifest()), encoding="utf-8")
    config = parse_plugin_config(
        {
            "plugins": {
                "example": {
                    "manifest": "../outside-plugin.yaml",
                    "enabled": True,
                }
            }
        }
    )

    with pytest.raises(PermissionError, match="outside the project root"):
        load_enabled_plugins(tmp_path, config)


def test_configured_name_must_match_manifest_name(tmp_path):
    manifest = _manifest()
    manifest["name"] = "different"
    manifest["tools"][0]["name"] = "different.echo"
    _write_manifest(tmp_path, manifest)
    _write_config(tmp_path, enabled=True)

    with pytest.raises(PluginConfigError, match="does not match"):
        load_enabled_plugins(tmp_path)


def test_valid_manifest_defines_schema_entrypoint_and_permissions():
    manifest = parse_plugin_manifest(_manifest())
    tool = manifest.tools[0]

    assert manifest.name == "example"
    assert manifest.version == "0.1.0"
    assert tool.entrypoint == "example_plugin:echo"
    assert tool.parameters["type"] == "object"
    assert tool.parameters["required"] == ["message"]
    assert tool.permissions.filesystem == "read"
    assert tool.permissions.commands is False
    assert tool.permissions.network is False


def test_agents_md_minimal_tool_shape_gets_safe_schema_defaults():
    manifest = _manifest()
    manifest["tools"][0].pop("description")
    manifest["tools"][0].pop("parameters")

    tool = parse_plugin_manifest(manifest).tools[0]

    assert tool.description == "Plugin tool example.echo."
    assert tool.parameters == {"type": "object", "properties": {}}


def test_manifest_schema_rejects_non_finite_numbers():
    manifest = _manifest()
    manifest["tools"][0]["parameters"]["properties"]["score"] = {
        "type": "number",
        "default": float("nan"),
    }

    with pytest.raises(PluginManifestError, match="JSON-serializable"):
        parse_plugin_manifest(manifest)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda manifest: manifest["tools"][0].update(
                {"name": "other.echo"}
            ),
            "namespace",
        ),
        (
            lambda manifest: manifest["tools"][0].update(
                {"entrypoint": "https://example.com/plugin.py"}
            ),
            "module:function",
        ),
        (
            lambda manifest: manifest["tools"][0].update(
                {"parameters": {"type": "array"}}
            ),
            "describe an object",
        ),
        (
            lambda manifest: manifest["tools"][0]["parameters"].update(
                {"$ref": "https://example.com/schema.json"}
            ),
            "remote references",
        ),
        (
            lambda manifest: manifest["tools"][0]["permissions"].pop("network"),
            "missing required keys",
        ),
        (
            lambda manifest: manifest["tools"][0]["permissions"].update(
                {"commands": "yes"}
            ),
            "must be booleans",
        ),
        (
            lambda manifest: manifest["tools"][0]["permissions"].update(
                {"filesystem": ["read"]}
            ),
            "filesystem permission",
        ),
    ),
)
def test_invalid_plugin_manifest_is_rejected_before_loading(mutate, message):
    manifest = copy.deepcopy(_manifest())
    mutate(manifest)

    with pytest.raises(PluginManifestError, match=message):
        parse_plugin_manifest(manifest)


def test_manifest_loading_does_not_import_plugin_entrypoint(tmp_path):
    marker = tmp_path / "imported.txt"
    module = tmp_path / "example_plugin.py"
    module.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    _write_manifest(tmp_path)
    _write_config(tmp_path, enabled=True)

    loaded = load_enabled_plugins(tmp_path)

    assert len(loaded) == 1
    assert not marker.exists()


def test_registered_plugin_tool_is_namespaced_and_registry_compatible(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    calls = []

    def resolver(plugin, definition):
        assert plugin is loaded
        assert definition.entrypoint == "example_plugin:echo"

        def echo(message):
            calls.append(message)
            return {"ok": True, "echo": message}

        return echo

    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: True,
        )
    )

    registered = register_plugin_tools(registry, (loaded,), resolver)
    schema = registry.schemas()[0]["function"]
    result = registry.execute("example_echo", {"message": "hello"})

    assert registered == ("example.echo",)
    assert schema["name"] == "example_echo"
    assert schema["parameters"]["required"] == ["message"]
    assert "filesystem=read" in schema["description"]
    assert registry.get("example.echo").permission is PermissionLevel.EXECUTE
    assert result == {"ok": True, "echo": "hello"}
    assert calls == ["hello"]
    json.dumps(result)


def test_local_entrypoint_is_confined_loaded_and_called_after_approval(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    module = loaded.manifest_path.parent / "example_plugin.py"
    module.write_text(
        "def echo(message):\n    return {'ok': True, 'echo': message}\n",
        encoding="utf-8",
    )
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            approval_callback=lambda request: True,
        )
    )
    register_plugin_tools(
        registry,
        (loaded,),
        resolve_local_plugin_entrypoint,
    )

    result = registry.execute("example.echo", {"message": "local"})

    assert result == {"ok": True, "echo": "local"}


def test_local_entrypoint_symlink_cannot_escape_plugin_bundle(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    outside_module = tmp_path / "outside_plugin.py"
    outside_module.write_text(
        "def echo(message):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    module = loaded.manifest_path.parent / "example_plugin.py"
    try:
        module.symlink_to(outside_module)
    except OSError as exc:
        pytest.skip(f"File symlinks are unavailable on this platform: {exc}")

    with pytest.raises(PermissionError, match="outside the project root"):
        resolve_local_plugin_entrypoint(loaded, loaded.manifest.tools[0])


def test_denied_local_plugin_is_not_imported(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    marker = tmp_path / "plugin-imported.txt"
    (loaded.manifest_path.parent / "example_plugin.py").write_text(
        (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('imported')\n"
            "def echo(message):\n    return {'ok': True}\n"
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            approval_callback=lambda request: False,
        )
    )
    register_plugin_tools(
        registry,
        (loaded,),
        resolve_local_plugin_entrypoint,
    )

    result = registry.execute("example_echo", {"message": "blocked"})

    assert result["permission_denied"] is True
    assert not marker.exists()


def test_permission_denial_prevents_plugin_handler_call(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    calls = []
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="default",
            approval_callback=lambda request: False,
        )
    )
    def resolver(plugin, definition):
        calls.append("resolved")
        return lambda **arguments: calls.append(arguments)

    register_plugin_tools(registry, (loaded,), resolver)

    result = registry.execute("example.echo", {"message": "blocked"})

    assert result["ok"] is False
    assert result["permission_denied"] is True
    assert calls == []


def test_plan_registry_hides_all_plugin_tools_without_resolving_code(tmp_path):
    safe_plugin = _loaded_plugin(tmp_path)
    unsafe_manifest = copy.deepcopy(_manifest())
    unsafe_manifest["name"] = "unsafe"
    unsafe_manifest["tools"][0]["name"] = "unsafe.deploy"
    unsafe_manifest["tools"][0]["permissions"] = {
        "filesystem": "write",
        "commands": True,
        "network": True,
    }
    unsafe_plugin = LoadedPlugin(
        manifest=parse_plugin_manifest(unsafe_manifest),
        manifest_path=tmp_path / "unsafe.yaml",
    )
    resolved = []

    def resolver(plugin, definition):
        resolved.append(definition.name)
        return lambda **arguments: {"ok": True, "arguments": arguments}

    registry = create_tool_registry(
        tmp_path,
        mode="plan",
        approval_callback=lambda request: pytest.fail("Plugin must stay hidden"),
        plugins=(safe_plugin, unsafe_plugin),
        plugin_resolver=resolver,
    )

    assert "list_dir" in registry.names()
    assert "example.echo" not in registry.names()
    assert "unsafe.deploy" not in registry.names()
    schemas = registry.schemas(read_only=True, allow_execute=False)
    schema_names = {schema["function"]["name"] for schema in schemas}
    assert "list_dir" in schema_names
    assert "example.echo" not in schema_names
    assert "unsafe.deploy" not in schema_names
    assert resolved == []


def test_agent_registers_double_opted_in_plugin_and_preserves_builtins(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    _write_config(tmp_path, enabled=True)
    (manifest_path.parent / "example_plugin.py").write_text(
        "def echo(message):\n    return {'ok': True, 'echo': message}\n",
        encoding="utf-8",
    )

    model = SequenceModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="plugin-call",
                        name="example_echo",
                        arguments={"message": "integrated"},
                    ),
                ),
            ),
            ModelResponse(text="Plugin complete."),
        )
    )
    agent = CodeAgent(
        AppConfig(plugins=PluginRuntimeConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda request: True,
    )

    output = agent.run("Use the example plugin", tmp_path)

    first_schema_names = {
        schema["function"]["name"] for schema in model.tool_schemas[0]
    }
    assert "read_file" in first_schema_names
    assert "example_echo" in first_schema_names
    assert all("." not in name for name in first_schema_names)
    assert output.startswith("Plugin complete.")


def test_agent_ignores_per_plugin_enablement_when_global_switch_is_off(tmp_path):
    _write_manifest(tmp_path)
    _write_config(tmp_path, enabled=True)
    model = SequenceModel((ModelResponse(text="No plugin used."),))

    output = CodeAgent(
        AppConfig(),
        model_client=model,
        plugin_resolver=lambda plugin, definition: pytest.fail(
            "Globally disabled plugins must not resolve entrypoints"
        ),
    ).run("Explain the project", tmp_path, mode="plan")

    schema_names = {schema["function"]["name"] for schema in model.tool_schemas[0]}
    assert "example_echo" not in schema_names
    assert output.startswith("No plugin used.")


def test_no_command_mode_blocks_plugin_code_before_resolution(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    resolved = []
    registry = create_tool_registry(
        tmp_path,
        mode="no-command",
        plugins=(loaded,),
        plugin_resolver=lambda plugin, definition: resolved.append(definition.name),
    )

    result = registry.execute("example_echo", {"message": "blocked"})

    assert result["permission_denied"] is True
    assert resolved == []


def test_yes_mode_still_prompts_before_plugin_execution(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    requests = []
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            mode="yes",
            approval_callback=lambda request: requests.append(request) or False,
        )
    )
    register_plugin_tools(
        registry,
        (loaded,),
        lambda plugin, definition: lambda **arguments: {
            "ok": True,
        },
    )

    result = registry.execute("example.echo", {"message": "blocked"})

    assert result["permission_denied"] is True
    assert len(requests) == 1
    assert requests[0].permission is PermissionLevel.EXECUTE
    assert requests[0].tool_name == "example.echo"


def test_plugin_exception_is_contained_without_exposing_message(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    secret = "sensitive-plugin-detail"

    def broken(message):
        raise RuntimeError(secret)

    registry = ToolRegistry(
        permission_manager=PermissionManager(
            approval_callback=lambda request: True,
        )
    )
    register_plugin_tools(
        registry,
        (loaded,),
        lambda plugin, definition: broken,
    )

    result = registry.execute("example.echo", {"message": "hello"})

    assert result == {
        "ok": False,
        "error": "Plugin tool failed with RuntimeError.",
    }
    assert secret not in json.dumps(result)


def test_non_serializable_plugin_result_becomes_tool_error():
    result = invoke_plugin_handler(
        lambda: {"ok": True, "value": object()},
        {},
    )

    assert result == {
        "ok": False,
        "error": "Plugin tool returned a non-serializable result.",
    }


def test_plugin_result_is_bounded_and_json_serializable():
    result = invoke_plugin_handler(
        lambda: {"ok": True, "content": "x" * (MAX_OUTPUT_CHARACTERS * 3)},
        {},
    )

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(json.dumps(result)) <= MAX_OUTPUT_CHARACTERS


def test_oversized_plugin_arguments_are_rejected_before_handler():
    calls = []
    result = invoke_plugin_handler(
        lambda **arguments: calls.append(arguments) or {"ok": True},
        {"content": "x" * (MAX_ARGUMENT_CHARACTERS + 1)},
    )

    assert result == {"ok": False, "error": "Plugin arguments are too large."}
    assert calls == []


def test_failed_resolution_is_returned_as_tool_error_after_approval(tmp_path):
    loaded = _loaded_plugin(tmp_path)
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            approval_callback=lambda request: True,
        )
    )
    register_plugin_tools(
        registry,
        (loaded,),
        lambda plugin, definition: None,
    )

    result = registry.execute("example.echo", {"message": "hello"})

    assert result == {
        "ok": False,
        "error": "Plugin tool did not resolve to a callable.",
    }
