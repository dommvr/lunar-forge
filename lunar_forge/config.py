from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "litellm"
    model: str = "openai/gpt-5.5"
    api_key_env: str | None = "OPENAI_API_KEY"
    api_base: str | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str = "local"  # local | docker | no-command
    allow_network: bool = False


@dataclass(frozen=True)
class PermissionConfig:
    mode: str = "default"  # plan | default | yes | no-command


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a YAML object: {path}")

    return data


def load_config(
    project_root: Path,
    cli_overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    """Load configuration in increasing order of precedence.

    Raw API keys are deliberately excluded. ``api_key_env`` identifies the
    environment variable that a model client may read only when making a call.
    """
    user_config_path = Path.home() / ".lunar-forge" / "config.yaml"
    project_config_path = project_root / ".agent" / "config.yaml"

    merged = _default_config()
    merged = deep_merge(merged, _environment_config(os.environ))
    merged = deep_merge(merged, _read_yaml(user_config_path))
    merged = deep_merge(merged, _read_yaml(project_config_path))
    if cli_overrides:
        merged = deep_merge(merged, dict(cli_overrides))

    model_data = _section(merged, "model")
    runtime_data = _section(merged, "runtime")
    permissions_data = _section(merged, "permissions")

    if "api_key" in model_data:
        raise ValueError(
            "Raw API keys are not supported in config; use model.api_key_env."
        )

    return AppConfig(
        model=ModelConfig(
            provider=str(model_data["provider"]),
            model=str(model_data["model"]),
            api_key_env=_optional_string(model_data.get("api_key_env")),
            api_base=_optional_string(model_data.get("api_base")),
        ),
        runtime=RuntimeConfig(
            mode=_runtime_mode(runtime_data["mode"]),
            allow_network=_as_bool(
                runtime_data["allow_network"],
                "runtime.allow_network",
            ),
        ),
        permissions=PermissionConfig(
            mode=str(permissions_data["mode"]),
        ),
    )


def _default_config() -> dict[str, Any]:
    return {
        "model": {
            "provider": "litellm",
            "model": "openai/gpt-5.5",
            "api_key_env": "OPENAI_API_KEY",
            "api_base": None,
        },
        "runtime": {
            "mode": "local",
            "allow_network": False,
        },
        "permissions": {
            "mode": "default",
        },
    }


def _environment_config(environ: Mapping[str, str]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    mappings = {
        "LUNAR_FORGE_MODEL_PROVIDER": ("model", "provider"),
        "LUNAR_FORGE_MODEL": ("model", "model"),
        "LUNAR_FORGE_API_KEY_ENV": ("model", "api_key_env"),
        "LUNAR_FORGE_API_BASE": ("model", "api_base"),
        "LUNAR_FORGE_RUNTIME_MODE": ("runtime", "mode"),
        "LUNAR_FORGE_ALLOW_NETWORK": ("runtime", "allow_network"),
        "LUNAR_FORGE_PERMISSION_MODE": ("permissions", "mode"),
    }

    for variable, (section, key) in mappings.items():
        if variable in environ:
            config.setdefault(section, {})[key] = environ[variable]

    return config


def _section(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = config.get(name, {})
    if not isinstance(section, Mapping):
        raise ValueError(f"Config section must be a YAML object: {name}")
    return section


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _runtime_mode(value: Any) -> str:
    mode = str(value).strip().lower()
    if mode not in {"local", "docker", "no-command"}:
        raise ValueError(
            "runtime.mode must be one of: local, docker, no-command."
        )
    return mode


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"Config value must be a boolean: {name}")


def deep_merge(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(base)

    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result
