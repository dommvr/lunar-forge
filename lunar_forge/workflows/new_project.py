"""Small, permission-gated starter workflows for empty projects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lunar_forge.permissions import ApprovalCallback
from lunar_forge.project_detection import detect_project
from lunar_forge.runtime.sessions import SessionLogger, create_session_logger
from lunar_forge.tools.files import IGNORED_DIRECTORIES, safe_path
from lunar_forge.tools.registry import ToolRegistry, create_tool_registry


TemplateName = str


@dataclass(frozen=True)
class TemplateSpec:
    """Declarative files and commands for one built-in starter template."""

    name: TemplateName
    files: tuple[str, ...]
    commands: tuple[str, ...]
    validation: tuple[str, ...]
    dependencies: tuple[str, ...]
    run_instructions: tuple[str, ...]


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"
TEMPLATE_SPECS: dict[TemplateName, TemplateSpec] = {
    "static_html": TemplateSpec(
        name="static_html",
        files=("index.html", "styles.css", "README.md"),
        commands=(),
        validation=(),
        dependencies=(),
        run_instructions=(
            "Open index.html in a browser, or run python -m http.server 8000.",
            "Visit http://localhost:8000.",
        ),
    ),
    "python_tkinter": TemplateSpec(
        name="python_tkinter",
        files=("app.py", "README.md"),
        commands=(),
        validation=(),
        dependencies=(),
        run_instructions=("Run python app.py from the project directory.",),
    ),
    "vite_react": TemplateSpec(
        name="vite_react",
        files=(
            "index.html",
            "package.json",
            "vite.config.js",
            "src/main.jsx",
            "src/App.jsx",
            "src/App.css",
            "README.md",
        ),
        commands=("npm install",),
        validation=("npm run build",),
        dependencies=("react", "react-dom", "vite", "@vitejs/plugin-react"),
        run_instructions=(
            "Run npm install from the project directory.",
            "Run npm run dev from the project directory.",
            "Open the local URL printed by Vite.",
            "Run npm run build to create a production build.",
            "Run npm run preview to preview the production build.",
        ),
    ),
    "python_cli": TemplateSpec(
        name="python_cli",
        files=("app.py", "test_app.py", "README.md"),
        commands=(),
        validation=("python -m unittest -q",),
        dependencies=(),
        run_instructions=(
            "Run python app.py --name Ada from the project directory.",
            "Run python -m unittest -q to execute the starter test.",
        ),
    ),
    "flask": TemplateSpec(
        name="flask",
        files=("app.py", "test_app.py", "requirements.txt", "README.md"),
        commands=("python -m pip install -r requirements.txt",),
        validation=("python -m unittest -q",),
        dependencies=("Flask>=3.0,<4.0",),
        run_instructions=(
            "Install dependencies with python -m pip install -r requirements.txt.",
            "Run flask --app app run --debug from the project directory.",
            "Visit http://127.0.0.1:5000.",
        ),
    ),
    "fastapi": TemplateSpec(
        name="fastapi",
        files=("app.py", "test_app.py", "requirements.txt", "README.md"),
        commands=("python -m pip install -r requirements.txt",),
        validation=("python -m unittest -q",),
        dependencies=(
            "fastapi>=0.110,<1.0",
            "uvicorn>=0.29,<1.0",
        ),
        run_instructions=(
            "Install dependencies with python -m pip install -r requirements.txt.",
            "Run uvicorn app:app --reload from the project directory.",
            "Visit http://127.0.0.1:8000/docs.",
        ),
    ),
}
SUPPORTED_TEMPLATES = tuple(TEMPLATE_SPECS)
TEMPLATE_FILES: dict[str, tuple[str, ...]] = {
    name: spec.files for name, spec in TEMPLATE_SPECS.items()
}
NEAR_EMPTY_ENTRIES = frozenset({"AGENTS.md", "CLAUDE.md", ".gitignore"})


def select_template(prompt: str) -> TemplateName:
    """Choose a starter template with small, explicit prompt heuristics."""
    normalized = " ".join(prompt.lower().split())
    if "vite" in normalized or "react" in normalized:
        return "vite_react"
    if "fastapi" in normalized or "fast api" in normalized:
        return "fastapi"
    if "flask" in normalized:
        return "flask"
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
    if any(
        phrase in normalized
        for phrase in (
            "python cli",
            "command-line",
            "command line",
            "console app",
            "terminal app",
        )
    ):
        return "python_cli"
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
    if template == "vite_react":
        return [
            "Create a small React app, Vite entry point, and responsive styles.",
            "Create package.json with dev, build, and preview scripts.",
            "Create README.md with install, development, and build instructions.",
            "Request approval to run npm install; this may require network.",
            "Request separate approval to validate with npm run build.",
        ]
    if template == "python_cli":
        return [
            "Create app.py with a small argparse-based command-line application.",
            "Create test_app.py with a standard-library unittest.",
            "Create README.md with run and test instructions.",
            "Request approval to run python -m unittest -q.",
        ]
    if template == "flask":
        framework = "Flask"
        run_command = "flask --app app run --debug"
    else:
        framework = "FastAPI"
        run_command = "uvicorn app:app --reload"
    return [
        f"Create a small {framework} app and standard-library test.",
        "Create requirements.txt and README.md.",
        "Request approval before installing dependencies.",
        "Request approval to run python -m unittest -q.",
        f"Provide {run_command} instructions.",
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
    runtime_mode: str = "local",
    allow_network: bool = False,
) -> dict[str, Any]:
    """Plan or create a starter project without overwriting existing work."""
    root = Path(project_root).expanduser().resolve()
    selected_template = template or select_template(prompt)
    _validate_template(selected_template)
    spec = TEMPLATE_SPECS[selected_template]
    plan = build_new_project_plan(selected_template)
    normalized_mode = mode.strip().lower() or "default"
    base_result: dict[str, Any] = {
        "template": selected_template,
        "plan": plan,
        "planned_files": list(spec.files),
        "planned_commands": list(spec.commands),
        "validation_commands": list(spec.validation),
        "dependencies": list(spec.dependencies),
        "changed_files": [],
        "commands_run": [],
        "command_results": [],
        "validation": [],
        "checkpoints": [],
        "run_instructions": list(spec.run_instructions),
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

    session = _start_session(root)
    if session is not None:
        base_result["session_log"] = session.relative_path
    _log(session, "user_prompt", prompt=prompt)
    _log(session, "template_selected", template=selected_template, plan=plan)

    registry = create_tool_registry(
        root,
        mode=normalized_mode,
        approval_callback=approval_callback,
        runtime_mode=runtime_mode,
        allow_network=allow_network,
    )
    result = _copy_template_files(
        spec,
        registry,
        base_result,
        session,
    )
    if result.get("ok") is True:
        result = _run_declared_commands(
            spec,
            registry,
            result,
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
    spec: TemplateSpec,
    registry: ToolRegistry,
    result: dict[str, Any],
    session: SessionLogger | None,
) -> dict[str, Any]:
    for relative_path in spec.files:
        try:
            source = TEMPLATE_ROOT / spec.name / relative_path
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

    result.update(ok=True, message=f"Created the {spec.name} starter project.")
    return result


def _run_declared_commands(
    spec: TemplateSpec,
    registry: ToolRegistry,
    result: dict[str, Any],
    session: SessionLogger | None,
) -> dict[str, Any]:
    for command in spec.commands:
        command_result = _execute_command(command, registry, result, session)
        if command_result.get("ok") is not True:
            result.update(
                ok=False,
                message=(
                    f"Created the {spec.name} files, but stopped before completing "
                    f"setup: {command_result.get('error', 'command failed')}"
                ),
            )
            return result

    for command in spec.validation:
        command_result = _execute_command(command, registry, result, session)
        if command_result.get("ok") is True:
            result["validation"].append(f"{command} passed")
            continue
        result["validation"].append(
            f"{command} failed: {command_result.get('error', 'command failed')}"
        )
        result.update(
            ok=False,
            message=(
                f"Created the {spec.name} starter, but validation did not pass."
            ),
        )
        return result

    result.update(ok=True, message=f"Created the {spec.name} starter project.")
    return result


def _execute_command(
    command: str,
    registry: ToolRegistry,
    result: dict[str, Any],
    session: SessionLogger | None,
) -> dict[str, Any]:
    arguments = {"command": command}
    _log(session, "tool_call", name="run_command", arguments=arguments)
    command_result = registry.execute("run_command", arguments)
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
    return command_result


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
    "TEMPLATE_FILES",
    "TEMPLATE_SPECS",
    "TemplateSpec",
    "build_new_project_plan",
    "format_new_project_plan",
    "format_new_project_result",
    "run",
    "run_new_project",
    "select_template",
]
