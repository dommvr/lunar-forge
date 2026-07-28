"""Central registry for model-callable tools."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lunar_forge.approvals import ApprovalProvider
from lunar_forge.permissions import (
    ApprovalCallback,
    ApprovalEventCallback,
    PermissionLevel,
    PermissionManager,
)
from lunar_forge.tools.ci import ci_summary
from lunar_forge.tools.dependencies import dependency_summary
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
from lunar_forge.tools.git import (
    git_diff,
    git_status,
    list_changed_files,
)
from lunar_forge.tools.project_health import project_health
from lunar_forge.tools.search import glob_files, grep
from lunar_forge.tools.shell import run_command
from lunar_forge.tools.symbols import list_symbols
from lunar_forge.tools.structured_readers import (
    DEFAULT_MANY_BYTES_PER_FILE,
    DEFAULT_MANY_TOTAL_BYTES,
    DEFAULT_STRUCTURED_MAX_BYTES,
    MAX_MANY_BYTES_PER_FILE,
    MAX_MANY_FILES,
    MAX_MANY_TOTAL_BYTES,
    MAX_REQUEST_PATH_CHARACTERS,
    MAX_STRUCTURED_MAX_BYTES,
    read_json,
    read_many_files,
    read_yaml,
)


if TYPE_CHECKING:
    from lunar_forge.mcp.client import MCPClient
    from lunar_forge.plugins.loader import LoadedPlugin
    from lunar_forge.plugins.registry import EntrypointResolver


ToolHandler = Callable[..., dict[str, Any]]
MAX_REGISTRY_RESULT_CHARACTERS = 200_000
MAX_REGISTRY_RESULT_PREVIEW_CHARACTERS = 20_000
MAX_REGISTERED_TOOLS = 256
MAX_SESSION_CHANGED_FILES = 500
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
_SESSION_FILE_MUTATION_TOOLS = frozenset(
    {"write_file", "edit_file", "replace_lines", "insert_lines"}
)
READ_NAVIGATION_TOOLS = frozenset(
    {
        "list_dir",
        "read_file",
        "read_file_with_line_numbers",
        "read_json",
        "read_yaml",
        "read_many_files",
        "list_symbols",
        "grep",
        "glob",
    }
)
PROJECT_INSPECTION_TOOLS = frozenset(
    {
        "detect_project",
        "project_health",
        "ci_summary",
        "dependency_summary",
    }
)
GIT_INSPECTION_TOOLS = frozenset(
    {"git_status", "git_diff", "list_changed_files"}
)
WRITE_TOOL_NAMES = frozenset(
    {"create_dir", "write_file", "edit_file", "replace_lines", "insert_lines"}
)
COMMAND_TOOL_NAMES = frozenset({"run_command", "run_validation"})
BROWSER_TOOL_NAMES = frozenset(
    {"run_browser_validation", "run_managed_browser_validation"}
)
COMMIT_EXECUTION_TOOL_NAMES = frozenset({"git_commit"})
READ_ONLY_PROFILE_TOOLS = (
    READ_NAVIGATION_TOOLS | PROJECT_INSPECTION_TOOLS | GIT_INSPECTION_TOOLS
)
BUILTIN_TASK_PROFILE_TOOLS = (
    READ_ONLY_PROFILE_TOOLS
    | WRITE_TOOL_NAMES
    | COMMAND_TOOL_NAMES
    | BROWSER_TOOL_NAMES
    | COMMIT_EXECUTION_TOOL_NAMES
)
_EXPLICIT_READONLY_SUPPORT = {
    "grep": frozenset({"read_file"}),
    "glob": frozenset({"read_file"}),
    "list_symbols": frozenset({"read_file_with_line_numbers"}),
    "git_diff": frozenset({"git_status"}),
    "list_changed_files": frozenset({"git_status"}),
}
_MUTATION_INTENT_PATTERN = re.compile(
    r"(?i)\b(?:add|build|change|create|delete|edit|fix|implement|insert|"
    r"refactor|remove|replace|scaffold|update|write)\b"
)
_NO_MUTATION_PATTERN = re.compile(
    r"(?i)\b(?:do not|don't|dont|never)\s+"
    r"(?:change|edit|modify|write)\b|"
    r"\bwithout\s+(?:changing|editing|modifying|writing)\b|"
    r"\bread[- ]only\b|"
    r"\bonly\s+inspect\b"
)
_STRICT_INSPECTION_PATTERN = re.compile(
    r"(?i)\bread[- ]only\b|"
    r"\bonly\s+inspect\b"
)
_NO_COMMAND_PATTERN = re.compile(
    r"(?i)\b(?:do not|don't|dont|never)\s+"
    r"(?:run|execute)\s+(?:shell\s+)?commands?\b|"
    r"\bwithout\s+(?:running|executing)\s+(?:shell\s+)?commands?\b|"
    r"\bno[- ]command\b"
)
_REVIEW_INTENT_PATTERN = re.compile(
    r"(?i)\b(?:audit|explain|inspect|review|summarize)\b|"
    r"\bcommit\s+readiness\b|"
    r"\b(?:git\s+)?diff\b"
)
_VALIDATION_EXECUTION_INTENT_PATTERN = re.compile(
    r"(?i)\b(?:run|execute|perform)\s+(?:the\s+)?"
    r"(?:build|checks?|lint|tests?|validation)\b|"
    r"\bvalidate\s+(?:it|the|this|project|repository|repo|changes?)\b"
)
_EXPLICIT_READONLY_FAST_PATH_TOOL_NAMES = frozenset(
    {
        "project_health",
        "dependency_summary",
        "git_status",
        "git_diff",
        "list_changed_files",
        "read_json",
        "read_yaml",
        "read_many_files",
        "list_symbols",
        "ci_summary",
    }
)
_EXPLICIT_READONLY_PATH_TOOLS = frozenset(
    {"read_json", "read_yaml", "list_symbols"}
)
_EXPLICIT_READONLY_START_PATTERN = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:run|use|call)\s+(?:the\s+)?"
    r"(?P<tool>"
    + "|".join(
        re.escape(name)
        for name in sorted(
            _EXPLICIT_READONLY_FAST_PATH_TOOL_NAMES,
            key=len,
            reverse=True,
        )
    )
    + r")\b"
)
_EXPLICIT_READONLY_PATH_PATTERN = re.compile(
    r'"(?P<double>[^"\r\n]+)"|'
    r"'(?P<single>[^'\r\n]+)'|"
    r"(?P<plain>"
    r"(?:(?:\.{1,2}|[A-Za-z0-9_@+.-]+)[\\/])*"
    r"[A-Za-z0-9_@+.-]+\.[A-Za-z0-9_-]+"
    r")"
)
_COMMIT_INTENT_PATTERN = re.compile(r"(?i)\bcommit(?:ted|ting|s)?\b")
_BROWSER_EXECUTION_INTENT_PATTERN = re.compile(
    r"(?i)\b(?:browser|playwright|screenshots?)\b|"
    r"https?://(?:localhost|127\.0\.0\.1|\[::1\])"
)
_EXPLICIT_NAMESPACED_TOOL_PATTERN = re.compile(
    r"(?i)\b(?:use|call|run)\s+(?:the\s+)?"
    r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\b"
)
_COMMAND_STARTING_TOOL_NAMES = frozenset(
    {"run_managed_browser_validation"}
)
MAX_EXPLICIT_READONLY_REQUEST_CHARACTERS = 4_000


class TaskProfile(str, Enum):
    """Bounded model-facing tool sets for one task or subagent call."""

    EXPLICIT_READONLY = "explicit_readonly"
    PLAN_ONLY = "plan_only"
    REVIEW_ONLY = "review_only"
    NO_EDIT_EXECUTION_ALLOWED = "no_edit_execution_allowed"
    EDIT_TASK = "edit_task"
    BROWSER_TASK = "browser_task"
    COMMIT_TASK = "commit_task"
    NEW_PROJECT = "new_project"


@dataclass(frozen=True)
class TaskProfileSelection:
    """Deterministic profile selection plus explicitly requested read tools."""

    profile: TaskProfile
    requested_tools: tuple[str, ...] = ()
    blocked_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExplicitReadOnlyToolRequest:
    """One unambiguous built-in read-only tool invocation."""

    tool_name: str
    arguments: Mapping[str, Any]


def parse_explicit_readonly_tool_request(
    request: str,
) -> ExplicitReadOnlyToolRequest | None:
    """Parse only conservative ``Run <tool> [on <path>]`` request forms."""
    if not isinstance(request, str):
        return None
    text = request.strip()
    if not text or len(text) > MAX_EXPLICIT_READONLY_REQUEST_CHARACTERS:
        return None
    match = _EXPLICIT_READONLY_START_PATTERN.match(text)
    if match is None:
        return None

    tool_name = match.group("tool").casefold()
    intent_text = _EXPLICIT_READONLY_PATH_PATTERN.sub("", text)
    mentioned_tools = _mentioned_names(
        text,
        _EXPLICIT_READONLY_FAST_PATH_TOOL_NAMES,
    )
    if mentioned_tools != (tool_name,):
        return None
    if (
        (
            _MUTATION_INTENT_PATTERN.search(text) is not None
            and _NO_MUTATION_PATTERN.search(text) is None
        )
        or _VALIDATION_EXECUTION_INTENT_PATTERN.search(text) is not None
        or _COMMIT_INTENT_PATTERN.search(text) is not None
        or _BROWSER_EXECUTION_INTENT_PATTERN.search(intent_text) is not None
    ):
        return None

    remainder = text[match.end() :]
    path_clause = re.search(
        r"(?i)(?:\bon\b|\bto\s+(?:inspect|read|summarize)\b)"
        r"(?P<paths>.*)",
        remainder,
    )
    path_text = path_clause.group("paths") if path_clause is not None else ""
    paths = _explicit_readonly_paths(path_text)

    if tool_name in _EXPLICIT_READONLY_PATH_TOOLS:
        if path_clause is None or len(paths) != 1:
            return None
        return ExplicitReadOnlyToolRequest(tool_name, {"path": paths[0]})

    if tool_name == "read_many_files":
        if path_clause is None or not paths:
            return None
        return ExplicitReadOnlyToolRequest(tool_name, {"paths": list(paths)})

    if tool_name == "git_diff":
        if len(paths) > 1:
            return None
        arguments: dict[str, Any] = {}
        if paths:
            arguments["path"] = paths[0]
        if re.search(r"(?i)\bstaged\b", remainder):
            arguments["staged"] = True
        return ExplicitReadOnlyToolRequest(tool_name, arguments)

    if path_clause is not None or paths:
        return None
    if tool_name == "list_changed_files":
        source_match = re.search(
            r"(?i)\bfrom\s+(?P<source>session|git|both)\b",
            remainder,
        )
        if source_match is not None:
            return ExplicitReadOnlyToolRequest(
                tool_name,
                {"source": source_match.group("source").casefold()},
            )
    return ExplicitReadOnlyToolRequest(tool_name, {})


def _explicit_readonly_paths(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _EXPLICIT_READONLY_PATH_PATTERN.finditer(text):
        path = next(
            (
                value
                for value in (
                    match.group("double"),
                    match.group("single"),
                    match.group("plain"),
                )
                if value is not None
            ),
            "",
        ).strip()
        if path and path not in paths:
            paths.append(path)
    return tuple(paths)


def select_task_profile(
    request: str,
    *,
    mode: str = "default",
    browser_intent: bool = False,
    commit_requested: bool = False,
    new_project: bool = False,
) -> TaskProfileSelection:
    """Choose one conservative task profile from explicit runtime context."""
    normalized_mode = str(mode).strip().lower()
    text = request if isinstance(request, str) else str(request)
    requested_read_tools = _mentioned_names(text, READ_ONLY_PROFILE_TOOLS)
    requested_command_tools = _mentioned_names(text, COMMAND_TOOL_NAMES)
    no_edit_requested = _NO_MUTATION_PATTERN.search(text) is not None
    commands_prohibited = (
        normalized_mode == "no-command"
        or _NO_COMMAND_PATTERN.search(text) is not None
    )

    if normalized_mode == "plan":
        return TaskProfileSelection(TaskProfile.PLAN_ONLY)
    if new_project:
        return TaskProfileSelection(TaskProfile.NEW_PROJECT)
    if no_edit_requested:
        requested_tools = set(requested_read_tools)
        requested_tools.update(requested_command_tools)
        strict_inspection = _STRICT_INSPECTION_PATTERN.search(text) is not None
        if (
            _VALIDATION_EXECUTION_INTENT_PATTERN.search(text) is not None
            and not requested_command_tools
            and not strict_inspection
        ):
            requested_tools.add("run_validation")
        blocked_tools: set[str] = set()
        if commands_prohibited:
            blocked_tools.update(COMMAND_TOOL_NAMES)
            blocked_tools.update(_COMMAND_STARTING_TOOL_NAMES)
            requested_tools.difference_update(COMMAND_TOOL_NAMES)
        has_non_read_execution = bool(
            requested_command_tools
            or browser_intent
            or _EXPLICIT_NAMESPACED_TOOL_PATTERN.search(text) is not None
            or (
                not strict_inspection
                and _VALIDATION_EXECUTION_INTENT_PATTERN.search(text) is not None
            )
        )
        if requested_read_tools and not has_non_read_execution:
            return TaskProfileSelection(
                TaskProfile.EXPLICIT_READONLY,
                requested_read_tools,
                tuple(sorted(blocked_tools)),
            )
        if not has_non_read_execution:
            return TaskProfileSelection(
                TaskProfile.REVIEW_ONLY,
                blocked_tools=tuple(sorted(blocked_tools)),
            )
        return TaskProfileSelection(
            TaskProfile.NO_EDIT_EXECUTION_ALLOWED,
            tuple(sorted(requested_tools)),
            tuple(sorted(blocked_tools)),
        )
    if commit_requested:
        return TaskProfileSelection(TaskProfile.COMMIT_TASK)

    mutation_intent = (
        _MUTATION_INTENT_PATTERN.search(text) is not None
        and _NO_MUTATION_PATTERN.search(text) is None
    )
    validation_execution_intent = (
        _VALIDATION_EXECUTION_INTENT_PATTERN.search(text) is not None
    )
    if browser_intent:
        return TaskProfileSelection(TaskProfile.BROWSER_TASK)
    if validation_execution_intent:
        return TaskProfileSelection(TaskProfile.EDIT_TASK)
    if (
        requested_read_tools
        and not mutation_intent
    ):
        return TaskProfileSelection(
            TaskProfile.EXPLICIT_READONLY,
            requested_read_tools,
        )
    if _REVIEW_INTENT_PATTERN.search(text) is not None and not mutation_intent:
        return TaskProfileSelection(TaskProfile.REVIEW_ONLY)
    return TaskProfileSelection(TaskProfile.EDIT_TASK)


def tool_names_for_profile(
    profile: TaskProfile | str,
    *,
    requested_tools: Iterable[str] = (),
    available_tools: Iterable[str] | None = None,
    browser_intent: bool = False,
    commit_requested: bool = False,
    blocked_tools: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return stable internal names allowed by one profile before role filtering."""
    resolved = _normalize_task_profile(profile)
    requested = {
        name.strip()
        for name in requested_tools
        if isinstance(name, str) and name.strip()
    }
    blocked = {
        name.strip()
        for name in blocked_tools
        if isinstance(name, str) and name.strip()
    }
    if resolved is TaskProfile.EXPLICIT_READONLY:
        allowed = set(requested & READ_ONLY_PROFILE_TOOLS)
        for name in tuple(allowed):
            allowed.update(_EXPLICIT_READONLY_SUPPORT.get(name, ()))
    elif resolved in {TaskProfile.PLAN_ONLY, TaskProfile.REVIEW_ONLY}:
        allowed = set(READ_ONLY_PROFILE_TOOLS)
    elif resolved is TaskProfile.NO_EDIT_EXECUTION_ALLOWED:
        allowed = set(READ_ONLY_PROFILE_TOOLS)
        allowed.update(requested & COMMAND_TOOL_NAMES)
    elif resolved is TaskProfile.EDIT_TASK:
        allowed = set(
            READ_ONLY_PROFILE_TOOLS | WRITE_TOOL_NAMES | COMMAND_TOOL_NAMES
        )
    elif resolved is TaskProfile.BROWSER_TASK:
        allowed = set(
            READ_ONLY_PROFILE_TOOLS
            | WRITE_TOOL_NAMES
            | COMMAND_TOOL_NAMES
            | BROWSER_TOOL_NAMES
        )
    elif resolved is TaskProfile.COMMIT_TASK:
        allowed = set(
            READ_ONLY_PROFILE_TOOLS
            | WRITE_TOOL_NAMES
            | COMMAND_TOOL_NAMES
            | COMMIT_EXECUTION_TOOL_NAMES
        )
    else:
        allowed = set(
            {
                "list_dir",
                "read_file",
                "read_json",
                "read_yaml",
                "dependency_summary",
                "create_dir",
                "write_file",
                "run_command",
                "run_validation",
            }
        )

    available = set(available_tools) if available_tools is not None else None
    if resolved not in {
        TaskProfile.EXPLICIT_READONLY,
        TaskProfile.PLAN_ONLY,
        TaskProfile.REVIEW_ONLY,
        TaskProfile.NO_EDIT_EXECUTION_ALLOWED,
    }:
        relevant_extensions = requested - BUILTIN_TASK_PROFILE_TOOLS
        if available is not None:
            relevant_extensions &= available
        allowed.update(relevant_extensions)

    if browser_intent and resolved in {
        TaskProfile.BROWSER_TASK,
        TaskProfile.COMMIT_TASK,
        TaskProfile.NEW_PROJECT,
        TaskProfile.NO_EDIT_EXECUTION_ALLOWED,
    }:
        allowed.update(BROWSER_TOOL_NAMES)
        if available is not None:
            allowed.update(
                name for name in available if name.startswith("mcp.playwright.")
            )
    else:
        allowed.difference_update(BROWSER_TOOL_NAMES)

    if not (
        resolved is TaskProfile.COMMIT_TASK and commit_requested
    ):
        allowed.difference_update(COMMIT_EXECUTION_TOOL_NAMES)

    allowed.difference_update(blocked)
    if available is not None:
        allowed.intersection_update(available)
    return tuple(sorted(allowed))


def _normalize_task_profile(profile: TaskProfile | str) -> TaskProfile:
    if isinstance(profile, TaskProfile):
        return profile
    if not isinstance(profile, str):
        raise ValueError("Task profile must be a string or TaskProfile.")
    normalized = profile.strip().lower().replace("-", "_")
    try:
        return TaskProfile(normalized)
    except ValueError as exc:
        supported = ", ".join(item.value for item in TaskProfile)
        raise ValueError(
            f"Unknown task profile {profile!r}. Expected one of: {supported}."
        ) from exc


def _mentioned_names(text: str, names: Iterable[str]) -> tuple[str, ...]:
    mentioned = []
    for name in sorted(set(names)):
        pattern = re.compile(
            rf"(?i)(?<![a-zA-Z0-9_-]){re.escape(name)}"
            rf"(?![a-zA-Z0-9_-])"
        )
        if pattern.search(text):
            mentioned.append(name)
    return tuple(mentioned)


def _external_tool_is_relevant(request: str, internal_name: str) -> bool:
    if internal_name in BUILTIN_TASK_PROFILE_TOOLS:
        return False
    request_words = set(re.findall(r"[a-zA-Z0-9]+", request.casefold()))
    tool_words = {
        word
        for word in re.findall(r"[a-zA-Z0-9]+", internal_name.casefold())
        if word not in {"mcp", "plugin", "plugins", "tool", "tools"}
    }
    if len(request_words & tool_words) >= 2:
        return True
    namespace = internal_name.split(".", 1)[0].casefold()
    return "plugin" in request_words and namespace in request_words


@dataclass(frozen=True)
class Tool:
    """A named handler and its model-facing JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler = field(repr=False, compare=False)
    permission: PermissionLevel = PermissionLevel.READ
    plan_safe: bool = False
    read_only_extension: bool = False


class ToolRegistry:
    def __init__(
        self,
        tools: Iterable[Tool] = (),
        permission_manager: PermissionManager | None = None,
        session_changed_files: list[str] | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._model_names_by_internal: dict[str, str] = {}
        self._internal_names_by_model: dict[str, str] = {}
        self._permission_manager = permission_manager or PermissionManager()
        self._session_changed_files = (
            session_changed_files
            if session_changed_files is not None
            else []
        )
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

    def relevant_tool_names(self, request: str) -> tuple[str, ...]:
        """Return registered tools explicitly named or clearly matched by intent."""
        text = request if isinstance(request, str) else str(request)
        mentioned = []
        for internal_name in self.names():
            model_name = self._model_names_by_internal[internal_name]
            if _mentioned_names(
                text,
                (internal_name, model_name),
            ) or _external_tool_is_relevant(text, internal_name):
                mentioned.append(internal_name)
        return tuple(mentioned)

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
        profile: TaskProfile | str | None = None,
        requested_tools: Iterable[str] = (),
        browser_intent: bool = False,
        commit_requested: bool = False,
        blocked_tools: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Return LiteLLM/OpenAI-compatible function tool schemas."""
        requested_tool_names = tuple(requested_tools)
        blocked_tool_names = tuple(blocked_tools)
        resolved_profile = (
            None if profile is None else _normalize_task_profile(profile)
        )
        profile_names = (
            None
            if resolved_profile is None
            else set(
                tool_names_for_profile(
                    resolved_profile,
                    requested_tools=requested_tool_names,
                    available_tools=self._tools,
                    browser_intent=browser_intent,
                    commit_requested=commit_requested,
                    blocked_tools=blocked_tool_names,
                )
            )
        )
        if resolved_profile in {
            TaskProfile.REVIEW_ONLY,
            TaskProfile.NO_EDIT_EXECUTION_ALLOWED,
        }:
            blocked = set(blocked_tool_names)
            profile_names.update(
                name
                for name in requested_tool_names
                if name in self._tools
                and self._tools[name].read_only_extension
                and name not in blocked
            )
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
            if profile_names is None or tool.name in profile_names
            if not read_only
            or tool.permission is PermissionLevel.READ
            or tool.plan_safe
            if allow_execute
            or (
                tool.permission is not PermissionLevel.EXECUTE
                and tool.name not in COMMIT_EXECUTION_TOOL_NAMES
            )
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
        self._record_session_changed_file(tool.name, result)
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

    def session_changed_files(self) -> tuple[str, ...]:
        """Return bounded files successfully changed through this registry."""
        return tuple(self._session_changed_files)

    def _record_session_changed_file(
        self,
        tool_name: str,
        result: Mapping[str, Any],
    ) -> None:
        if (
            tool_name not in _SESSION_FILE_MUTATION_TOOLS
            or result.get("ok") is not True
        ):
            return
        path = result.get("path")
        if (
            not isinstance(path, str)
            or not path
            or path in self._session_changed_files
            or len(self._session_changed_files) >= MAX_SESSION_CHANGED_FILES
        ):
            return
        self._session_changed_files.append(path)


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


def create_read_only_registry(
    project_root: str | Path,
    *,
    mode: str = "default",
    runtime_mode: str = "local",
    session_changed_files: list[str] | None = None,
) -> ToolRegistry:
    """Create a registry containing only the current read-only tools."""
    session_tracker = (
        session_changed_files
        if session_changed_files is not None
        else []
    )
    allow_git_inspection = (
        mode.strip().lower() != "no-command"
        and runtime_mode.strip().lower() != "no-command"
    )
    git_mode = (
        "no-command"
        if (
            mode.strip().lower() == "no-command"
            or runtime_mode.strip().lower() == "no-command"
        )
        else mode
    )
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
                name="read_json",
                description=(
                    "Safely parse one bounded project JSON file. Secret-looking, "
                    "runtime, and generated paths are blocked."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_REQUEST_PATH_CHARACTERS,
                            "description": "Project-relative JSON file path.",
                        },
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_STRUCTURED_MAX_BYTES,
                            "description": "Maximum file bytes to read.",
                            "default": DEFAULT_STRUCTURED_MAX_BYTES,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=partial(read_json, project_root),
            ),
            Tool(
                name="read_yaml",
                description=(
                    "Safely parse one bounded project YAML file with safe_load. "
                    "Secret-looking, runtime, and generated paths are blocked."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_REQUEST_PATH_CHARACTERS,
                            "description": "Project-relative YAML file path.",
                        },
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_STRUCTURED_MAX_BYTES,
                            "description": "Maximum file bytes to read.",
                            "default": DEFAULT_STRUCTURED_MAX_BYTES,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=partial(read_yaml, project_root),
            ),
            Tool(
                name="read_many_files",
                description=(
                    "Read a small bounded set of related UTF-8 project files. "
                    "Returns independent per-file results and skips binary, "
                    "secret-looking, runtime, and generated paths."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_REQUEST_PATH_CHARACTERS,
                            },
                            "minItems": 1,
                            "maxItems": MAX_MANY_FILES,
                            "description": (
                                "Small list of project-relative text file paths."
                            ),
                        },
                        "max_bytes_per_file": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_MANY_BYTES_PER_FILE,
                            "description": "Maximum bytes returned per file.",
                            "default": DEFAULT_MANY_BYTES_PER_FILE,
                        },
                        "max_total_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_MANY_TOTAL_BYTES,
                            "description": "Maximum bytes returned across files.",
                            "default": DEFAULT_MANY_TOTAL_BYTES,
                        },
                    },
                    "required": ["paths"],
                    "additionalProperties": False,
                },
                handler=partial(read_many_files, project_root),
            ),
            Tool(
                name="list_symbols",
                description=(
                    "List bounded definitions and line numbers from one Python, "
                    "JavaScript, JSX, TypeScript, or TSX source file without "
                    "executing project code."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_REQUEST_PATH_CHARACTERS,
                            "description": "Project-relative source file path.",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=partial(list_symbols, project_root),
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
            Tool(
                name="project_health",
                description=(
                    "Return a compact read-only project readiness summary. Use "
                    "this first for broad reviews, audits, explanations, or "
                    "onboarding, but not for routine tiny edits."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=partial(
                    project_health,
                    project_root,
                    allow_git=allow_git_inspection,
                ),
            ),
            Tool(
                name="ci_summary",
                description=(
                    "Return a bounded, redacted summary of supported CI YAML "
                    "providers, jobs, runtime hints, package managers, and "
                    "validation commands without executing CI."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=partial(ci_summary, project_root),
            ),
            Tool(
                name="dependency_summary",
                description=(
                    "Statically summarize bounded dependency, script, framework, "
                    "and likely command metadata without reading lockfile bodies "
                    "or running project code. Use before guessing validation."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=partial(dependency_summary, project_root),
            ),
            Tool(
                name="git_status",
                description=(
                    "Return bounded read-only Git status with compact modified, "
                    "staged, untracked, and excluded path metadata."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=partial(
                    git_status,
                    project_root,
                    mode=git_mode,
                ),
            ),
            Tool(
                name="git_diff",
                description=(
                    "Return a bounded staged or unstaged Git diff. Runtime, "
                    "generated, and secret-looking file contents are excluded."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Optional project-relative file path to inspect."
                            ),
                        },
                        "staged": {
                            "type": "boolean",
                            "description": "Inspect the staged diff.",
                            "default": False,
                        },
                        "max_lines": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 2000,
                            "description": "Maximum diff lines to return.",
                            "default": 400,
                        },
                    },
                    "additionalProperties": False,
                },
                handler=partial(
                    git_diff,
                    project_root,
                    mode=git_mode,
                ),
            ),
            Tool(
                name="list_changed_files",
                description=(
                    "Combine current-session file changes with bounded Git state "
                    "and mark staged, untracked, excluded, and commit-candidate "
                    "paths."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["session", "git", "both"],
                            "description": "Changed-file source to inspect.",
                            "default": "both",
                        }
                    },
                    "additionalProperties": False,
                },
                handler=partial(
                    list_changed_files,
                    project_root,
                    session_files=session_tracker,
                    mode=git_mode,
                ),
            ),
        ),
        session_changed_files=session_tracker,
    )


def create_tool_registry(
    project_root: str | Path,
    mode: str = "default",
    approval_callback: ApprovalCallback | None = None,
    *,
    approval_provider: ApprovalProvider | None = None,
    approval_event_callback: ApprovalEventCallback | None = None,
    runtime_mode: str = "local",
    project_trust: str = "auto",
    allow_network: bool = False,
    mcp_client: MCPClient | None = None,
    plugins: Sequence[LoadedPlugin] = (),
    plugin_resolver: EntrypointResolver | None = None,
    session_changed_files: list[str] | None = None,
) -> ToolRegistry:
    """Create built-ins and explicitly enabled external extension tools."""
    from lunar_forge.project_detection import resolve_project_trust

    normalized_mode = mode.strip().lower()
    resolved_project_trust = resolve_project_trust(
        project_root,
        project_trust,
    )
    session_tracker = (
        session_changed_files
        if session_changed_files is not None
        else []
    )
    read_registry = create_read_only_registry(
        project_root,
        mode=normalized_mode,
        runtime_mode=runtime_mode,
        session_changed_files=session_tracker,
    )
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
            approval_provider=approval_provider,
            approval_callback=approval_callback,
            approval_event_callback=approval_event_callback,
            runtime_mode=runtime_mode,
            project_trust=resolved_project_trust,
        ),
        session_changed_files=session_tracker,
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
    tools = [
        Tool(
            name="run_command",
            description=(
                "Run one command in the project through the configured local "
                "or Docker runner after approval. Shell operators and dangerous "
                "commands are not supported."
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
                    "full_page": {
                        "type": "boolean",
                        "description": "Capture the entire scrollable page.",
                        "default": False,
                    },
                    "width": {
                        "type": "integer",
                        "minimum": 320,
                        "maximum": 3840,
                        "description": "Browser viewport width in pixels.",
                        "default": 1280,
                    },
                    "height": {
                        "type": "integer",
                        "minimum": 240,
                        "maximum": 2160,
                        "description": "Browser viewport height in pixels.",
                        "default": 720,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=partial(_run_browser_validation, project_root),
            permission=PermissionLevel.EXECUTE,
        ),
    ]
    if runtime_mode.strip().lower() == "local":
        tools.append(
            Tool(
                name="run_managed_browser_validation",
                description=(
                    "After approval, start a project dev server with shell disabled, "
                    "wait for its loopback URL, validate it in Playwright, and stop "
                    "the server. Use only when project detection supplies a likely "
                    "dev command and local URL."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Project dev command requiring approval.",
                        },
                        "url": {
                            "type": "string",
                            "description": "Expected local loopback HTTP(S) URL.",
                        },
                        "screenshot": {
                            "type": "boolean",
                            "description": "Capture a screenshot.",
                            "default": True,
                        },
                        "checks": {
                            "type": "array",
                            "description": "Optional CSS selectors that must match.",
                            "items": {"type": "string", "maxLength": 500},
                            "maxItems": 20,
                        },
                        "full_page": {
                            "type": "boolean",
                            "description": "Capture the entire scrollable page.",
                            "default": False,
                        },
                        "width": {
                            "type": "integer",
                            "minimum": 320,
                            "maximum": 3840,
                            "default": 1280,
                        },
                        "height": {
                            "type": "integer",
                            "minimum": 240,
                            "maximum": 2160,
                            "default": 720,
                        },
                        "startup_timeout_ms": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 300000,
                            "description": "Maximum wait for the URL to respond.",
                            "default": 30000,
                        },
                    },
                    "required": ["command", "url"],
                    "additionalProperties": False,
                },
                handler=partial(_run_managed_browser_validation, project_root),
                permission=PermissionLevel.EXECUTE,
            )
        )
    return tuple(tools)


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
    full_page: bool = False,
    width: int = 1280,
    height: int = 720,
) -> dict[str, Any]:
    """Import the optional browser workflow only after tool approval."""
    from lunar_forge.workflows.browser_validation import run_browser_validation

    return run_browser_validation(
        url,
        screenshot=screenshot,
        checks=checks,
        full_page=full_page,
        width=width,
        height=height,
        project_root=project_root,
    )


def _run_managed_browser_validation(
    project_root: str | Path,
    command: str,
    url: str,
    screenshot: bool = True,
    checks: list[str] | None = None,
    full_page: bool = False,
    width: int = 1280,
    height: int = 720,
    startup_timeout_ms: int = 30_000,
) -> dict[str, Any]:
    """Run the managed workflow after ToolRegistry has approved the command."""
    from lunar_forge.workflows.browser_validation import (
        run_managed_browser_validation,
    )

    return run_managed_browser_validation(
        command,
        url,
        screenshot=screenshot,
        checks=checks,
        full_page=full_page,
        width=width,
        height=height,
        startup_timeout_ms=startup_timeout_ms,
        project_root=project_root,
        approval_callback=lambda request: True,
    )
