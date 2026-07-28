"""Safe persistent working-memory compaction for continuous chat."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from lunar_forge.config import AppConfig
from lunar_forge.events import AgentEvent, sanitize_event_payload
from lunar_forge.model_clients import ModelClient, ModelResponse
from lunar_forge.runtime.conversation import (
    ConversationMemory,
    TokenPressureEstimate,
    estimate_tokens,
    should_compact,
)
from lunar_forge.runtime.sessions import project_fingerprint
from lunar_forge.tools.files import safe_path


SUMMARY_SCHEMA_VERSION = 1
MAX_SUMMARY_FILE_CHARACTERS = 200_000
MAX_SUMMARY_TEXT_CHARACTERS = 50_000
MAX_COMPACTION_SOURCE_CHARACTERS = 120_000
MAX_FACT_ITEMS = 50
MAX_FACT_TEXT_CHARACTERS = 1_000
MAX_RECENT_MESSAGES_IN_SUMMARY = 4
_SUMMARY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
_INSTRUCTION_PATH_PATTERN = re.compile(
    r"Project instructions from ([^\s]+) \(scope:",
)
_PRIVATE_REASONING_KEYS = frozenset(
    {
        "chainofthought",
        "hiddenreasoning",
        "privatereasoning",
        "reasoning",
        "reasoningcontent",
        "reasoningtext",
    }
)
_PRIVATE_REASONING_LINE = re.compile(
    r"(?i)^\s*(?:chain[-_\s]*of[-_\s]*thought|hidden[-_\s]*reasoning|"
    r"private[-_\s]*reasoning|reasoning[-_\s]*(?:content|text))\s*[:=]",
)
_PRIVATE_REASONING_BLOCK = re.compile(
    r"(?is)<(?:think|reasoning)>.*?</(?:think|reasoning)>",
)
_OPEN_ITEM_PATTERN = re.compile(
    r"(?i)\b(?:todo|open item|remaining|pending|unresolved|next step)\b",
)
_CONSTRAINT_PATTERN = re.compile(
    r"(?i)\b(?:do not|don't|must|must not|never|only|without|keep|preserve)\b",
)
_TOOL_RESULT_KEYS = frozenset(
    {
        "ok",
        "error",
        "path",
        "file_path",
        "changed_files",
        "exit_code",
        "duration_ms",
        "truncated",
        "command",
        "status",
        "summary",
        "validation",
        "checkpoint_path",
        "artifact_path",
        "screenshot_path",
    }
)


class CompactionError(RuntimeError):
    """A recoverable failure while compacting chat context."""


@dataclass(frozen=True, slots=True)
class CompactionResult:
    triggered: bool
    compacted: bool
    pressure: TokenPressureEstimate
    reason: str
    summary_path: str | None = None
    summary_record: Mapping[str, Any] | None = None
    messages_before: int = 0
    messages_after: int = 0
    model_usage: Mapping[str, Any] | None = None


class ConversationCompactor:
    """Estimate, summarize, sanitize, persist, and replace older chat turns."""

    def __init__(
        self,
        project_root: str | Path,
        session_id: str,
        config: AppConfig,
        model_client: ModelClient,
        *,
        seed_facts: Mapping[str, Any] | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        if not self.project_root.is_dir():
            raise NotADirectoryError(
                f"Project root is not a directory: {self.project_root}"
            )
        if not _SUMMARY_ID_PATTERN.fullmatch(session_id):
            raise ValueError("Session id is not safe for a summary filename.")
        self.session_id = session_id
        self.config = config
        self.model_client = model_client
        self.seed_facts = dict(seed_facts or {})

    def maybe_compact(
        self,
        memory: ConversationMemory,
        *,
        incoming_user_text: str,
        instruction_context: str,
        events: Sequence[AgentEvent | Mapping[str, Any]],
        source_session_event_count: int,
        pending_operation: bool = False,
    ) -> CompactionResult:
        relevant_results = relevant_tool_results(events)
        pressure = memory.estimate_pressure(
            instruction_context=instruction_context,
            relevant_tool_results=relevant_results,
            incoming_user_text=incoming_user_text,
        )
        before = memory.message_count
        if not should_compact(
            pressure,
            self.config.ui.chat.compact_at_tokens,
        ):
            return CompactionResult(
                triggered=False,
                compacted=False,
                pressure=pressure,
                reason="Token pressure is below the compaction threshold.",
                messages_before=before,
                messages_after=before,
            )
        if pending_operation:
            return CompactionResult(
                triggered=False,
                compacted=False,
                pressure=pressure,
                reason=(
                    "Compaction is deferred while an approval or tool call "
                    "is pending."
                ),
                messages_before=before,
                messages_after=before,
            )
        if not memory.can_compact():
            return CompactionResult(
                triggered=False,
                compacted=False,
                pressure=pressure,
                reason="There are no older turns that can be compacted safely.",
                messages_before=before,
                messages_after=before,
            )

        partition = memory.partition()
        deterministic_facts = _collect_facts(
            project_root=self.project_root,
            config=self.config,
            messages=(
                *memory.messages,
                {"role": "user", "content": incoming_user_text},
            ),
            events=events,
            instruction_context=instruction_context,
            seed_facts=self.seed_facts,
        )
        model_messages = _compaction_messages(
            older_messages=partition.older,
            facts=deterministic_facts,
            instruction_context=instruction_context,
            compact_to_tokens=self.config.ui.chat.compact_to_tokens,
        )
        try:
            response = self.model_client.complete(model_messages, tools=[])
        except Exception as exc:
            raise CompactionError(
                f"Working-memory compaction model call failed: {exc}"
            ) from exc
        if response.tool_calls:
            raise CompactionError(
                "Working-memory compaction returned tool calls; no tools were run."
            )

        summary_text, model_facts = _parse_model_summary(response)
        facts = _merge_facts(deterministic_facts, model_facts)
        max_summary_characters = max(
            256,
            min(
                MAX_SUMMARY_TEXT_CHARACTERS,
                self.config.ui.chat.compact_to_tokens * 4,
            ),
        )
        summary_text = _bounded_text(
            _strip_private_reasoning(summary_text).strip(),
            max_summary_characters,
        )
        if not summary_text:
            raise CompactionError(
                "Working-memory compaction returned an empty safe summary."
            )

        last_event_id = _last_event_id(events)
        summary_record: dict[str, Any] = {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "session_id": self.session_id,
            "source_events_through": (
                last_event_id
                or f"session-record-{source_session_event_count}"
            ),
            "source_event_count": source_session_event_count,
            "created_at": _timestamp(),
            "summary": summary_text,
            "facts": facts,
            "recent_messages": list(
                partition.recent[-MAX_RECENT_MESSAGES_IN_SUMMARY:]
            ),
        }
        summary_record = _safe_summary_record(summary_record)
        summary_path = _write_summary(
            self.project_root,
            self.session_id,
            summary_record,
        )
        memory.replace_older_with_summary(summary_record)
        self.seed_facts = dict(summary_record.get("facts", {}))
        usage = _compaction_usage(
            response,
            model_messages=model_messages,
            config=self.config,
        )
        return CompactionResult(
            triggered=True,
            compacted=True,
            pressure=pressure,
            reason="Older conversation turns were compacted.",
            summary_path=summary_path,
            summary_record=summary_record,
            messages_before=before,
            messages_after=memory.message_count,
            model_usage=usage,
        )


def relevant_tool_results(
    events: Sequence[AgentEvent | Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    results: list[dict[str, Any]] = []
    for raw_event in events:
        event_type, payload, _ = _event_parts(raw_event)
        if event_type not in {"tool.finished", "tool.failed"}:
            continue
        tool_name = _first_text(
            payload,
            "tool_name",
            "internal_tool_name",
            "name",
        ) or "unknown"
        raw_result = payload.get("result", {})
        result = raw_result if isinstance(raw_result, Mapping) else {}
        selected = {
            str(key): _bounded_value(value)
            for key, value in result.items()
            if str(key) in _TOOL_RESULT_KEYS
        }
        if payload.get("error") and "error" not in selected:
            selected["error"] = _bounded_value(payload["error"])
        results.append(
            {
                "tool_name": tool_name,
                "ok": payload.get("ok", result.get("ok")),
                "result": selected,
                "historical_context_only": True,
            }
        )
        if len(results) >= MAX_FACT_ITEMS:
            break
    safe = sanitize_event_payload({"results": results})
    raw_safe_results = safe.get("results", [])
    if not isinstance(raw_safe_results, list):
        return ()
    return tuple(
        dict(item)
        for item in raw_safe_results
        if isinstance(item, Mapping)
    )


def _collect_facts(
    *,
    project_root: Path,
    config: AppConfig,
    messages: Sequence[Mapping[str, Any]],
    events: Sequence[AgentEvent | Mapping[str, Any]],
    instruction_context: str,
    seed_facts: Mapping[str, Any],
) -> dict[str, Any]:
    changed_files = _string_list(seed_facts.get("changed_files"))
    validation = _mapping_list(seed_facts.get("validation"))
    approvals = _mapping_list(seed_facts.get("approval_decisions"))
    open_items = _string_list(seed_facts.get("open_items"))
    important_results = _mapping_list(seed_facts.get("important_tool_results"))
    unresolved_errors = _string_list(seed_facts.get("unresolved_errors"))
    constraints = _string_list(seed_facts.get("user_constraints"))
    instruction_notes = _string_list(seed_facts.get("instruction_notes"))

    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for line in content.splitlines():
            normalized = line.strip()
            if not normalized:
                continue
            if _OPEN_ITEM_PATTERN.search(normalized):
                open_items.append(_bounded_text(normalized, MAX_FACT_TEXT_CHARACTERS))
            if (
                message.get("role") == "user"
                and _CONSTRAINT_PATTERN.search(normalized)
            ):
                constraints.append(
                    _bounded_text(normalized, MAX_FACT_TEXT_CHARACTERS)
                )

    important_results.extend(relevant_tool_results(events))
    for raw_event in events:
        event_type, payload, _ = _event_parts(raw_event)
        result = payload.get("result", {})
        result_mapping = result if isinstance(result, Mapping) else {}
        if event_type == "checkpoint.created":
            _append_paths(changed_files, payload.get("path"))
        if event_type in {"tool.finished", "tool.failed"}:
            _append_paths(changed_files, result_mapping.get("changed_files"))
            tool_name = _first_text(payload, "tool_name", "name") or ""
            if tool_name in {
                "write_file",
                "edit_file",
                "replace_lines",
                "insert_lines",
                "create_dir",
            }:
                _append_paths(
                    changed_files,
                    result_mapping.get("path") or payload.get("path"),
                )
        elif event_type == "validation.finished":
            validation.append(
                {
                    "tool_name": _first_text(payload, "tool_name", "name"),
                    "ok": payload.get("ok"),
                    "error": _bounded_value(payload.get("error")),
                }
            )
        elif event_type == "permission.resolved":
            approvals.append(
                {
                    "request_id": payload.get("request_id") or payload.get("id"),
                    "approved": payload.get("approved"),
                    "source": payload.get("source"),
                    "reason": _bounded_value(payload.get("reason")),
                    "historical_context_only": True,
                }
            )
        elif event_type == "error":
            error = _first_text(payload, "message", "error")
            if error:
                unresolved_errors.append(
                    _bounded_text(error, MAX_FACT_TEXT_CHARACTERS)
                )

    instruction_paths = _INSTRUCTION_PATH_PATTERN.findall(instruction_context)
    if instruction_paths:
        instruction_notes.extend(
            f"Reload current instructions from {path} before acting."
            for path in instruction_paths
        )
    else:
        instruction_notes.append(
            "Reload current project AGENTS.md instructions before acting."
        )

    last_user = next(
        (
            str(message["content"]).strip()
            for message in reversed(messages)
            if message.get("role") == "user"
            and isinstance(message.get("content"), str)
            and str(message["content"]).strip()
        ),
        "",
    )
    facts = {
        "project_root": str(project_root),
        "project_fingerprint": project_fingerprint(project_root),
        "runtime_mode": config.runtime.mode,
        "permission_mode": config.permissions.mode,
        "model": config.model.model,
        "reasoning_effort": config.model.reasoning.effort,
        "current_task_goal": _bounded_text(
            last_user,
            MAX_FACT_TEXT_CHARACTERS,
        ),
        "user_constraints": _dedupe(constraints),
        "changed_files": _dedupe(changed_files),
        "validation": _dedupe_mappings(validation),
        "approval_decisions": _dedupe_mappings(approvals),
        "open_items": _dedupe(open_items),
        "important_tool_results": _dedupe_mappings(important_results),
        "unresolved_errors": _dedupe(unresolved_errors),
        "instruction_notes": _dedupe(instruction_notes),
        "staleness_notice": (
            "File contents and tool results are historical context only; "
            "reload current project state before treating them as authoritative."
        ),
    }
    safe = sanitize_event_payload(
        _drop_private_reasoning(facts),
    )
    return safe


def _compaction_messages(
    *,
    older_messages: Sequence[Mapping[str, Any]],
    facts: Mapping[str, Any],
    instruction_context: str,
    compact_to_tokens: int,
) -> list[dict[str, str]]:
    source = {
        "older_messages": _bounded_source_messages(older_messages),
        "known_facts": facts,
        "instruction_context": _bounded_text(
            instruction_context,
            MAX_COMPACTION_SOURCE_CHARACTERS // 3,
        ),
    }
    safe_source = sanitize_event_payload(
        _drop_private_reasoning(source),
    )
    return [
        {
            "role": "system",
            "content": (
                "Summarize older LunarForge chat context for safe continuation. "
                "Return one JSON object with keys summary and facts. Keep the "
                f"result near {compact_to_tokens} tokens or fewer. Preserve task "
                "goals, user constraints, changed files, validation, approval "
                "decisions as historical context, open TODOs, important bounded "
                "tool results, unresolved errors, and relevant instruction notes. "
                "Never include hidden reasoning, chain-of-thought, credentials, "
                "secret values, huge raw output, or stale file contents as "
                "authoritative facts. Do not request or call tools."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                safe_source,
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]


def _parse_model_summary(
    response: ModelResponse,
) -> tuple[str, Mapping[str, Any]]:
    raw_text = _strip_private_reasoning(response.text).strip()
    if not raw_text:
        return "", {}
    candidate = raw_text
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
            if candidate.casefold().startswith("json\n"):
                candidate = candidate[5:].strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return raw_text, {}
    if not isinstance(parsed, Mapping):
        return raw_text, {}
    clean = _drop_private_reasoning(parsed)
    summary = clean.get("summary", "")
    facts = clean.get("facts", {})
    return (
        str(summary).strip(),
        facts if isinstance(facts, Mapping) else {},
    )


def _merge_facts(
    deterministic: Mapping[str, Any],
    model_facts: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(deterministic)
    safe_model = sanitize_event_payload(
        _drop_private_reasoning(model_facts),
    )
    for key in (
        "user_constraints",
        "changed_files",
        "validation",
        "approval_decisions",
        "open_items",
        "important_tool_results",
        "unresolved_errors",
        "instruction_notes",
    ):
        current = merged.get(key, [])
        proposed = safe_model.get(key, [])
        if key in {
            "validation",
            "approval_decisions",
            "important_tool_results",
        }:
            merged[key] = _dedupe_mappings(
                [*_mapping_list(current), *_mapping_list(proposed)]
            )
        else:
            merged[key] = _dedupe(
                [*_string_list(current), *_string_list(proposed)]
            )
    proposed_goal = safe_model.get("current_task_goal")
    if isinstance(proposed_goal, str) and proposed_goal.strip():
        merged["current_task_goal"] = _bounded_text(
            proposed_goal.strip(),
            MAX_FACT_TEXT_CHARACTERS,
        )
    return sanitize_event_payload(_drop_private_reasoning(merged))


def _safe_summary_record(record: Mapping[str, Any]) -> dict[str, Any]:
    safe = sanitize_event_payload(_drop_private_reasoning(record))
    if safe.get("truncated") is True:
        raise CompactionError(
            "Working-memory summary exceeded the safe payload limit."
        )
    serialized = json.dumps(
        safe,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    if len(serialized) > MAX_SUMMARY_FILE_CHARACTERS:
        raise CompactionError(
            "Working-memory summary exceeded the persistent size limit."
        )
    return safe


def _write_summary(
    project_root: Path,
    session_id: str,
    record: Mapping[str, Any],
) -> str:
    summaries_directory = safe_path(project_root, ".agent/summaries")
    summaries_directory.mkdir(parents=True, exist_ok=True)
    summary_path = safe_path(
        project_root,
        summaries_directory / f"{session_id}.summary.json",
    )
    temporary_path = safe_path(
        project_root,
        summaries_directory / f".{session_id}-{uuid4().hex}.tmp",
    )
    serialized = json.dumps(
        record,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    try:
        temporary_path.write_text(f"{serialized}\n", encoding="utf-8")
        temporary_path.replace(summary_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return summary_path.relative_to(project_root).as_posix()


def _compaction_usage(
    response: ModelResponse,
    *,
    model_messages: Sequence[Mapping[str, Any]],
    config: AppConfig,
) -> dict[str, Any]:
    input_characters = len(
        json.dumps(model_messages, ensure_ascii=False, default=str)
    )
    output_characters = len(response.text)
    usage = response.usage
    input_tokens = (
        usage.input_tokens
        if usage is not None and usage.input_tokens is not None
        else estimate_tokens(input_characters)
    )
    output_tokens = (
        usage.output_tokens
        if usage is not None and usage.output_tokens is not None
        else estimate_tokens(output_characters)
    )
    total_tokens = (
        usage.total_tokens
        if usage is not None and usage.total_tokens is not None
        else input_tokens + output_tokens
    )
    return {
        "phase": "memory_compaction",
        "role": "compactor",
        "model": (
            usage.model
            if usage is not None and usage.model
            else response.model or config.model.model
        ),
        "provider": usage.provider if usage is not None else None,
        "reasoning_effort": config.model.reasoning.effort,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "exact": usage.exact if usage is not None else False,
        "estimated": usage is None or not usage.exact,
        "usage_source": (
            "provider" if usage is not None and usage.exact else "estimate"
        ),
        "messages_count": len(model_messages),
        "tool_schema_count": 0,
        "context_estimate_method": "characters_divided_by_4_rounded_up",
        "context_components": {
            "messages_characters": input_characters,
            "messages_token_estimate": estimate_tokens(input_characters),
            "response_characters": output_characters,
            "response_token_estimate": estimate_tokens(output_characters),
            "tool_schemas_characters": 0,
            "tool_schemas_token_estimate": 0,
        },
    }


def _event_parts(
    raw_event: AgentEvent | Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], str | None]:
    if isinstance(raw_event, AgentEvent):
        return raw_event.type, raw_event.payload, raw_event.event_id
    event_type = raw_event.get("type") or raw_event.get("event") or ""
    payload = raw_event.get("payload", raw_event.get("data", {}))
    if not isinstance(payload, Mapping):
        payload = {}
    event_id = raw_event.get("event_id")
    return (
        str(event_type),
        payload,
        str(event_id) if isinstance(event_id, str) else None,
    )


def _last_event_id(
    events: Sequence[AgentEvent | Mapping[str, Any]],
) -> str | None:
    for event in reversed(events):
        _, _, event_id = _event_parts(event)
        if event_id:
            return event_id
    return None


def _bounded_source_messages(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    remaining = MAX_COMPACTION_SOURCE_CHARACTERS
    for message in reversed(messages):
        role = str(message.get("role", "")).strip().casefold()
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        clean = _strip_private_reasoning(content)
        if len(clean) > remaining:
            clean = _bounded_text(clean, remaining)
        if not clean:
            continue
        selected.append({"role": role, "content": clean})
        remaining -= len(clean)
        if remaining <= 0:
            break
    selected.reverse()
    return selected


def _drop_private_reasoning(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _drop_private_reasoning(item)
            for key, item in value.items()
            if _normalized_key(str(key)) not in _PRIVATE_REASONING_KEYS
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_drop_private_reasoning(item) for item in value]
    if isinstance(value, str):
        return _strip_private_reasoning(value)
    return value


def _strip_private_reasoning(value: str) -> str:
    without_blocks = _PRIVATE_REASONING_BLOCK.sub(
        "[private reasoning removed]",
        value,
    )
    return "\n".join(
        line
        for line in without_blocks.splitlines()
        if not _PRIVATE_REASONING_LINE.match(line)
    )


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _first_text(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _append_paths(destination: list[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        destination.append(_bounded_text(value.strip(), MAX_FACT_TEXT_CHARACTERS))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, str) and item.strip():
                destination.append(
                    _bounded_text(item.strip(), MAX_FACT_TEXT_CHARACTERS)
                )


def _bounded_value(value: Any) -> Any:
    if isinstance(value, str):
        return _bounded_text(value, MAX_FACT_TEXT_CHARACTERS)
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_value(item)
            for index, (key, item) in enumerate(value.items())
            if index < MAX_FACT_ITEMS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_bounded_value(item) for item in value[:MAX_FACT_ITEMS]]
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [
        _bounded_text(item.strip(), MAX_FACT_TEXT_CHARACTERS)
        for item in value
        if isinstance(item, str) and item.strip()
    ]


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [
        dict(item)
        for item in value
        if isinstance(item, Mapping)
    ]


def _dedupe(values: Sequence[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.casefold()
        if not value or normalized in seen:
            continue
        selected.append(value)
        seen.add(normalized)
        if len(selected) >= MAX_FACT_ITEMS:
            break
    return selected


def _dedupe_mappings(
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        safe_value = sanitize_event_payload(
            _drop_private_reasoning(dict(value)),
        )
        marker = json.dumps(
            safe_value,
            ensure_ascii=False,
            sort_keys=True,
        )
        if marker in seen:
            continue
        selected.append(safe_value)
        seen.add(marker)
        if len(selected) >= MAX_FACT_ITEMS:
            break
    return selected


def _bounded_text(value: str, maximum: int) -> str:
    if maximum <= 0:
        return ""
    if len(value) <= maximum:
        return value
    marker = "\n...[compaction value truncated]"
    if maximum <= len(marker):
        return value[:maximum]
    keep = max(0, maximum - len(marker))
    return f"{value[:keep]}{marker}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


__all__ = [
    "CompactionError",
    "CompactionResult",
    "ConversationCompactor",
    "MAX_SUMMARY_FILE_CHARACTERS",
    "SUMMARY_SCHEMA_VERSION",
    "relevant_tool_results",
]
