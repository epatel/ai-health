"""Command line entry point. Subcommands are stubs until their milestones land."""

from __future__ import annotations

import argparse
import sys

from aihealth import __version__


def _not_yet(milestone: str):
    def run(_args: argparse.Namespace) -> int:
        print(f"not implemented yet (planned in {milestone})", file=sys.stderr)
        return 2

    return run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aihealth",
        description="Code health estimates (1-10) for Python and Dart code.",
    )
    parser.add_argument("--version", action="version", version=f"aihealth {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="score every supported file under a path")
    scan.add_argument("path", nargs="?", default=".")
    scan.set_defaults(func=_not_yet("M4"))

    delta = sub.add_parser("delta", help="score a code change before vs after")
    delta.add_argument("--staged", action="store_true", help="compare index against HEAD")
    delta.add_argument("--base", metavar="REV", help="compare working tree against REV")
    delta.set_defaults(func=_not_yet("M5"))

    mcp = sub.add_parser("mcp", help="run the MCP server over stdio")
    mcp.set_defaults(func=_not_yet("M7"))

    serve = sub.add_parser("serve", help="run the web dashboard")
    serve.add_argument("--repo", default=".")
    serve.set_defaults(func=_not_yet("M9"))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
