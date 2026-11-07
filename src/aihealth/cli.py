"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aihealth import __version__
from aihealth.config import ConfigError, load_config
from aihealth.scoring import band


def _not_yet(milestone: str):
    def run(_args: argparse.Namespace) -> int:
        print(f"not implemented yet (planned in {milestone})", file=sys.stderr)
        return 2

    return run


# --- scan -------------------------------------------------------------------


def _fmt_score(score: float | None) -> str:
    return "n/a" if score is None else f"{score:5.2f} {band(score)}"


def _print_scan_text(result, top: int, verbose: bool) -> None:
    files = sorted(result.files, key=lambda f: (f.score, -f.loc))
    print(f"aihealth scan {result.root}")
    print(
        f"project score: {_fmt_score(result.score)}  "
        f"({len(result.files)} files, {result.loc} LoC, LoC-weighted)"
    )
    bands = {"healthy": 0, "warning": 0, "alert": 0}
    for f in result.files:
        bands[f.band] += 1
    print(f"files: {bands['healthy']} healthy, {bands['warning']} warning, {bands['alert']} alert")
    worst = [f for f in files if f.findings][:top]
    if not worst:
        print("\nno code smells found")
        return
    print(f"\nworst {len(worst)} files:")
    for f in worst:
        print(f"\n  {f.score:5.2f}  {f.path}  ({f.loc} LoC)")
        findings = sorted(f.findings, key=lambda x: -x.penalty)
        for fd in findings if verbose else findings[:5]:
            print(f"         L{fd.line:<5} {fd.label}: {fd.message}")
        if not verbose and len(findings) > 5:
            print(f"         ... {len(findings) - 5} more (use -v)")


def _cmd_scan(args: argparse.Namespace) -> int:
    from aihealth.engine import scan

    root = Path(args.path)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    try:
        config = load_config(root, args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    result = scan(root, config)
    if args.format == "json":
        json.dump(result.to_dict(with_functions=args.functions), sys.stdout, indent=2)
        print()
    else:
        _print_scan_text(result, args.top, args.verbose)
    if args.fail_under is not None and result.score is not None and result.score < args.fail_under:
        return 1
    return 0


# --- delta --------------------------------------------------------------------

_MARK = {
    "new": "+ new     ",
    "worsened": "↑ worse   ",
    "improved": "↓ better  ",
    "fixed": "✓ fixed   ",
}


_HEAD_LABEL = {"worktree": "working tree", "index": "staged changes"}


def _print_delta_text(result, verbose: bool) -> None:
    head = _HEAD_LABEL.get(result.head, result.head)
    base = result.base or "(empty repository)"
    print(f"aihealth delta: {head} vs {base}: {result.verdict.upper()}")
    if not result.files:
        print("no changed Python/Dart files")
    for fd in result.files:
        _print_file_delta(fd, verbose)


def _score_label(fd) -> str:
    if fd.score_before is None:
        return f"new file, {fd.score_after:.2f}"
    return f"{fd.score_before:.2f} → {fd.score_after:.2f} ({fd.score_delta:+.2f})"


def _print_file_delta(fd, verbose: bool) -> None:
    rename = f" (from {fd.old_path})" if fd.status == "renamed" else ""
    print(f"\n  {fd.verdict:9} {fd.path}{rename}  {_score_label(fd)}")
    if fd.after and fd.after.parse_errors:
        print(f"            ! {fd.after.parse_errors} syntax error(s): results may be partial")
    shown = fd.changes if verbose else fd.of_kind("new", "worsened")
    for ch in shown:
        _print_change(ch)
    hidden = len(fd.changes) - len(shown)
    if hidden:
        print(f"    ({hidden} improvement(s), -v to list)")


def _print_change(ch) -> None:
    f = ch.finding
    was = f" (was {ch.value_before:g})" if ch.kind in ("worsened", "improved") else ""
    line = "" if ch.kind == "fixed" else f"L{f.line}"
    print(f"    {_MARK[ch.kind]}{line:6} {f.label}: {f.message}{was}")
    if ch.kind in ("new", "worsened"):
        print(f"                     → {f.hint}")


def _cmd_delta(args: argparse.Namespace) -> int:
    from aihealth import gitutil
    from aihealth.delta import DEGRADED, run_delta

    if args.staged and args.head:
        print("error: --staged and --head are mutually exclusive", file=sys.stderr)
        return 2
    head = gitutil.INDEX if args.staged else (args.head or gitutil.WORKTREE)
    try:
        root = gitutil.repo_root(args.repo)
        config = load_config(root, args.config)
        result = run_delta(root, config, gitutil.Revisions(args.base, head), args.paths or None)
    except (gitutil.GitError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.format == "json":
        json.dump(result.to_dict(), sys.stdout, indent=2)
        print()
    else:
        _print_delta_text(result, args.verbose)
    return 1 if result.verdict == DEGRADED else 0


# --- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aihealth",
        description="Code health estimates (1-10) for Python and Dart code.",
    )
    parser.add_argument("--version", action="version", version=f"aihealth {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="score every supported file under a path")
    scan.add_argument("path", nargs="?", default=".")
    scan.add_argument("--format", choices=["text", "json"], default="text")
    scan.add_argument("--top", type=int, default=10, help="files to list in text output")
    scan.add_argument("-v", "--verbose", action="store_true", help="list every finding")
    scan.add_argument("--functions", action="store_true", help="include function metrics (json)")
    scan.add_argument("--config", help="config file (default: <path>/.aihealth.toml)")
    scan.add_argument(
        "--fail-under", type=float, metavar="SCORE", help="exit 1 if project score is below"
    )
    scan.set_defaults(func=_cmd_scan)

    delta = sub.add_parser(
        "delta",
        help="score a code change before vs after (exit 1 if health degraded)",
        description="Compare code health of changed files. Default: working tree "
        "(incl. untracked files) against HEAD.",
    )
    delta.add_argument("paths", nargs="*", help="limit to these files/directories")
    delta.add_argument("--repo", default=".", help="path inside the git repository")
    delta.add_argument("--staged", action="store_true", help="compare the index against base")
    delta.add_argument("--base", metavar="REV", default="HEAD", help="before revision (HEAD)")
    delta.add_argument("--head", metavar="REV", help="after revision instead of the working tree")
    delta.add_argument("--format", choices=["text", "json"], default="text")
    delta.add_argument("-v", "--verbose", action="store_true", help="also list improvements")
    delta.add_argument("--config", help="config file (default: <repo root>/.aihealth.toml)")
    delta.set_defaults(func=_cmd_delta)

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
