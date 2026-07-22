"""Role metadata and deny-by-default tool access for subagents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lunar_forge.permissions import PermissionManager
from lunar_forge.tools.registry import ToolRegistry


BUILTIN_SUBAGENT_TOOLS = frozenset(
    {
        "list_dir",
        "read_file",
        "grep",
        "glob",
        "create_dir",
        "write_file",
        "edit_file",
        "run_command",
        "detect_project",
        "run_validation",
    }
)
SUBAGENT_WRITE_TOOLS = frozenset(
    {
        "create_dir",
        "write_file",
        "edit_file",
        "replace_lines",
        "insert_lines",
    }
)


@dataclass(frozen=True)
class SubagentRole:
    """Static definition of one role and the tools it may use."""

    name: str
    purpose: str
    system_prompt_fragment: str
    allowed_tools: frozenset[str]
    blocked_tools: frozenset[str]

    def __post_init__(self) -> None:
        normalized_name = self.name.strip().lower()
        allowed_tools = frozenset(self.allowed_tools)
        blocked_tools = frozenset(self.blocked_tools)
        if not normalized_name:
            raise ValueError("Subagent role name must not be empty.")
        if not self.purpose.strip():
            raise ValueError("Subagent role purpose must not be empty.")
        if not self.system_prompt_fragment.strip():
            raise ValueError("Subagent system prompt fragment must not be empty.")
        if any(not tool.strip() for tool in allowed_tools | blocked_tools):
            raise ValueError("Subagent tool names must not be empty.")
        overlap = allowed_tools & blocked_tools
        if overlap:
            raise ValueError(
                "Subagent tools cannot be both allowed and blocked: "
                f"{sorted(overlap)}"
            )
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "allowed_tools", allowed_tools)
        object.__setattr__(self, "blocked_tools", blocked_tools)

    def allows(self, tool_name: str) -> bool:
        """Return whether the role may see and request ``tool_name``."""
        return tool_name in self.allowed_tools and tool_name not in self.blocked_tools

    @property
    def is_writer(self) -> bool:
        """Return whether the role can mutate project files."""
        return bool(self.allowed_tools & SUBAGENT_WRITE_TOOLS)

    @property
    def can_run_in_parallel(self) -> bool:
        """Only roles without file mutation tools are parallel-safe."""
        return not self.is_writer

    def restrict(self, registry: ToolRegistry) -> RestrictedToolRegistry:
        """Create a role-scoped view without replacing registry permissions."""
        return RestrictedToolRegistry(registry, self)


class RestrictedToolRegistry:
    """Expose only a role's allowlisted tools from an existing registry.

    Calls that pass the role check are delegated to the original ``ToolRegistry``
    so its permission manager, path protection, and command safety remain active.
    """

    def __init__(self, registry: ToolRegistry, role: SubagentRole) -> None:
        self._registry = registry
        self.role = role

    def names(self) -> tuple[str, ...]:
        available = set(self._registry.names())
        return tuple(sorted(available & self.role.allowed_tools))

    def schemas(
        self,
        *,
        read_only: bool = False,
        allow_execute: bool = True,
    ) -> list[dict[str, Any]]:
        allowed_names = set(self.names())
        return [
            schema
            for schema in self._registry.schemas(
                read_only=read_only,
                allow_execute=allow_execute,
            )
            if self._registry.internal_name_for(_schema_tool_name(schema) or "")
            in allowed_names
        ]

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        internal_name = self.internal_name_for(name)
        if internal_name is None or not self.role.allows(internal_name):
            return {
                "ok": False,
                "error": (
                    f"Subagent role {self.role.name!r} is not allowed to use "
                    f"tool {name!r}."
                ),
                "permission_denied": True,
                "blocked_by_subagent": True,
            }
        return self._registry.execute(name, arguments)

    def model_name_for(self, internal_name: str) -> str:
        """Return the provider-safe alias from the underlying registry."""
        return self._registry.model_name_for(internal_name)

    def internal_name_for(self, name: str) -> str | None:
        """Resolve a model-facing alias without weakening the role allowlist."""
        return self._registry.internal_name_for(name)

    def set_permission_manager(self, permission_manager: PermissionManager) -> None:
        """Delegate policy updates without creating a second permission path."""
        self._registry.set_permission_manager(permission_manager)


def _schema_tool_name(schema: Mapping[str, Any]) -> str | None:
    function = schema.get("function")
    if not isinstance(function, Mapping):
        return None
    name = function.get("name")
    return name if isinstance(name, str) else None


__all__ = [
    "BUILTIN_SUBAGENT_TOOLS",
    "RestrictedToolRegistry",
    "SUBAGENT_WRITE_TOOLS",
    "SubagentRole",
]
