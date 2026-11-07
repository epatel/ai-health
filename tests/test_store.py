"""Storage (M8): migration runner guarantees, scan persistence, history CLI."""

import json
import sqlite3
from pathlib import Path

import pytest

import aihealth
from aihealth.cli import main
from aihealth.config import Config
from aihealth.engine import scan
from aihealth.store import (
    MIGRATIONS,
    GitInfo,
    SchemaTooNewError,
    connect,
    file_trend,
    get_file,
    get_scan,
    latest_scan_id,
    list_scans,
    migrate,
    project_trend,
    save_scan,
    schema_version,
)

NESTED = (
    "def f(x):\n"
    "    if x:\n"
    "        for y in x:\n"
    "            if y:\n"
    "                while y:\n"
    "                    y -= 1\n"
    "    return x\n"
)


def tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# --- migration runner ------------------------------------------------------------------


def test_fresh_database_runs_all_migrations(tmp_path):
    conn = connect(tmp_path / "h.db")
    assert schema_version(conn) == len(MIGRATIONS)
    assert {"scan", "file_score", "finding", "function_metric"} <= tables(conn)


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "h.db")
    assert migrate(conn) == len(MIGRATIONS)
    assert migrate(conn) == len(MIGRATIONS)


def test_upgrade_from_v1_keeps_data(tmp_path):
    conn = sqlite3.connect(tmp_path / "h.db")
    migrate(conn, MIGRATIONS[:1])
    conn.execute(
        "INSERT INTO scan (created_at, root, loc, file_count, tool_version, config_hash)"
        " VALUES ('2026-01-01', '/r', 10, 1, '0.0.1', 'abc')"
    )
    conn.commit()
    assert "function_metric" not in tables(conn)
    assert migrate(conn) == len(MIGRATIONS)  # v1 -> latest
    assert "function_metric" in tables(conn)
    assert conn.execute("SELECT root FROM scan").fetchone()[0] == "/r"


def test_failing_migration_rolls_back_step_and_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "h.db")
    steps = [
        "CREATE TABLE a (x INTEGER);",
        "CREATE TABLE b (y INTEGER); INSERT INTO nope VALUES (1);",  # fails halfway
    ]
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, steps)
    assert schema_version(conn) == 1  # step 2 not recorded
    assert tables(conn) == {"a"}  # and its table not half-created
    steps[1] = "CREATE TABLE b (y INTEGER);"  # fixed: rerun continues at step 2
    assert migrate(conn, steps) == 2 and tables(conn) == {"a", "b"}


def test_refuses_newer_schema(tmp_path):
    conn = sqlite3.connect(tmp_path / "h.db")
    conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    with pytest.raises(SchemaTooNewError, match="upgrade aihealth"):
        migrate(conn)


def test_connect_creates_self_ignoring_dir(tmp_path):
    connect(tmp_path / ".aihealth" / "health.db")
    assert (tmp_path / ".aihealth" / ".gitignore").read_text().strip().endswith("*")


# --- persistence ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "bad.py").write_text(NESTED)
    (tmp_path / "ok.py").write_text("def g(x):\n    return x\n")
    return tmp_path


def test_save_and_read_back(project, tmp_path):
    cfg = Config()
    result = scan(project, cfg)
    conn = connect(tmp_path / "db" / "h.db")
    scan_id = save_scan(conn, result, cfg, GitInfo("abc123", "main", True))
    assert latest_scan_id(conn) == scan_id

    [row] = list_scans(conn)
    assert (row["score"], row["file_count"], row["git_commit"], row["git_dirty"]) == (
        result.score,
        2,
        "abc123",
        1,
    )
    assert len(row["config_hash"]) == 12

    stored = get_scan(conn, scan_id)
    assert [f["path"] for f in stored["files"]] == ["bad.py", "ok.py"]  # worst first
    assert stored["files"][0]["finding_count"] >= 1

    bad = get_file(conn, scan_id, "bad.py")
    assert {f["smell"] for f in bad["findings"]} >= {"deep_nesting"}
    assert bad["functions"][0]["qualname"] == "f" and bad["functions"][0]["max_nesting"] == 4
    assert get_file(conn, scan_id, "missing.py") is None
    assert get_scan(conn, 999) is None


def test_trends_and_cascade(project, tmp_path):
    cfg = Config()
    conn = connect(tmp_path / "h.db")
    first = save_scan(conn, scan(project, cfg), cfg, GitInfo())
    (project / "bad.py").write_text("def f(x):\n    return x\n")
    second = save_scan(conn, scan(project, cfg), cfg, GitInfo())
    trend = project_trend(conn)
    assert [t["id"] for t in trend] == [first, second]
    assert trend[1]["score"] > trend[0]["score"]
    assert [t["score"] for t in file_trend(conn, "bad.py")][-1] == 10.0
    # deleting a scan removes its files, findings and metrics
    conn.execute("DELETE FROM scan WHERE id = ?", (first,))
    assert conn.execute("SELECT count(*) FROM finding").fetchone()[0] == 0
    assert conn.execute("SELECT count(DISTINCT scan_id) FROM file_score").fetchone()[0] == 1


def test_config_change_changes_hash(project, tmp_path):
    conn = connect(tmp_path / "h.db")
    a, b = Config(), Config()
    b.data["thresholds"]["large_method"] = 5
    save_scan(conn, scan(project, a), a, GitInfo())
    save_scan(conn, scan(project, b), b, GitInfo())
    hashes = {s["config_hash"] for s in list_scans(conn)}
    assert len(hashes) == 2


# --- cli -------------------------------------------------------------------------------


def test_cli_save_and_history(project, capsys):
    assert main(["history", str(project)]) == 2
    assert "no scan history" in capsys.readouterr().err
    assert main(["scan", str(project), "--save"]) == 0
    assert "saved scan #1" in capsys.readouterr().err
    (project / "bad.py").write_text("def f(x):\n    return x\n")
    assert main(["scan", str(project), "--save", "--format", "json"]) == 0
    capsys.readouterr()
    assert main(["history", str(project)]) == 0
    out = capsys.readouterr().out
    assert "    1  " in out and "    2  " in out and "+" in out
    assert main(["history", str(project), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [s["id"] for s in data["scans"]] == [1, 2]


def test_cli_custom_db(project, tmp_path, capsys):
    db = tmp_path / "elsewhere.db"
    assert main(["scan", str(project), "--save", "--db", str(db)]) == 0
    assert db.is_file() and not (project / ".aihealth").exists()
    assert main(["history", "--db", str(db)]) == 0


# --- parser versions ----------------------------------------------------------------


def test_upgrade_from_v2_adds_parser_versions(tmp_path):
    conn = sqlite3.connect(tmp_path / "h.db")
    migrate(conn, MIGRATIONS[:2])
    conn.execute(
        "INSERT INTO scan (created_at, root, loc, file_count, tool_version, config_hash)"
        " VALUES ('2026-01-01', '/r', 10, 1, '0.0.1', 'abc')"
    )
    conn.commit()
    assert migrate(conn) == len(MIGRATIONS)
    assert conn.execute("SELECT root, parser_versions FROM scan").fetchone() == ("/r", None)


def test_scans_record_parser_versions(project, tmp_path):
    from importlib import metadata

    conn = connect(tmp_path / "h.db")
    save_scan(conn, scan(project, Config()), Config(), GitInfo())
    recorded = list_scans(conn)[0]["parser_versions"]
    assert recorded == (
        f"tree-sitter {metadata.version('tree-sitter')}, "
        f"tree-sitter-language-pack {metadata.version('tree-sitter-language-pack')}"
    )
    assert project_trend(conn)[0]["parser_versions"] == recorded
    assert file_trend(conn, "bad.py")[0]["parser_versions"] == recorded


def _history_rows(project, capsys, scans: int, updates: list[str]) -> list[str]:
    """`aihealth history` rows after saving ``scans`` scans and running
    ``updates`` (SQL) on the history database."""
    for _ in range(scans):
        assert main(["scan", str(project), "--save"]) == 0
    db = sqlite3.connect(project / ".aihealth" / "health.db")
    for sql in updates:
        db.execute(sql)
    db.commit()
    capsys.readouterr()
    assert main(["history", str(project)]) == 0
    return capsys.readouterr().out.splitlines()[3:]


def test_history_notes_parser_changes(project, capsys):
    rows = _history_rows(
        project,
        capsys,
        4,
        [
            "UPDATE scan SET parser_versions = NULL WHERE id = 1",  # saved before v3
            "UPDATE scan SET parser_versions = 'tree-sitter-language-pack 1.26.0' WHERE id = 4",
        ],
    )
    notes = ["parsers changed" in row for row in rows]
    assert notes == [False, False, False, True]  # unknown -> known is not a change
    assert "parsers changed: tree-sitter-language-pack 1.26.0" in rows[3]


def test_history_notes_aihealth_version_changes(project, capsys):
    rows = _history_rows(
        project, capsys, 2, ["UPDATE scan SET tool_version = '0.1.0' WHERE id = 1"]
    )
    assert "aihealth changed" not in rows[0]
    assert f"aihealth changed: {aihealth.__version__}" in rows[1]
