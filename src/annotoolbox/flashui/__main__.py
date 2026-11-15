"""``python -m <package>.flashui``: the Flash UI editor's commands on their own (``uicommands``)."""

from __future__ import annotations

import argparse
import sys

from . import uicommands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flashui", description="Anno 2205's Flash UI: look, edit, build a mod")
    uicommands.register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
