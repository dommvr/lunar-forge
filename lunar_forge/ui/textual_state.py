"""Dependency-free runtime state for one Textual chat process."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from lunar_forge.approvals import ApprovalProvider
from lunar_forge.config import (
    ALLOWED_REASONING_EFFORTS,
    MAX_CONFIG_CHARACTERS,
    AppConfig,
    MCPRuntimeConfig,
    PermissionConfig,
    PluginRuntimeConfig,
    ReasoningConfig,
    SubagentConfig,
)
from lunar_forge.permissions import (
    ApprovalEventCallback,
    PermissionLevel,
    PermissionManager,
)
from lunar_forge.runtime.checkpoints import create_file_checkpoint
from lunar_forge.tools.files import safe_path


ALLOWED_RUNTIME_MODES = ("local", "docker", "no-command")
ALLOWED_PERMISSION_MODES = (
    "default",
    "yes",
    "no-command",
    "plan",
    "docker",
)
_SAVABLE_CONFIG_PATHS = frozenset(
    {
        ("model", "reasoning", "effort"),
        ("runtime", "mode"),
        ("runtime", "allow_network"),
        ("permissions", "mode"),
        ("subagents", "enabled"),
        ("subagents", "parallel"),
        ("mcp", "enabled"),
        ("plugins", "enabled"),
    }
)
_SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "refresh_token",
        "authorization",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class SessionConfigUpdate:
    """One validated config value that may optionally be persisted."""

    path: tuple[str, ...]
    value: str | bool

    def __post_init__(self) -> None:
        if self.path not in _SAVABLE_CONFIG_PATHS:
            raise ValueError(
                f"Unsupported project config setting: {'.'.join(self.path)}."
            )
        if self.path == ("model", "reasoning", "effort"):
            if self.value not in ALLOWED_REASONING_EFFORTS:
                raise ValueError(
                    "Invalid model.reasoning.effort config value."
                )
        elif self.path == ("runtime", "mode"):
            if self.value not in ALLOWED_RUNTIME_MODES:
                raise ValueError("Invalid runtime.mode config value.")
        elif self.path == ("permissions", "mode"):
            if self.value not in ALLOWED_PERMISSION_MODES:
                raise ValueError("Invalid permissions.mode config value.")
        elif not isinstance(self.value, bool):
            raise ValueError(
                f"{'.'.join(self.path)} must be true or false."
            )


@dataclass(frozen=True, slots=True)
class ProjectConfigSaveResult:
    """Result of an approved, project-confined config write."""

    path: Path
    checkpoint_path: Path | None
    created: bool


@dataclass(slots=True)
class ChatSessionState:
    """Effective settings for future turns in one running chat app."""

    project_root: Path
    config: AppConfig
    offer_commit: bool = False
    commit_message: str | None = None
    show_usage: bool = False

    @classmethod
    def create(
        cls,
        project_root: str | Path,
        config: AppConfig,
    ) -> ChatSessionState:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {root}"
            )
        return cls(project_root=root, config=config)

    def set_project_root(self, project_root: str | Path) -> Path:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {root}"
            )
        self.project_root = root
        return root

    def set_reasoning_effort(self, effort: str) -> SessionConfigUpdate:
        normalized = effort.strip().lower()
        if normalized not in ALLOWED_REASONING_EFFORTS:
            allowed = ", ".join(ALLOWED_REASONING_EFFORTS)
            raise ValueError(
                f"Reasoning effort must be one of: {allowed}."
            )
        model = replace(
            self.config.model,
            reasoning=ReasoningConfig(effort=normalized),
        )
        self.config = replace(self.config, model=model)
        return SessionConfigUpdate(
            ("model", "reasoning", "effort"),
            normalized,
        )

    def set_runtime_mode(self, mode: str) -> SessionConfigUpdate:
        normalized = mode.strip().lower()
        if normalized not in ALLOWED_RUNTIME_MODES:
            allowed = ", ".join(ALLOWED_RUNTIME_MODES)
            raise ValueError(f"Runtime mode must be one of: {allowed}.")
        runtime = replace(self.config.runtime, mode=normalized)
        self.config = replace(self.config, runtime=runtime)
        return SessionConfigUpdate(("runtime", "mode"), normalized)

    def set_permission_mode(self, mode: str) -> SessionConfigUpdate:
        normalized = mode.strip().lower()
        if normalized not in ALLOWED_PERMISSION_MODES:
            allowed = ", ".join(ALLOWED_PERMISSION_MODES)
            raise ValueError(f"Permission mode must be one of: {allowed}.")
        self.config = replace(
            self.config,
            permissions=PermissionConfig(mode=normalized),
        )
        return SessionConfigUpdate(("permissions", "mode"), normalized)

    def set_allow_network(self, enabled: bool) -> SessionConfigUpdate:
        runtime = replace(self.config.runtime, allow_network=enabled)
        self.config = replace(self.config, runtime=runtime)
        return SessionConfigUpdate(("runtime", "allow_network"), enabled)

    def set_subagents_enabled(self, enabled: bool) -> SessionConfigUpdate:
        subagents = SubagentConfig(
            enabled=enabled,
            parallel=(self.config.subagents.parallel if enabled else False),
        )
        self.config = replace(self.config, subagents=subagents)
        return SessionConfigUpdate(("subagents", "enabled"), enabled)

    def set_parallel_subagents(self, enabled: bool) -> SessionConfigUpdate:
        subagents = SubagentConfig(
            enabled=(self.config.subagents.enabled or enabled),
            parallel=enabled,
        )
        self.config = replace(self.config, subagents=subagents)
        return SessionConfigUpdate(("subagents", "parallel"), enabled)

    def set_mcp_enabled(self, enabled: bool) -> SessionConfigUpdate:
        self.config = replace(
            self.config,
            mcp=MCPRuntimeConfig(enabled=enabled),
        )
        return SessionConfigUpdate(("mcp", "enabled"), enabled)

    def set_plugins_enabled(self, enabled: bool) -> SessionConfigUpdate:
        self.config = replace(
            self.config,
            plugins=PluginRuntimeConfig(enabled=enabled),
        )
        return SessionConfigUpdate(("plugins", "enabled"), enabled)

    def apply_config_update(
        self,
        update: SessionConfigUpdate,
    ) -> None:
        """Apply one already-validated update to the active session."""

        handlers = {
            ("model", "reasoning", "effort"): self.set_reasoning_effort,
            ("runtime", "mode"): self.set_runtime_mode,
            ("runtime", "allow_network"): self.set_allow_network,
            ("permissions", "mode"): self.set_permission_mode,
            ("subagents", "enabled"): self.set_subagents_enabled,
            ("subagents", "parallel"): self.set_parallel_subagents,
            ("mcp", "enabled"): self.set_mcp_enabled,
            ("plugins", "enabled"): self.set_plugins_enabled,
        }
        handler = handlers.get(update.path)
        if handler is None:
            raise ValueError(
                f"Unsupported session config setting: {'.'.join(update.path)}."
            )
        handler(update.value)


def persist_project_config_update(
    project_root: str | Path,
    update: SessionConfigUpdate,
    *,
    config: AppConfig,
    approval_provider: ApprovalProvider,
    approval_event_callback: ApprovalEventCallback | None = None,
) -> ProjectConfigSaveResult:
    """Validate, approve, checkpoint, and persist one project setting."""

    root = Path(project_root).expanduser().resolve()
    prepared = _prepare_project_config_update(root, update)
    permission_manager = PermissionManager(
        mode=config.permissions.mode,
        approval_provider=approval_provider,
        approval_event_callback=approval_event_callback,
        runtime_mode=config.runtime.mode,
        project_trust=config.runtime.project_trust,
    )
    decision = permission_manager.authorize(
        PermissionLevel.WRITE,
        "write_file",
        {
            "path": ".agent/config.yaml",
            "overwrite": prepared.target.exists(),
            "setting": ".".join(update.path),
            "value": update.value,
        },
    )
    if not decision.allowed:
        raise PermissionError(
            decision.reason or "Project config write was not approved."
        )

    # Approval can remain open while other processes touch the project.
    # Re-read and revalidate before checkpointing the exact file we replace.
    prepared = _prepare_project_config_update(root, update)
    existed = prepared.target.exists()
    checkpoint_path = (
        create_file_checkpoint(root, prepared.target)
        if existed
        else None
    )
    _write_prepared_config(root, prepared)
    return ProjectConfigSaveResult(
        path=prepared.target,
        checkpoint_path=checkpoint_path,
        created=not existed,
    )


def persist_user_config_update(
    update: SessionConfigUpdate,
    *,
    config: AppConfig,
    approval_provider: ApprovalProvider,
    approval_event_callback: ApprovalEventCallback | None = None,
    user_home: str | Path | None = None,
) -> ProjectConfigSaveResult:
    """Approve and atomically persist one setting to the user config."""

    home = Path(user_home or Path.home()).expanduser().resolve()
    prepared = _prepare_user_config_update(home, update)
    configured_mode = config.permissions.mode.strip().casefold()
    permission_manager = PermissionManager(
        # User-config writes are always interactive, including yes mode.
        mode="plan" if configured_mode == "plan" else "default",
        approval_provider=approval_provider,
        approval_event_callback=approval_event_callback,
        runtime_mode=config.runtime.mode,
        project_trust=config.runtime.project_trust,
    )
    decision = permission_manager.authorize(
        PermissionLevel.WRITE,
        "write_file",
        {
            "path": "~/.lunar-forge/config.yaml",
            "overwrite": prepared.target.exists(),
            "setting": ".".join(update.path),
            "value": update.value,
            "config_scope": "global",
        },
    )
    if not decision.allowed:
        raise PermissionError(
            decision.reason or "User config write was not approved."
        )

    # Approval can remain open while another process updates user config.
    prepared = _prepare_user_config_update(home, update)
    existed = prepared.target.exists()
    _write_prepared_user_config(home, prepared)
    return ProjectConfigSaveResult(
        path=prepared.target,
        checkpoint_path=None,
        created=not existed,
    )


@dataclass(frozen=True, slots=True)
class _PreparedConfigUpdate:
    target: Path
    serialized: str


def _prepare_project_config_update(
    root: Path,
    update: SessionConfigUpdate,
) -> _PreparedConfigUpdate:
    target = safe_path(root, ".agent/config.yaml")
    return _prepare_config_update(target, update, label="Project")


def _prepare_user_config_update(
    home: Path,
    update: SessionConfigUpdate,
) -> _PreparedConfigUpdate:
    target = home / ".lunar-forge" / "config.yaml"
    _validate_user_config_target(home, target)
    return _prepare_config_update(target, update, label="User")


def _prepare_config_update(
    target: Path,
    update: SessionConfigUpdate,
    *,
    label: str,
) -> _PreparedConfigUpdate:
    data = _read_config(target, label=label)
    if _contains_sensitive_config_key(data):
        raise ValueError(
            f"{label} config contains a raw secret field. Use environment "
            "variable names instead; the setting was not saved."
        )
    destination: dict[str, Any] = data
    for key in update.path[:-1]:
        current = destination.get(key)
        if current is None:
            nested: dict[str, Any] = {}
            destination[key] = nested
            destination = nested
        elif isinstance(current, dict):
            destination = current
        else:
            raise ValueError(
                f"Cannot save setting because an existing {label.lower()} config "
                f"section is not a mapping: {key}."
            )
    destination[update.path[-1]] = update.value

    serialized = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
    )
    if len(serialized) > MAX_CONFIG_CHARACTERS:
        raise ValueError(
            f"Updated {label.lower()} config exceeds the configured size limit."
        )
    return _PreparedConfigUpdate(target=target, serialized=serialized)


def _write_prepared_config(
    root: Path,
    prepared: _PreparedConfigUpdate,
) -> None:
    target = safe_path(root, prepared.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe_path(root, ".agent/config.yaml.tmp")
    temporary.write_text(
        prepared.serialized,
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(target)


def _write_prepared_user_config(
    home: Path,
    prepared: _PreparedConfigUpdate,
) -> None:
    target = prepared.target
    _validate_user_config_target(home, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _validate_user_config_target(home, target)
    temporary = target.with_name(
        f"{target.name}.{uuid4().hex}.tmp"
    )
    _validate_user_config_target(home, temporary, allow_temporary=True)
    try:
        temporary.write_text(
            prepared.serialized,
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(target)
    finally:
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def _validate_user_config_target(
    home: Path,
    target: Path,
    *,
    allow_temporary: bool = False,
) -> None:
    expected_directory = home / ".lunar-forge"
    expected_target = expected_directory / "config.yaml"
    if allow_temporary:
        valid_name = (
            target.parent == expected_directory
            and target.name.startswith("config.yaml.")
            and target.name.endswith(".tmp")
        )
    else:
        valid_name = target == expected_target
    if not valid_name:
        raise PermissionError("User config path is outside the allowed location.")
    if target.exists() and target.is_symlink():
        raise PermissionError("User config path must not be a symbolic link.")
    if expected_directory.exists():
        try:
            expected_directory.resolve().relative_to(home)
        except ValueError as exc:
            raise PermissionError(
                "User config directory resolves outside the user home."
            ) from exc


def _read_config(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if len(raw) > MAX_CONFIG_CHARACTERS:
        raise ValueError(
            f"{label} config exceeds the configured size limit."
        )
    loaded = yaml.safe_load(raw) or {}
    if not isinstance(loaded, Mapping):
        raise ValueError(f"{label} config must contain a YAML object.")
    return _copy_mapping(loaded, label=label)


def _copy_mapping(
    value: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{label} config keys must be strings.")
        copied[key] = (
            _copy_mapping(item, label=label)
            if isinstance(item, Mapping)
            else item
        )
    return copied


def _contains_sensitive_config_key(value: Mapping[str, Any]) -> bool:
    for key, item in value.items():
        normalized = key.strip().lower().replace("-", "_")
        if normalized in _SENSITIVE_CONFIG_KEYS:
            return True
        if isinstance(item, Mapping) and _contains_sensitive_config_key(item):
            return True
    return False


__all__ = [
    "ALLOWED_PERMISSION_MODES",
    "ALLOWED_RUNTIME_MODES",
    "ChatSessionState",
    "ProjectConfigSaveResult",
    "SessionConfigUpdate",
    "persist_project_config_update",
    "persist_user_config_update",
]
