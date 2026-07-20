"""A small command-line application with no third-party dependencies."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def greeting(name: str) -> str:
    """Return the message printed by the CLI."""
    return f"Hello, {name}!"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print a friendly greeting.")
    parser.add_argument("--name", default="world", help="Name to greet.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(greeting(args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
