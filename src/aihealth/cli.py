"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aihealth import __version__
from aihealth.config import ConfigError, load_config
from aihealth.lang import SUPPORTED_LANGUAGES
from aihealth.report import (
    FORMATS,
    ReportOptions,
    render_delta,
    render_file,
    render_history,
    render_scan,
)
from aihealth.store.scans import default_db_path

# --- scan -------------------------------------------------------------------


def _report_options(args: argparse.Namespace) -> ReportOptions:
    return ReportOptions(
        top=getattr(args, "top", 10),
        max_findings=args.max_findings,
        verbose=args.verbose,
        functions=getattr(args, "functions", False),
    )


def _cmd_scan(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.is_file():
        return _scan_file(target, args)
    if target.is_dir():
        return _scan_dir(target, args)
    print(f"no such file or directory: {target}", file=sys.stderr)
    return 2


def _scan_dir(root: Path, args: argparse.Namespace) -> int:
    from aihealth.engine import scan

    try:
        config = load_config(root, args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    result = scan(root, config)
    print(render_scan(result, args.format, _report_options(args)))
    if args.save:
        _save(result, config, args.db or default_db_path(root))
    below = args.fail_under is not None and (result.score or 10.0) < args.fail_under
    return 1 if below else 0


def _scan_file(path: Path, args: argparse.Namespace) -> int:
    """Score one file on its own (duplication within the file only), with
    the config of the project it belongs to."""
    from aihealth.engine import project_path, score_source
    from aihealth.lang import is_supported_file
    from aihealth.lang.base import is_generated

    if args.save:
        print("error: --save needs a directory (one file is not a project scan)", file=sys.stderr)
        return 2
    root, rel = project_path(path)
    if not is_supported_file(path):
        print(
            f"unsupported file type: {rel} (supported: {SUPPORTED_LANGUAGES})",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(root, args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    source = path.read_bytes()
    if is_generated(source):
        print(f"skipped {rel}: generated file", file=sys.stderr)
        return 0
    fs = score_source(source, rel, config)
    print(render_file(fs, args.format, _report_options(args)))
    return 1 if args.fail_under is not None and fs.score < args.fail_under else 0


def _save(result, config, db: Path) -> None:
    from aihealth.store import connect, save_scan

    conn = connect(db)
    try:
        scan_id = save_scan(conn, result, config)
    finally:
        conn.close()
    print(f"saved scan #{scan_id} to {db}", file=sys.stderr)


# --- history ------------------------------------------------------------------


def _cmd_history(args: argparse.Namespace) -> int:
    from aihealth.store import SchemaTooNewError, connect, list_scans

    db = Path(args.db) if args.db else default_db_path(args.path)
    if not db.is_file():
        print(f"no scan history at {db} (run: aihealth scan --save)", file=sys.stderr)
        return 2
    try:
        conn = connect(db)
    except SchemaTooNewError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        scans = list(reversed(list_scans(conn, args.limit)))  # oldest first
    finally:
        conn.close()
    print(render_history(scans, args.format, str(db)))
    return 0


# --- delta --------------------------------------------------------------------


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
    print(render_delta(result, args.format, _report_options(args)))
    return 1 if result.verdict == DEGRADED else 0


# --- mcp ----------------------------------------------------------------------


def _cmd_mcp(_args: argparse.Namespace) -> int:
    try:
        from aihealth.mcp_server import main as serve
    except ImportError as e:  # pragma: no cover - depends on install
        print(f"MCP support not installed ({e}); run: pip install mcp", file=sys.stderr)
        return 2
    serve()
    return 0


# --- serve --------------------------------------------------------------------


def _cmd_serve(args: argparse.Namespace) -> int:
    from aihealth.web.app import LOOPBACK, serve

    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"not a directory: {repo}", file=sys.stderr)
        return 2
    print(
        f"aihealth dashboard for {repo.resolve()} on http://{args.host}:{args.port}",
        file=sys.stderr,
    )
    if args.host not in LOOPBACK:
        print(
            f"warning: {args.host} is reachable from other machines, and the dashboard has no "
            "authentication: anyone who can connect can browse the findings and start scans",
            file=sys.stderr,
        )
    serve(repo, args.db, args.host, args.port)
    return 0


# --- hook ---------------------------------------------------------------------


def _cmd_hook(args: argparse.Namespace) -> int:
    from aihealth.hooks import run

    out = run(args.event, sys.stdin.read())
    if out:
        print(out)
    return 0  # fail open: hooks never block editing on their own errors


# --- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aihealth",
        description=f"Code health estimates (1-10) for {SUPPORTED_LANGUAGES}.",
    )
    parser.add_argument("--version", action="version", version=f"aihealth {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="score a directory tree, or a single file")
    scan.add_argument("path", nargs="?", default=".", help="directory or file (default: .)")
    scan.add_argument("--format", choices=FORMATS, default="text")
    scan.add_argument("--max-findings", type=int, default=10, help="markdown: findings listed")
    scan.add_argument("--top", type=int, default=10, help="files to list in text output")
    scan.add_argument("-v", "--verbose", action="store_true", help="list every finding")
    scan.add_argument("--functions", action="store_true", help="include function metrics (json)")
    scan.add_argument("--config", help="config file (default: <path>/.aihealth.toml)")
    scan.add_argument(
        "--fail-under",
        type=float,
        metavar="SCORE",
        help="exit 1 if the (project/file) score is below",
    )
    scan.add_argument("--save", action="store_true", help="store the scan in the history DB")
    scan.add_argument("--db", help="history DB (default: <path>/.aihealth/health.db)")
    scan.set_defaults(func=_cmd_scan)

    history = sub.add_parser("history", help="list saved scans with score changes")
    history.add_argument("path", nargs="?", default=".")
    history.add_argument("--db", help="history DB (default: <path>/.aihealth/health.db)")
    history.add_argument("--limit", type=int, default=30)
    history.add_argument("--format", choices=("text", "json"), default="text")
    history.set_defaults(func=_cmd_history)

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
    delta.add_argument("--format", choices=FORMATS, default="text")
    delta.add_argument("--max-findings", type=int, default=10, help="markdown: findings listed")
    delta.add_argument("-v", "--verbose", action="store_true", help="also list improvements")
    delta.add_argument("--config", help="config file (default: <repo root>/.aihealth.toml)")
    delta.set_defaults(func=_cmd_delta)

    mcp = sub.add_parser("mcp", help="run the MCP server over stdio")
    mcp.set_defaults(func=_cmd_mcp)

    serve = sub.add_parser("serve", help="run the web dashboard")
    serve.add_argument("--repo", default=".", help="repository to show and scan")
    serve.add_argument("--db", help="history DB (default: <repo>/.aihealth/health.db)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=_cmd_serve)

    hook = sub.add_parser(
        "hook",
        help="Claude Code hook: read hook JSON on stdin, report regressions",
        description="post-edit: PostToolUse on Edit|Write (edited file only). "
        "stop: Stop hook (all uncommitted changes, fires at most once per stop).",
    )
    hook.add_argument("event", choices=("post-edit", "stop"))
    hook.set_defaults(func=_cmd_hook)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
