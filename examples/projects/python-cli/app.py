"""A small command-line application with no third-party dependencies."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def greeting(name: str, excited: bool = False) -> str:
    """Return the message printed by the CLI."""
    punctuation = "!" if excited else "."
    return f"Hello, {name}{punctuation}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print a friendly greeting.")
    parser.add_argument("--name", default="world", help="Name to greet.")
    parser.add_argument(
        "--excited",
        action="store_true",
        help="End the greeting with an exclamation mark.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(greeting(args.name, excited=args.excited))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
