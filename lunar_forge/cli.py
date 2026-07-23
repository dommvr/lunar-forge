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
from lunar_forge.mcp.client import build_mcp_diagnostic
from lunar_forge.plugins.registry import build_plugin_diagnostic
from lunar_forge.runtime.checkpoints import (
    list_checkpoint_directories,
    rollback_file,
)
from lunar_forge.runtime.git import (
    create_git_commit,
    format_git_commit_result,
    format_git_status,
    git_status,
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
    BROWSER_SETUP_COMMANDS,
    DEFAULT_SERVER_STARTUP_TIMEOUT_MS,
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
    run_browser_setup,
    run_browser_validation,
    run_managed_browser_validation,
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
            "browser-setup",
            "browser-validate",
            "git",
            "mcp",
            "plugins",
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
mcp_app = typer.Typer(
    add_completion=False,
    help="Inspect explicitly configured local MCP servers.",
)
app.add_typer(mcp_app, name="mcp")
plugins_app = typer.Typer(
    add_completion=False,
    help="Inspect explicitly configured local plugins without loading code.",
)
app.add_typer(plugins_app, name="plugins")
git_app = typer.Typer(
    add_completion=False,
    help="Inspect status or create an explicitly approved Git commit.",
)
app.add_typer(git_app, name="git")


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
    parallel_subagents: Annotated[
        bool,
        typer.Option(
            "--parallel-subagents",
            help="Enable safe concurrent read-only specialist phases.",
        ),
    ] = False,
    commit: Annotated[
        bool,
        typer.Option(
            "--commit",
            help="Offer an approved Git commit after successful work.",
        ),
    ] = False,
    commit_message: Annotated[
        str | None,
        typer.Option(
            "--commit-message",
            help="Commit message used with --commit; otherwise derived from the task.",
        ),
    ] = None,
) -> None:
    """Accept a coding task for a target project."""
    project_root = project.expanduser().resolve()
    cli_overrides = _runtime_overrides(
        plan,
        docker,
        allow_network,
        subagents,
        parallel_subagents,
    )

    try:
        if commit_message is not None and not commit:
            raise ValueError("--commit-message requires --commit.")
        config = load_config(project_root, cli_overrides=cli_overrides)
        _validate_network_flag(allow_network, config.runtime.mode)
        response = run_agent(
            prompt,
            project_root,
            config=config,
            mode=config.permissions.mode,
            offer_commit=commit,
            commit_message=commit_message,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(response)


@git_app.command("status")
def git_status_command(
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
) -> None:
    """Show bounded repository-wide ``git status --short`` output."""
    project_root = project.expanduser().resolve()
    try:
        config = load_config(project_root)
        result = git_status(
            project_root,
            mode=_git_execution_mode(
                config.permissions.mode,
                config.runtime.mode,
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(format_git_status(result))
    if result.get("ok") is not True:
        raise typer.Exit(code=1)


@git_app.command("commit")
def git_commit_command(
    message: Annotated[
        str,
        typer.Option("--message", "-m", help="Concise Git commit message."),
    ],
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
) -> None:
    """Preview and create one explicitly approved path-limited commit."""
    project_root = project.expanduser().resolve()
    try:
        config = load_config(project_root)
        result = create_git_commit(
            project_root,
            message,
            mode=_git_execution_mode(
                config.permissions.mode,
                config.runtime.mode,
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(format_git_commit_result(result))
    if result.get("ok") is not True:
        raise typer.Exit(code=1)


@app.command("browser-setup")
def browser_setup_command(
    project: Annotated[
        Path,
        typer.Option(
            "--project",
            "-p",
            help="LunarForge checkout where browser support will be installed.",
        ),
    ] = Path("."),
) -> None:
    """Install optional browser dependencies after explicit approvals."""
    typer.echo("Browser setup will run these commands:")
    for command in BROWSER_SETUP_COMMANDS:
        typer.echo(f"- {command}")

    project_root = project.expanduser().resolve()
    try:
        config = load_config(project_root)
        result = run_browser_setup(
            project_root,
            permission_mode=config.permissions.mode,
            runtime_mode=config.runtime.mode,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Browser setup result:")
    typer.echo(
        json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    )
    if result.get("ok") is not True:
        raise typer.Exit(code=1)


@app.command("browser-validate")
def browser_validate_command(
    url: Annotated[
        str | None,
        typer.Argument(help="Already-running local loopback HTTP(S) URL."),
    ] = None,
    managed_url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help="Loopback URL used with --serve, or instead of the argument.",
        ),
    ] = None,
    serve: Annotated[
        str | None,
        typer.Option(
            "--serve",
            help="Approved project-local dev server command to manage.",
        ),
    ] = None,
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
    startup_timeout_ms: Annotated[
        int,
        typer.Option(
            "--startup-timeout-ms",
            help="Maximum time to wait for a managed server URL.",
        ),
    ] = DEFAULT_SERVER_STARTUP_TIMEOUT_MS,
    checks: Annotated[
        list[str] | None,
        typer.Option(
            "--check",
            help="CSS selector expected to match; repeat for multiple checks.",
        ),
    ] = None,
) -> None:
    """Validate a local page directly or with an approved managed dev server."""
    if url is not None and managed_url is not None:
        typer.echo("Error: Provide the URL as an argument or --url, not both.", err=True)
        raise typer.Exit(code=1)
    selected_url = managed_url or url
    if selected_url is None:
        typer.echo("Error: A loopback URL is required.", err=True)
        raise typer.Exit(code=1)
    project_root = project.expanduser().resolve()
    if serve is None:
        result = run_browser_validation(
            selected_url,
            screenshot=screenshot,
            checks=checks,
            full_page=full_page,
            width=width,
            height=height,
            project_root=project_root,
        )
    else:
        result = run_managed_browser_validation(
            serve,
            selected_url,
            screenshot=screenshot,
            checks=checks,
            full_page=full_page,
            width=width,
            height=height,
            startup_timeout_ms=startup_timeout_ms,
            project_root=project_root,
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


@mcp_app.command("list")
def mcp_list_command(
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
) -> None:
    """Start enabled MCP servers and report bounded tool discovery details."""
    project_root = project.expanduser().resolve()
    try:
        config = load_config(project_root)
        result = build_mcp_diagnostic(
            project_root,
            globally_enabled=config.mcp.enabled,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    )
    if result.get("ok") is not True:
        raise typer.Exit(code=1)


@plugins_app.command("list")
def plugins_list_command(
    project: Annotated[
        Path,
        typer.Option("--project", "-p", help="Target project directory."),
    ] = Path("."),
) -> None:
    """Report plugin config and manifest tools without importing plugin code."""
    project_root = project.expanduser().resolve()
    try:
        config = load_config(project_root)
        result = build_plugin_diagnostic(
            project_root,
            globally_enabled=config.plugins.enabled,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    )
    if result.get("ok") is not True:
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
    parallel_subagents: Annotated[
        bool,
        typer.Option(
            "--parallel-subagents",
            help="Run safe testing and review phases concurrently.",
        ),
    ] = False,
) -> None:
    """Create a small starter project from a built-in template."""
    project_root = project.expanduser().resolve()
    cli_overrides = _runtime_overrides(
        plan,
        docker,
        allow_network,
        subagents,
        parallel_subagents,
    )
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
            subagents_parallel=config.subagents.parallel,
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
    parallel_subagents: Annotated[
        bool,
        typer.Option(
            "--parallel-subagents",
            help="Continue with safe concurrent read-only specialist phases.",
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
            parallel_subagents,
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
    parallel_subagents: bool = False,
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
    if subagents or parallel_subagents:
        subagent_overrides: dict[str, object] = {"enabled": True}
        if parallel_subagents:
            subagent_overrides["parallel"] = True
        overrides["subagents"] = subagent_overrides
    return overrides or None


def _validate_network_flag(allow_network: bool, runtime_mode: str) -> None:
    if allow_network and runtime_mode != "docker":
        raise ValueError("--allow-network requires Docker runtime mode.")


def _git_execution_mode(permission_mode: str, runtime_mode: str) -> str:
    if runtime_mode.strip().lower() == "no-command":
        return "no-command"
    return permission_mode.strip().lower() or "default"


def main() -> None:
    """Run the Typer application."""
    app()


if __name__ == "__main__":
    main()
