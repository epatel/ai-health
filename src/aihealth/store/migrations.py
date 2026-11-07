"""Schema migrations: ordered, forward-only, dependency-free.

Rules (see cards/standing-defaults.md):
- ``PRAGMA user_version`` holds the schema version (= migrations applied).
- Never edit a migration that has shipped; append a new one.
- Each step runs inside an explicit BEGIN ... COMMIT together with its
  version bump, so a failing step leaves the schema exactly as it was.
- A fresh database runs every step, so there is one code path.
"""

from __future__ import annotations

import sqlite3

MIGRATIONS: list[str] = [
    # 1: scans, per-file scores, findings
    """
    CREATE TABLE scan (
        id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,           -- ISO 8601 UTC
        root TEXT NOT NULL,
        git_commit TEXT,
        git_branch TEXT,
        git_dirty INTEGER,                  -- 1 if the work tree had changes
        score REAL,                         -- LoC-weighted project score
        loc INTEGER NOT NULL,
        file_count INTEGER NOT NULL,
        tool_version TEXT NOT NULL,
        config_hash TEXT NOT NULL           -- scores are comparable only per config
    );
    CREATE TABLE file_score (
        id INTEGER PRIMARY KEY,
        scan_id INTEGER NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
        path TEXT NOT NULL,
        language TEXT NOT NULL,
        loc INTEGER NOT NULL,
        score REAL NOT NULL,
        parse_errors INTEGER NOT NULL DEFAULT 0,
        UNIQUE (scan_id, path)
    );
    CREATE INDEX file_score_path ON file_score (path, scan_id);
    CREATE TABLE finding (
        id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES file_score(id) ON DELETE CASCADE,
        smell TEXT NOT NULL,
        level TEXT NOT NULL,
        line INTEGER NOT NULL,
        symbol TEXT,
        value REAL NOT NULL,
        threshold REAL NOT NULL,
        severity REAL NOT NULL,
        penalty REAL NOT NULL,
        message TEXT NOT NULL
    );
    CREATE INDEX finding_file ON finding (file_id);
    """,
    # 2: per-function metrics for the dashboard's file view
    """
    CREATE TABLE function_metric (
        id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES file_score(id) ON DELETE CASCADE,
        qualname TEXT NOT NULL,
        line INTEGER NOT NULL,
        loc INTEGER NOT NULL,
        cyclomatic INTEGER NOT NULL,
        cognitive INTEGER NOT NULL,
        max_nesting INTEGER NOT NULL,
        bumps INTEGER NOT NULL,
        args INTEGER NOT NULL,
        widget_depth INTEGER NOT NULL
    );
    CREATE INDEX function_metric_file ON function_metric (file_id);
    """,
]


class SchemaTooNewError(RuntimeError):
    pass


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, migrations: list[str] | None = None) -> int:
    """Apply pending migrations in order; return the resulting version."""
    steps = MIGRATIONS if migrations is None else migrations
    version = schema_version(conn)
    if version > len(steps):
        raise SchemaTooNewError(
            f"database schema v{version} is newer than this aihealth (v{len(steps)}); upgrade aihealth"
        )
    for i, sql in enumerate(steps[version:], start=version):
        try:
            # executescript() commits any open transaction first and ignores
            # `with conn:`; the explicit BEGIN/COMMIT makes step + bump atomic
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {i + 1};\nCOMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
    return schema_version(conn)
