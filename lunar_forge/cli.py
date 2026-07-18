"""Command-line entry point for LunarForge."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from lunar_forge.agent import run_agent
from lunar_forge.config import load_config


app = typer.Typer(
    add_completion=False,
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
) -> None:
    """Accept a coding task for a target project."""
    project_root = project.expanduser().resolve()
    cli_overrides = {"permissions": {"mode": "plan"}} if plan else None

    try:
        config = load_config(project_root, cli_overrides=cli_overrides)
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


def main() -> None:
    """Run the Typer application."""
    app()


if __name__ == "__main__":
    main()
