"""Small, permission-gated starter workflows for empty projects."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lunar_forge.permissions import ApprovalCallback
from lunar_forge.project_detection import detect_project
from lunar_forge.runtime.sessions import SessionLogger, create_session_logger
from lunar_forge.tools.files import IGNORED_DIRECTORIES, safe_path
from lunar_forge.tools.registry import ToolRegistry, create_tool_registry


TemplateName = str

SUPPORTED_TEMPLATES = ("static_html", "python_tkinter", "vite_react")
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"
TEMPLATE_FILES: dict[str, tuple[str, ...]] = {
    "static_html": ("index.html", "styles.css", "README.md"),
    "python_tkinter": ("app.py", "README.md"),
    "vite_react": (),
}
NEAR_EMPTY_ENTRIES = frozenset({"AGENTS.md", "CLAUDE.md", ".gitignore"})


def select_template(prompt: str) -> TemplateName:
    """Choose one of the three starter templates with explicit heuristics."""
    normalized = " ".join(prompt.lower().split())
    if "vite" in normalized or "react" in normalized:
        return "vite_react"
    if any(
        phrase in normalized
        for phrase in (
            "calculator",
            "desktop",
            "tkinter",
            "python ui",
            "python gui",
        )
    ):
        return "python_tkinter"
    return "static_html"


def build_new_project_plan(template: TemplateName) -> list[str]:
    """Return a user-facing plan without touching the target project."""
    _validate_template(template)
    if template == "static_html":
        return [
            "Create index.html with a small semantic landing page.",
            "Create styles.css with responsive, dependency-free styling.",
            "Create README.md with local run instructions.",
        ]
    if template == "python_tkinter":
        return [
            "Create app.py with a standard-library Tkinter calculator.",
            "Create README.md with Python run instructions.",
        ]
    return [
        "Request approval to run npm create vite@latest . -- --template react.",
        "Request separate approval to run npm install; this may require network.",
        "Provide npm run dev instructions after scaffolding.",
    ]


def format_new_project_plan(template: TemplateName) -> str:
    steps = build_new_project_plan(template)
    lines = [f"New-project plan ({template}):"]
    lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    return "\n".join(lines)


def run_new_project(
    prompt: str,
    project_root: str | Path,
    *,
    mode: str = "default",
    approval_callback: ApprovalCallback | None = None,
    template: TemplateName | None = None,
) -> dict[str, Any]:
    """Plan or create a starter project without overwriting existing work."""
    root = Path(project_root).expanduser().resolve()
    selected_template = template or select_template(prompt)
    _validate_template(selected_template)
    plan = build_new_project_plan(selected_template)
    normalized_mode = mode.strip().lower() or "default"
    base_result: dict[str, Any] = {
        "template": selected_template,
        "plan": plan,
        "planned_files": list(TEMPLATE_FILES[selected_template]),
        "changed_files": [],
        "commands_run": [],
        "command_results": [],
        "validation": [],
        "checkpoints": [],
        "run_instructions": _run_instructions(selected_template),
        "session_log": None,
    }

    if not root.is_dir():
        return {
            "ok": False,
            "message": f"Target project directory does not exist: {root}",
            **base_result,
        }
    if not _is_empty_or_nearly_empty(root):
        return {
            "ok": False,
            "message": (
                "Target project is not empty; use the existing-project workflow "
                "instead. No project files were changed."
            ),
            **base_result,
        }
    if normalized_mode == "plan":
        return {
            "ok": True,
            "message": "Plan mode selected a template without writing files.",
            "planned": True,
            **base_result,
        }

    session = None if selected_template == "vite_react" else _start_session(root)
    if session is not None:
        base_result["session_log"] = session.relative_path
    _log(session, "user_prompt", prompt=prompt)
    _log(session, "template_selected", template=selected_template, plan=plan)

    registry = create_tool_registry(
        root,
        mode=normalized_mode,
        approval_callback=approval_callback,
    )
    if selected_template == "vite_react":
        result, session = _scaffold_vite(
            root,
            registry,
            base_result,
            prompt,
            plan,
        )
    else:
        result = _copy_template_files(
            selected_template,
            registry,
            base_result,
            session,
        )

    _log(
        session,
        "assistant_message",
        text=result["message"],
        changed_files=result["changed_files"],
        commands_run=result["commands_run"],
        run_instructions=result["run_instructions"],
    )
    return result


def format_new_project_result(result: dict[str, Any]) -> str:
    """Render the JSON-safe workflow result for terminal output."""
    status = "Done." if result.get("ok") else "Partially done."
    lines = [status, "", f"Template: {result.get('template', 'unknown')}"]
    lines.extend(("", "Plan:"))
    plan = result.get("plan") or []
    lines.extend(f"{index}. {step}" for index, step in enumerate(plan, start=1))
    _append_list(lines, "Changed files", result.get("changed_files"))
    _append_list(lines, "Commands run", result.get("commands_run"))

    lines.extend(("", "Validation:"))
    validation = result.get("validation") or []
    if validation:
        lines.extend(f"- {item}" for item in validation)
    else:
        lines.append("- Not run in this starter workflow.")

    _append_list(lines, "Checkpoints", result.get("checkpoints"))
    _append_list(lines, "Run instructions", result.get("run_instructions"))
    lines.extend(("", "Notes:", f"- {result.get('message', '')}"))
    session_log = result.get("session_log") or "disabled or unavailable"
    lines.append(f"- Session log: {session_log}")
    return "\n".join(lines)


def run(
    root: str | Path,
    prompt: str = "Build a simple website",
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility wrapper retaining the placeholder's root-first call shape."""
    return run_new_project(prompt, root, **kwargs)


def _copy_template_files(
    template: TemplateName,
    registry: ToolRegistry,
    result: dict[str, Any],
    session: SessionLogger | None,
) -> dict[str, Any]:
    for relative_path in TEMPLATE_FILES[template]:
        try:
            source = TEMPLATE_ROOT / template / relative_path
            content = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            result.update(
                ok=False,
                message=f"Could not load template file {relative_path}: {exc}",
            )
            _log(session, "error", source="template", message=str(exc))
            return result

        arguments = {"path": relative_path, "content": content}
        _log(session, "tool_call", name="write_file", arguments=arguments)
        tool_result = registry.execute("write_file", arguments)
        _log(session, "tool_result", name="write_file", result=tool_result)
        if tool_result.get("permission_denied") is True:
            _log(
                session,
                "permission_denial",
                name="write_file",
                reason=tool_result.get("error"),
            )
        if tool_result.get("ok") is not True:
            result.update(
                ok=False,
                message=(
                    f"Stopped before writing {relative_path}: "
                    f"{tool_result.get('error', 'write failed')}"
                ),
            )
            return result
        result["changed_files"].append(str(tool_result["path"]))

    result.update(ok=True, message=f"Created the {template} starter project.")
    return result


def _scaffold_vite(
    root: Path,
    registry: ToolRegistry,
    result: dict[str, Any],
    prompt: str,
    plan: list[str],
) -> tuple[dict[str, Any], SessionLogger | None]:
    before = _project_files(root)
    commands = (
        "npm create vite@latest . -- --template react",
        "npm install",
    )
    session: SessionLogger | None = None
    for index, command in enumerate(commands):
        arguments = {"command": command}
        if session is not None:
            _log(session, "tool_call", name="run_command", arguments=arguments)
        command_result = registry.execute("run_command", arguments)
        if index == 0:
            # create-vite expects an empty directory, so create .agent only after
            # the scaffold attempt and then record the buffered context.
            session = _start_session(root)
            if session is not None:
                result["session_log"] = session.relative_path
            _log(session, "user_prompt", prompt=prompt)
            _log(session, "template_selected", template="vite_react", plan=plan)
            _log(session, "tool_call", name="run_command", arguments=arguments)
        result["command_results"].append(command_result)
        _log(session, "tool_result", name="run_command", result=command_result)
        if command_result.get("permission_denied") is True:
            _log(
                session,
                "permission_denial",
                name="run_command",
                reason=command_result.get("error"),
            )
        else:
            result["commands_run"].append(command)
        if command_result.get("ok") is not True:
            result["changed_files"] = sorted(_project_files(root) - before)
            result.update(
                ok=False,
                message=(
                    f"Stopped before completing Vite setup: "
                    f"{command_result.get('error', 'command failed')}"
                ),
            )
            return result, session

    result["changed_files"] = sorted(_project_files(root) - before)
    result.update(
        ok=True,
        message=(
            "Created the Vite React starter. Network availability was not "
            "assumed; both commands ran only after approval."
        ),
    )
    return result, session


def _is_empty_or_nearly_empty(root: Path) -> bool:
    if detect_project(root)["is_empty"]:
        return True
    for entry in root.iterdir():
        if entry.name in IGNORED_DIRECTORIES or entry.name in NEAR_EMPTY_ENTRIES:
            continue
        try:
            safe_path(root, entry)
        except PermissionError:
            return False
        return False
    return True


def _project_files(root: Path) -> set[str]:
    files: set[str] = set()
    for current_directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
        directory_names[:] = [
            name for name in directory_names if name not in IGNORED_DIRECTORIES
        ]
        current = Path(current_directory)
        for file_name in file_names:
            candidate = current / file_name
            try:
                resolved = safe_path(root, candidate)
            except PermissionError:
                continue
            if resolved.is_file():
                files.add(resolved.relative_to(root).as_posix())
    return files


def _run_instructions(template: TemplateName) -> list[str]:
    if template == "static_html":
        return [
            "Open index.html in a browser, or run python -m http.server 8000.",
            "Visit http://localhost:8000.",
        ]
    if template == "python_tkinter":
        return ["Run python app.py from the project directory."]
    return [
        "Run npm run dev from the project directory.",
        "Open the local URL printed by Vite.",
    ]


def _validate_template(template: TemplateName) -> None:
    if template not in SUPPORTED_TEMPLATES:
        raise ValueError(
            f"Unknown template {template!r}; choose one of {SUPPORTED_TEMPLATES}."
        )


def _start_session(root: Path) -> SessionLogger | None:
    try:
        return create_session_logger(root)
    except Exception:
        return None


def _log(session: SessionLogger | None, event: str, **data: Any) -> None:
    if session is None:
        return
    session.log(event, **data)


def _append_list(lines: list[str], heading: str, values: Any) -> None:
    lines.extend(("", f"{heading}:"))
    items = values or []
    if items:
        lines.extend(f"- {item}" for item in items)
    else:
        lines.append("- None")


__all__ = [
    "SUPPORTED_TEMPLATES",
    "build_new_project_plan",
    "format_new_project_plan",
    "format_new_project_result",
    "run",
    "run_new_project",
    "select_template",
]
