"""Command-line entry point for LunarForge."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import click
import typer
from typer.core import TyperGroup

from lunar_forge.agent import run_agent
from lunar_forge.config import load_config
from lunar_forge.workflows.new_project import (
    format_new_project_plan,
    format_new_project_result,
    run_new_project,
    select_template,
)


class DefaultCommandGroup(TyperGroup):
    """Route legacy root invocations to ``run`` while supporting subcommands."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] not in {"run", "new", "--help", "-h"}:
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
) -> None:
    """Accept a coding task for a target project."""
    project_root = project.expanduser().resolve()
    cli_overrides = _runtime_overrides(plan, docker, allow_network)

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
) -> None:
    """Create a small starter project from a built-in template."""
    project_root = project.expanduser().resolve()
    cli_overrides = _runtime_overrides(plan, docker, allow_network)
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
        )
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo()
    typer.echo(format_new_project_result(result))
    if result.get("ok") is not True:
        raise typer.Exit(code=1)


def _runtime_overrides(
    plan: bool,
    docker: bool,
    allow_network: bool,
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
    return overrides or None


def _validate_network_flag(allow_network: bool, runtime_mode: str) -> None:
    if allow_network and runtime_mode != "docker":
        raise ValueError("--allow-network requires Docker runtime mode.")


def main() -> None:
    """Run the Typer application."""
    app()


if __name__ == "__main__":
    main()
