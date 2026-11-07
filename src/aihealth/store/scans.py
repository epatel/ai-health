"""Persist scans and read them back (history, trends, drill-down)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from aihealth import __version__, gitutil
from aihealth.config import Config
from aihealth.engine import ScanResult
from aihealth.store.migrations import migrate

DB_DIR = ".aihealth"
DB_NAME = "health.db"


def default_db_path(repo: str | Path) -> Path:
    return Path(repo) / DB_DIR / DB_NAME


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the database at ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.name == DB_DIR:
        ignore = path.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# created by aihealth: local scan history, not for git\n*\n")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def config_hash(config: Config) -> str:
    blob = json.dumps(config.data, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


PARSER_PACKAGES = ("tree-sitter", "tree-sitter-language-pack")


def parser_versions() -> str:
    """The installed parser and grammar versions, e.g.
    "tree-sitter 0.26.0, tree-sitter-language-pack 1.25.0"."""
    return ", ".join(f"{name} {_installed(name)}" for name in PARSER_PACKAGES)


def _installed(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


@dataclass
class GitInfo:
    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None


def git_info(root: str | Path) -> GitInfo:
    try:
        top = gitutil.repo_root(root)
    except gitutil.GitError:
        return GitInfo()
    return GitInfo(
        commit=gitutil.head_commit(top),
        branch=gitutil.current_branch(top),
        dirty=gitutil.is_dirty(top),
    )


def save_scan(
    conn: sqlite3.Connection, result: ScanResult, config: Config, git: GitInfo | None = None
) -> int:
    """Store a scan with all file scores, findings and function metrics."""
    git = git or git_info(result.root)
    with conn:  # one transaction: a scan is saved completely or not at all
        cur = conn.execute(
            "INSERT INTO scan (created_at, root, git_commit, git_branch, git_dirty, score, loc,"
            " file_count, tool_version, config_hash, parser_versions)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                result.root,
                git.commit,
                git.branch,
                None if git.dirty is None else int(git.dirty),
                result.score,
                result.loc,
                len(result.files),
                __version__,
                config_hash(config),
                parser_versions(),
            ),
        )
        scan_id = cur.lastrowid
        for fs in result.files:
            _save_file(conn, scan_id, fs)
    return scan_id


def _save_file(conn: sqlite3.Connection, scan_id: int, fs) -> None:
    file_id = conn.execute(
        "INSERT INTO file_score (scan_id, path, language, loc, score, parse_errors)"
        " VALUES (?,?,?,?,?,?)",
        (scan_id, fs.path, fs.language, fs.loc, fs.score, fs.parse_errors),
    ).lastrowid
    conn.executemany(
        "INSERT INTO finding (file_id, smell, level, line, symbol, value, threshold, severity,"
        " penalty, message) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                file_id,
                f.smell,
                f.level,
                f.line,
                f.symbol,
                f.value,
                f.threshold,
                f.severity,
                f.penalty,
                f.message,
            )
            for f in fs.findings
        ],
    )
    conn.executemany(
        "INSERT INTO function_metric (file_id, qualname, line, loc, cyclomatic, cognitive,"
        " max_nesting, bumps, args, widget_depth) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                file_id,
                m.qualname,
                m.start_line,
                m.loc,
                m.cyclomatic,
                m.cognitive,
                m.max_nesting,
                len(m.bumps),
                m.args,
                m.widget_depth,
            )
            for m in fs.functions
        ],
    )


# --- queries ------------------------------------------------------------------------


def list_scans(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Most recent first."""
    rows = conn.execute("SELECT * FROM scan ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def latest_scan_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT max(id) FROM scan").fetchone()
    return row[0]


def get_scan(conn: sqlite3.Connection, scan_id: int) -> dict | None:
    """A scan with its files (worst first), each with finding counts."""
    scan = conn.execute("SELECT * FROM scan WHERE id = ?", (scan_id,)).fetchone()
    if scan is None:
        return None
    files = conn.execute(
        "SELECT f.*, count(x.id) AS finding_count, coalesce(sum(x.penalty), 0) AS penalty"
        " FROM file_score f LEFT JOIN finding x ON x.file_id = f.id"
        " WHERE f.scan_id = ? GROUP BY f.id ORDER BY f.score, f.loc DESC",
        (scan_id,),
    ).fetchall()
    return {**dict(scan), "files": [dict(f) for f in files]}


def get_file(conn: sqlite3.Connection, scan_id: int, path: str) -> dict | None:
    """One file of a scan with findings and function metrics."""
    row = conn.execute(
        "SELECT * FROM file_score WHERE scan_id = ? AND path = ?", (scan_id, path)
    ).fetchone()
    if row is None:
        return None
    findings = conn.execute(
        "SELECT * FROM finding WHERE file_id = ? ORDER BY penalty DESC, line", (row["id"],)
    ).fetchall()
    functions = conn.execute(
        "SELECT * FROM function_metric WHERE file_id = ? ORDER BY line", (row["id"],)
    ).fetchall()
    return {
        **dict(row),
        "findings": [dict(f) for f in findings],
        "functions": [dict(f) for f in functions],
    }


def project_trend(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    """Project score per scan, oldest first."""
    rows = conn.execute(
        "SELECT id, created_at, git_commit, score, loc, file_count, config_hash,"
        " tool_version, parser_versions FROM"
        " (SELECT * FROM scan ORDER BY id DESC LIMIT ?) ORDER BY id",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def file_trend(conn: sqlite3.Connection, path: str, limit: int = 200) -> list[dict]:
    """One file's score per scan it appears in, oldest first."""
    rows = conn.execute(
        "SELECT s.id AS scan_id, s.created_at, s.git_commit, s.tool_version, s.parser_versions,"
        " f.score, f.loc"
        " FROM file_score f"
        " JOIN scan s ON s.id = f.scan_id WHERE f.path = ? ORDER BY s.id DESC LIMIT ?",
        (path, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def smell_summary(conn: sqlite3.Connection, scan_id: int) -> list[dict]:
    """Findings per smell in a scan: count, files affected, total penalty."""
    rows = conn.execute(
        "SELECT x.smell, count(*) AS count, count(DISTINCT f.id) AS files,"
        " round(sum(x.penalty), 2) AS penalty FROM finding x"
        " JOIN file_score f ON f.id = x.file_id WHERE f.scan_id = ?"
        " GROUP BY x.smell ORDER BY penalty DESC",
        (scan_id,),
    ).fetchall()
    return [dict(r) for r in rows]
