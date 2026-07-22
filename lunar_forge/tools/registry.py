"""Central registry for model-callable tools."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lunar_forge.permissions import (
    ApprovalCallback,
    PermissionLevel,
    PermissionManager,
)
from lunar_forge.tools.files import (
    create_dir,
    edit_file,
    insert_lines,
    list_dir,
    read_file,
    read_file_with_line_numbers,
    replace_lines,
    write_file,
)
from lunar_forge.tools.search import glob_files, grep
from lunar_forge.tools.shell import run_command


if TYPE_CHECKING:
    from lunar_forge.mcp.client import MCPClient
    from lunar_forge.plugins.loader import LoadedPlugin
    from lunar_forge.plugins.registry import EntrypointResolver


ToolHandler = Callable[..., dict[str, Any]]
MAX_REGISTRY_RESULT_CHARACTERS = 200_000
MAX_REGISTRY_RESULT_PREVIEW_CHARACTERS = 20_000
MAX_REGISTERED_TOOLS = 256
PROVIDER_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
REDACTED_TOOL_VALUE = "[REDACTED]"
_SENSITIVE_RESULT_KEYS = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "credentials",
        "authorization",
        "cookie",
        "privatekey",
    }
)


@dataclass(frozen=True)
class Tool:
    """A named handler and its model-facing JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler = field(repr=False, compare=False)
    permission: PermissionLevel = PermissionLevel.READ
    plan_safe: bool = False


class ToolRegistry:
    def __init__(
        self,
        tools: Iterable[Tool] = (),
        permission_manager: PermissionManager | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._model_names_by_internal: dict[str, str] = {}
        self._internal_names_by_model: dict[str, str] = {}
        self._permission_manager = permission_manager or PermissionManager()
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool is already registered: {tool.name}")
        model_name = provider_safe_tool_name(tool.name)
        colliding_name = self._internal_names_by_model.get(model_name)
        if colliding_name is not None:
            raise ValueError(
                "Provider-safe tool name collision: "
                f"{colliding_name!r} and {tool.name!r} both map to "
                f"{model_name!r}."
            )
        if len(self._tools) >= MAX_REGISTERED_TOOLS:
            raise ValueError(
                f"Tool registry supports at most {MAX_REGISTERED_TOOLS} tools."
            )
        self._tools[tool.name] = tool
        self._model_names_by_internal[tool.name] = model_name
        self._internal_names_by_model[model_name] = tool.name

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> tuple[str, ...]:
        """Return stable internal names for diagnostics and permission policy."""
        return tuple(sorted(self._tools))

    def model_name_for(self, internal_name: str) -> str:
        """Return the provider-safe alias for a registered internal tool name."""
        return self._model_names_by_internal[internal_name]

    def internal_name_for(self, name: str) -> str | None:
        """Resolve an internal name or provider-safe alias to internal identity."""
        if name in self._tools:
            return name
        return self._internal_names_by_model.get(name)

    def set_permission_manager(self, permission_manager: PermissionManager) -> None:
        """Apply a mode-specific permission policy to future executions."""
        self._permission_manager = permission_manager

    def schemas(
        self,
        *,
        read_only: bool = False,
        allow_execute: bool = True,
    ) -> list[dict[str, Any]]:
        """Return LiteLLM/OpenAI-compatible function tool schemas."""
        return [
            {
                "type": "function",
                "function": {
                    "name": self._model_names_by_internal[tool.name],
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
            if not read_only
            or tool.permission is PermissionLevel.READ
            or tool.plan_safe
            if allow_execute
            or tool.permission is not PermissionLevel.EXECUTE
        ]

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool and always return a JSON-serializable result."""
        internal_name = self.internal_name_for(name)
        if internal_name is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        tool = self._tools[internal_name]
        if not isinstance(arguments, Mapping):
            return {"ok": False, "error": "Tool arguments must be an object."}

        decision = self._permission_manager.authorize(
            tool.permission,
            tool.name,
            arguments,
            plan_safe=tool.plan_safe,
        )
        if not decision.allowed:
            return {
                "ok": False,
                "error": decision.reason or "Permission denied.",
                "permission_denied": True,
            }

        try:
            result = tool.handler(**dict(arguments))
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Tool {tool.name} failed with {type(exc).__name__}.",
            }

        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return {
                "ok": False,
                "error": f"Tool {tool.name} returned an invalid result.",
            }
        try:
            safe_result = _redact_sensitive_result_values(result)
            serialized = json.dumps(
                safe_result,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, RecursionError):
            return {
                "ok": False,
                "error": (
                    f"Tool {tool.name} returned a non-serializable result."
                ),
            }
        if len(serialized) > MAX_REGISTRY_RESULT_CHARACTERS:
            bounded_result: dict[str, Any] = {
                "ok": safe_result["ok"],
                "truncated": True,
                "preview": serialized[:MAX_REGISTRY_RESULT_PREVIEW_CHARACTERS],
            }
            if safe_result["ok"] is False:
                bounded_result["error"] = "Tool error result exceeded the size limit."
            return bounded_result
        return safe_result


def provider_safe_tool_name(internal_name: str) -> str:
    """Normalize one internal identity for provider function-name constraints."""
    if not isinstance(internal_name, str) or not internal_name.strip():
        raise ValueError("Tool name must be a non-empty string.")
    model_name = re.sub(r"[^a-zA-Z0-9_-]", "_", internal_name)
    if not PROVIDER_TOOL_NAME_PATTERN.fullmatch(model_name):
        raise ValueError(
            f"Tool name cannot be converted to a provider-safe name: {internal_name!r}"
        )
    return model_name


def _redact_sensitive_result_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Tool result keys must be strings.")
            normalized_key = "".join(
                character for character in key.casefold() if character.isalnum()
            )
            redacted[key] = (
                REDACTED_TOOL_VALUE
                if normalized_key in _SENSITIVE_RESULT_KEYS
                else _redact_sensitive_result_values(item)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive_result_values(item) for item in value]
    return value


def create_read_only_registry(project_root: str | Path) -> ToolRegistry:
    """Create a registry containing only the current read-only tools."""
    return ToolRegistry(
        (
            Tool(
                name="list_dir",
                description="List files and directories inside the project.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Project-relative directory path.",
                            "default": ".",
                        }
                    },
                    "additionalProperties": False,
                },
                handler=partial(list_dir, project_root),
            ),
            Tool(
                name="read_file",
                description="Read a bounded line range from a UTF-8 project file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Project-relative file path.",
                        },
                        "start_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "First one-based line to return.",
                        },
                        "end_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Last one-based line to return.",
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=partial(read_file, project_root),
            ),
            Tool(
                name="read_file_with_line_numbers",
                description=(
                    "Read a bounded UTF-8 file range with stable one-based line "
                    "numbers for precise line edits."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Project-relative file path.",
                        },
                        "start_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "First one-based line to return.",
                        },
                        "end_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Last one-based line to return.",
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=partial(read_file_with_line_numbers, project_root),
            ),
            Tool(
                name="grep",
                description="Search project files with a regular expression.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Regular expression to search for.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Project-relative file or directory.",
                            "default": ".",
                        },
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
                handler=partial(grep, project_root),
            ),
            Tool(
                name="glob",
                description="Find project files matching a glob pattern.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Glob pattern such as **/*.py.",
                        }
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
                handler=partial(glob_files, project_root),
            ),
        )
    )


def create_tool_registry(
    project_root: str | Path,
    mode: str = "default",
    approval_callback: ApprovalCallback | None = None,
    *,
    runtime_mode: str = "local",
    allow_network: bool = False,
    mcp_client: MCPClient | None = None,
    plugins: Sequence[LoadedPlugin] = (),
    plugin_resolver: EntrypointResolver | None = None,
) -> ToolRegistry:
    """Create built-ins and explicitly enabled external extension tools."""
    normalized_mode = mode.strip().lower()
    read_registry = create_read_only_registry(project_root)
    tools = [read_registry.get(name) for name in read_registry.names()]
    if normalized_mode != "plan":
        tools.extend(_write_tools(project_root))
    if (
        normalized_mode not in {"plan", "no-command"}
        and runtime_mode.strip().lower() != "no-command"
    ):
        tools.extend(
            _execution_tools(
                project_root,
                runtime_mode=runtime_mode,
                allow_network=allow_network,
            )
        )
    registry = ToolRegistry(
        tools,
        permission_manager=PermissionManager(
            mode=mode,
            approval_callback=approval_callback,
        ),
    )
    if mcp_client is not None:
        # Local import avoids making the central registry depend on an optional
        # MCP transport during normal built-in-only startup.
        from lunar_forge.mcp.registry import register_mcp_tools

        register_mcp_tools(
            registry,
            mcp_client,
            read_only_only=normalized_mode == "plan",
        )
    if plugins and normalized_mode != "plan":
        if plugin_resolver is None:
            raise ValueError("Enabled plugins require a trusted entrypoint resolver.")
        from lunar_forge.plugins.registry import register_plugin_tools

        register_plugin_tools(
            registry,
            tuple(plugins),
            plugin_resolver,
        )
    return registry


def _write_tools(project_root: str | Path) -> tuple[Tool, ...]:
    return (
        Tool(
            name="create_dir",
            description="Create a directory inside the project after approval.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative directory path.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=partial(create_dir, project_root),
            permission=PermissionLevel.WRITE,
        ),
        Tool(
            name="write_file",
            description=(
                "Create a UTF-8 file, or overwrite it only when explicitly requested "
                "and approved."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete file content to write.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Allow replacing an existing file.",
                        "default": False,
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=partial(write_file, project_root),
            permission=PermissionLevel.WRITE,
        ),
        Tool(
            name="edit_file",
            description=(
                "Replace an exact text block only when it occurs exactly once, after "
                "approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text expected once in the file.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            handler=partial(edit_file, project_root),
            permission=PermissionLevel.WRITE,
        ),
        Tool(
            name="replace_lines",
            description=(
                "Replace a precise one-based inclusive line range after first "
                "reading the file with line numbers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "First one-based line to replace.",
                    },
                    "end_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Last one-based line to replace.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text for the selected lines.",
                    },
                },
                "required": ["path", "start_line", "end_line", "new_text"],
                "additionalProperties": False,
            },
            handler=partial(replace_lines, project_root),
            permission=PermissionLevel.WRITE,
        ),
        Tool(
            name="insert_lines",
            description=(
                "Insert text after a one-based line; use after_line=0 for the "
                "top of the file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Project-relative file path.",
                    },
                    "after_line": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "Insert after this one-based line, or zero at file top."
                        ),
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Text to insert as one or more lines.",
                    },
                },
                "required": ["path", "after_line", "new_text"],
                "additionalProperties": False,
            },
            handler=partial(insert_lines, project_root),
            permission=PermissionLevel.WRITE,
        ),
    )


def _execution_tools(
    project_root: str | Path,
    *,
    runtime_mode: str,
    allow_network: bool,
) -> tuple[Tool, ...]:
    return (
        Tool(
            name="run_command",
            description=(
                "Run one local command in the project after approval. Shell "
                "operators and dangerous commands are not supported."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Executable and arguments to run.",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Timeout in milliseconds.",
                        "default": 120000,
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=partial(
                run_command,
                project_root,
                runtime_mode=runtime_mode,
                allow_network=allow_network,
            ),
            permission=PermissionLevel.EXECUTE,
        ),
        Tool(
            name="run_validation",
            description=(
                "Detect and run likely Python and Node validation commands in "
                "the project after command approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "timeout_ms": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Per-command timeout in milliseconds.",
                        "default": 120000,
                    }
                },
                "additionalProperties": False,
            },
            handler=partial(
                _run_validation,
                project_root,
                runtime_mode=runtime_mode,
                allow_network=allow_network,
            ),
            permission=PermissionLevel.EXECUTE,
        ),
        Tool(
            name="run_browser_validation",
            description=(
                "Preferred tool for browser/UI validation, rendered-page checks, "
                "console and request errors, and screenshots of an already-running "
                "local site. Use this instead of curl or run_validation for UI "
                "evidence. Requires approval and optional Playwright support; it "
                "never starts a development server."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "Local loopback HTTP(S) URL, such as "
                            "http://127.0.0.1:8000."
                        ),
                    },
                    "screenshot": {
                        "type": "boolean",
                        "description": "Capture a bounded viewport screenshot.",
                        "default": True,
                    },
                    "checks": {
                        "type": "array",
                        "description": (
                            "Optional CSS selectors that must each match at least "
                            "one element."
                        ),
                        "items": {"type": "string", "maxLength": 500},
                        "maxItems": 20,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=partial(_run_browser_validation, project_root),
            permission=PermissionLevel.EXECUTE,
        ),
    )


def _run_validation(
    project_root: str | Path,
    timeout_ms: int = 120_000,
    *,
    runtime_mode: str = "local",
    allow_network: bool = False,
) -> dict[str, Any]:
    """Import the workflow lazily to keep tool package imports acyclic."""
    from lunar_forge.workflows.validation import run_validation

    return run_validation(
        project_root,
        timeout_ms,
        runtime_mode=runtime_mode,
        allow_network=allow_network,
    )


def _run_browser_validation(
    project_root: str | Path,
    url: str,
    screenshot: bool = True,
    checks: list[str] | None = None,
) -> dict[str, Any]:
    """Import the optional browser workflow only after tool approval."""
    from lunar_forge.workflows.browser_validation import run_browser_validation

    return run_browser_validation(
        url,
        screenshot=screenshot,
        checks=checks,
        project_root=project_root,
    )
