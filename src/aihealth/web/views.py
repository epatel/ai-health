"""Data shaping for the dashboard pages (no web framework imports)."""

from __future__ import annotations

import sqlite3
from pathlib import PurePosixPath
from typing import NotRequired, TypedDict

from aihealth import store
from aihealth.scoring import band
from aihealth.smells import HINTS, LABELS


class FileRow(TypedDict):
    """A file_score row as returned by store.get_scan."""

    path: str
    loc: int
    score: float
    finding_count: NotRequired[int]


def band_counts(files: list[FileRow]) -> dict[str, int]:
    counts = {"healthy": 0, "warning": 0, "alert": 0}
    for f in files:
        counts[band(f["score"])] += 1
    return counts


def treemap_tree(files: list[FileRow]) -> list[dict]:
    """Nest files by directory for an ECharts treemap: size = LoC; each
    leaf carries score/band/findings for colour and tooltip."""
    root: dict = {"children": {}}
    for f in files:
        node = root
        parts = PurePosixPath(f["path"]).parts
        for part in parts[:-1]:
            node = node["children"].setdefault(part, {"name": part, "children": {}})
        node["children"][parts[-1]] = {
            "name": parts[-1],
            "path": f["path"],
            "value": max(f["loc"], 1),
            "score": f["score"],
            "band": band(f["score"]),
            "findings": f.get("finding_count", 0),
        }
    return _listify(root)["children"]


def _listify(node: dict) -> dict:
    if "children" not in node:
        return node
    kids = [_listify(c) for c in node["children"].values()]
    return {**node, "children": sorted(kids, key=lambda c: c["name"])}


def overview(conn: sqlite3.Connection, scan_id: int) -> dict | None:
    scan = store.get_scan(conn, scan_id)
    if scan is None:
        return None
    files = scan["files"]
    smells = [
        {**s, "label": LABELS.get(s["smell"], s["smell"])}
        for s in store.smell_summary(conn, scan_id)
    ]
    return {
        "scan": scan,
        "files": files,
        "worst": [f for f in files if f["finding_count"]][:15],
        "counts": band_counts(files),
        "findings": sum(f["finding_count"] for f in files),
        "smells": smells,
        "trend": store.project_trend(conn),
        "top_smells": _top_smells(conn, scan_id),
    }


def _top_smells(conn: sqlite3.Connection, scan_id: int) -> dict[str, list[str]]:
    """Per path: up to 3 smell labels by penalty (for the worst-files table)."""
    rows = conn.execute(
        "SELECT f.path, x.smell, sum(x.penalty) AS p FROM finding x JOIN file_score f"
        " ON f.id = x.file_id WHERE f.scan_id = ? GROUP BY f.path, x.smell"
        " ORDER BY f.path, p DESC",
        (scan_id,),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for path, smell, _ in rows:
        labels = out.setdefault(path, [])
        if len(labels) < 3:
            labels.append(LABELS.get(smell, smell))
    return out


def file_view(conn: sqlite3.Connection, scan_id: int, path: str) -> dict | None:
    data = store.get_file(conn, scan_id, path)
    if data is None:
        return None
    findings = [
        {**f, "label": LABELS.get(f["smell"], f["smell"]), "hint": HINTS.get(f["smell"], "")}
        for f in data["findings"]
    ]
    functions = sorted(data["functions"], key=lambda m: (-m["cognitive"], m["line"]))
    return {
        "file": data,
        "findings": findings,
        "functions": functions,
        "trend": store.file_trend(conn, path),
    }
