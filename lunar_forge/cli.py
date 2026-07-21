"""Command-line entry point for LunarForge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import click
import typer
from typer.core import TyperGroup

from lunar_forge.agent import run_agent
from lunar_forge.config import load_config
from lunar_forge.runtime.checkpoints import (
    list_checkpoint_directories,
    rollback_file,
)
from lunar_forge.runtime.sessions import (
    format_session_summary,
    list_session_files,
    load_session,
)
from lunar_forge.workflows.new_project import (
    format_new_project_plan,
    format_new_project_result,
    run_new_project,
    select_template,
)
from lunar_forge.workflows.browser_validation import (
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
    run_browser_validation,
)


class DefaultCommandGroup(TyperGroup):
    """Route legacy root invocations to ``run`` while supporting subcommands."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        commands = {
            "run",
            "new",
            "checkpoints",
            "rollback",
            "sessions",
            "resume",
            "browser-validate",
            "--help",
            "-h",
        }
        if args and args[0] not in commands:
            args = ["run", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    add_completion=False,
    cls=DefaultCommandGroup,
    help="A small, local coding-agent CLI.",
)


@app.command()
def run(
    prompt: Annotated[str, typer.Argument(help="Task for LunarForge to perform.")],
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
    plan: Annotated[
        bool,
        typer.Option("--plan", help="Inspect and plan without making changes."),
    ] = False,
    docker: Annotated[
        bool,
        typer.Option("--docker", help="Run approved commands in Docker."),
    ] = False,
    allow_network: Annotated[
        bool,
        typer.Option(
            "--allow-network",
            help="Use Docker bridge networking instead of network isolation.",
        ),
    ] = False,
    subagents: Annotated[
        bool,
        typer.Option(
            "--subagents",
            help="Use the deterministic specialist subagent workflow.",
        ),
    ] = False,
) -> None:
    """Accept a coding task for a target project."""
    project_root = project.expanduser().resolve()
    cli_overrides = _runtime_overrides(plan, docker, allow_network, subagents)

    try:
        config = load_config(project_root, cli_overrides=cli_overrides)
        _validate_network_flag(allow_network, config.runtime.mode)
        response = run_agent(
            prompt,
            project_root,
            config=config,
            mode=config.permissions.mode,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(response)


@app.command("browser-validate")
def browser_validate_command(
    url: Annotated[
        str,
        typer.Argument(help="Already-running local loopback HTTP(S) URL."),
    ],
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
    screenshot: Annotated[
        bool,
        typer.Option(
            "--screenshot/--no-screenshot",
            help="Capture a bounded screenshot.",
        ),
    ] = True,
    full_page: Annotated[
        bool,
        typer.Option(
            "--full-page",
            help="Capture the whole scrollable page instead of the viewport.",
        ),
    ] = False,
    width: Annotated[
        int,
        typer.Option(
            "--width",
            help="Browser viewport width in pixels.",
        ),
    ] = DEFAULT_VIEWPORT_WIDTH,
    height: Annotated[
        int,
        typer.Option(
            "--height",
            help="Browser viewport height in pixels.",
        ),
    ] = DEFAULT_VIEWPORT_HEIGHT,
    checks: Annotated[
        list[str] | None,
        typer.Option(
            "--check",
            help="CSS selector expected to match; repeat for multiple checks.",
        ),
    ] = None,
) -> None:
    """Validate an already-running local page without model or API access."""
    result = run_browser_validation(
        url,
        screenshot=screenshot,
        checks=checks,
        full_page=full_page,
        width=width,
        height=height,
        project_root=project.expanduser().resolve(),
    )
    output = dict(result)
    output.setdefault("status", "passed" if output.get("ok") is True else "failed")
    typer.echo(
        json.dumps(
            output,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    )
    if output.get("ok") is not True:
        raise typer.Exit(code=1)


@app.command("new")
def new_project(
    prompt: Annotated[
        str,
        typer.Argument(help="Description of the new project to create."),
    ],
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Empty target project directory."),
    ] = Path("."),
    plan: Annotated[
        bool,
        typer.Option("--plan", help="Select a template and plan without writing."),
    ] = False,
    docker: Annotated[
        bool,
        typer.Option("--docker", help="Run approved commands in Docker."),
    ] = False,
    allow_network: Annotated[
        bool,
        typer.Option(
            "--allow-network",
            help="Use Docker bridge networking instead of network isolation.",
        ),
    ] = False,
    subagents: Annotated[
        bool,
        typer.Option(
            "--subagents",
            help="Use specialist phases for scaffolding, testing, and review.",
        ),
    ] = False,
) -> None:
    """Create a small starter project from a built-in template."""
    project_root = project.expanduser().resolve()
    cli_overrides = _runtime_overrides(plan, docker, allow_network, subagents)
    template = select_template(prompt)

    try:
        config = load_config(project_root, cli_overrides=cli_overrides)
        _validate_network_flag(allow_network, config.runtime.mode)
        typer.echo(format_new_project_plan(template))
        result = run_new_project(
            prompt,
            project_root,
            mode=config.permissions.mode,
            template=template,
            runtime_mode=config.runtime.mode,
            allow_network=config.runtime.allow_network,
            subagents_enabled=config.subagents.enabled,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo()
    typer.echo(format_new_project_result(result))
    if result.get("ok") is not True:
        raise typer.Exit(code=1)


@app.command("checkpoints")
def checkpoints_command(
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
) -> None:
    """List project checkpoint directories, newest first."""
    result = list_checkpoint_directories(project.expanduser().resolve())
    if result.get("ok") is not True:
        typer.echo(f"Error: {result.get('error', 'Could not list checkpoints.')}", err=True)
        raise typer.Exit(code=1)

    checkpoints = result.get("checkpoints", [])
    if not checkpoints:
        typer.echo("No checkpoints found under .agent/checkpoints.")
        return
    typer.echo("Checkpoints (newest first):")
    for checkpoint in checkpoints:
        if isinstance(checkpoint, dict):
            typer.echo(f"- {checkpoint.get('id')}  {checkpoint.get('path')}")
    if result.get("truncated") is True:
        typer.echo("- ... additional checkpoint directories omitted")


@app.command("rollback")
def rollback_command(
    path: Annotated[
        Path,
        typer.Argument(help="Project-relative file path to restore."),
    ],
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
) -> None:
    """Restore the latest checkpoint for a project file."""
    result = rollback_file(project.expanduser().resolve(), path)
    if result.get("ok") is not True:
        typer.echo(f"Error: {result.get('error', 'Rollback failed.')}", err=True)
        raise typer.Exit(code=1)

    typer.echo(
        f"Restored {result.get('path')} from {result.get('checkpoint_path')}."
    )
    previous_state = result.get("previous_state_checkpoint")
    if previous_state:
        typer.echo(f"Saved the replaced state to {previous_state}.")


@app.command("sessions")
def sessions_command(
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
) -> None:
    """List project session log files without reading their contents."""
    result = list_session_files(project.expanduser().resolve())
    if result.get("ok") is not True:
        typer.echo(f"Error: {result.get('error', 'Could not list sessions.')}", err=True)
        raise typer.Exit(code=1)

    sessions = result.get("sessions", [])
    if not sessions:
        typer.echo("No sessions found under .agent/sessions.")
        return
    typer.echo("Sessions (newest first):")
    for session in sessions:
        if isinstance(session, dict):
            typer.echo(
                f"- {session.get('name')}  {session.get('size')} bytes"
            )
    if result.get("truncated") is True:
        typer.echo("- ... additional session files omitted")


@app.command("resume")
def resume_command(
    session_id_or_file: Annotated[
        str,
        typer.Argument(help="Session ID, filename, or project-local session path."),
    ],
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
    prompt: Annotated[
        str,
        typer.Option(
            "--prompt",
            help="New instruction to continue after loading historical context.",
        ),
    ] = "Continue the previous session safely.",
    summary_only: Annotated[
        bool,
        typer.Option(
            "--summary-only",
            help="Print a redacted summary without loading config or a model.",
        ),
    ] = False,
    plan: Annotated[
        bool,
        typer.Option("--plan", help="Continue in read-only plan mode."),
    ] = False,
    docker: Annotated[
        bool,
        typer.Option("--docker", help="Run approved commands in Docker."),
    ] = False,
    allow_network: Annotated[
        bool,
        typer.Option(
            "--allow-network",
            help="Use Docker bridge networking instead of network isolation.",
        ),
    ] = False,
    subagents: Annotated[
        bool,
        typer.Option(
            "--subagents",
            help="Continue with the deterministic specialist workflow.",
        ),
    ] = False,
) -> None:
    """Safely summarize or continue a previous project session."""
    project_root = project.expanduser().resolve()
    try:
        previous_session = load_session(project_root, session_id_or_file)
        if summary_only:
            typer.echo(format_session_summary(previous_session))
            return

        cli_overrides = _runtime_overrides(
            plan,
            docker,
            allow_network,
            subagents,
        )
        config = load_config(project_root, cli_overrides=cli_overrides)
        _validate_network_flag(allow_network, config.runtime.mode)
        response = run_agent(
            prompt,
            project_root,
            config=config,
            mode=config.permissions.mode,
            resume_messages=previous_session.messages,
            resumed_from=previous_session.relative_path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(response)


def _runtime_overrides(
    plan: bool,
    docker: bool,
    allow_network: bool,
    subagents: bool = False,
) -> dict[str, dict[str, object]] | None:
    overrides: dict[str, dict[str, object]] = {}
    if plan:
        overrides["permissions"] = {"mode": "plan"}
    runtime: dict[str, object] = {}
    if docker:
        runtime["mode"] = "docker"
    if allow_network:
        runtime["allow_network"] = True
    if runtime:
        overrides["runtime"] = runtime
    if subagents:
        overrides["subagents"] = {"enabled": True}
    return overrides or None


def _validate_network_flag(allow_network: bool, runtime_mode: str) -> None:
    if allow_network and runtime_mode != "docker":
        raise ValueError("--allow-network requires Docker runtime mode.")


def main() -> None:
    """Run the Typer application."""
    app()


if __name__ == "__main__":
    main()
