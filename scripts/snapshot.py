"""Snapshot everything the engine produces, to prove a change is behaviour-preserving.

    .venv/bin/python scripts/snapshot.py --out before.json ROOT [ROOT ...]
    ... change the code ...
    .venv/bin/python scripts/snapshot.py --out after.json ROOT [ROOT ...]
    .venv/bin/python scripts/snapshot.py --compare before.json after.json

Per supported file (generated files and >1 MB skipped): classes, functions
(qualname, kind, span, loc, params), cohesion member refs, function metrics,
findings and the file score. Per ROOT: the ordered cross-file clone list.
Read-only: nothing is written into the ROOTs. Write snapshots to a scratch
directory. Used for the metrics/Dart/Python/duplication refactors, where
up to 1M functions came out byte-identical.
"""

# CLI/JSON tool: paths, language names and JSON dicts are the domain here.
# aihealth: disable=primitive_obsession

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from aihealth.config import Config
from aihealth.duplication import DuplicationIndex
from aihealth.engine import score_module
from aihealth.lang import adapter_for, file_head, is_supported_file, parse_source
from aihealth.lang.base import is_generated

SKIP_DIRS = {
    ".git",
    "node_modules",
    "env",
    "venv",
    ".venv",
    "__pycache__",
    "CMakeFiles",
    ".dart_tool",
}
MAX_BYTES = 1_000_000


def source_files(root: Path, langs: set[str] | None) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        found += [Path(dirpath, f) for f in sorted(filenames) if wanted(Path(dirpath, f), langs)]
    return found


def wanted(path: Path, langs: set[str] | None) -> bool:
    adapter = adapter_for(path, head=file_head(path)) if is_supported_file(path) else None
    if adapter is None or not language_selected(adapter.language, langs):
        return False
    return path.is_file() and path.stat().st_size <= MAX_BYTES


def language_selected(language: str, langs: set[str] | None) -> bool:
    return not langs or language in langs


def file_record(module, adapter) -> dict:
    fs = score_module(module, Config(), [])
    return {
        "loc": module.loc,
        "classes": [(c.name, c.kind, c.start_line, c.end_line, c.loc) for c in module.classes],
        "functions": [
            (f.qualname, f.kind, f.start_line, f.end_line, f.loc, [asdict(p) for p in f.params])
            for f in module.all_functions()
        ],
        "member_refs": [
            sorted((k, sorted(v)) for k, v in adapter.member_refs(c).items())
            for c in module.classes
        ],
        "metrics": [asdict(m) for m in fs.functions],
        "findings": [(x.smell, x.symbol, x.line, x.value, x.severity) for x in fs.findings],
        "score": fs.score,
    }


def snapshot_root(root: Path, langs: set[str] | None) -> tuple[dict, list]:
    files: dict = {}
    index = DuplicationIndex()
    for path in source_files(root, langs):
        source = path.read_bytes()
        if is_generated(source):
            continue
        module, adapter = parse_source(source, str(path)), adapter_for(path, head=source)
        files[str(path)] = file_record(module, adapter)
        index.add(module, adapter)
    clones = [
        [
            c.a.path,
            c.a.qualname,
            c.a.start_line,
            c.a.end_line,
            c.b.path,
            c.b.qualname,
            c.b.start_line,
            c.b.end_line,
            c.tokens,
        ]
        for c in index.clones()
    ]
    return files, clones


def take(roots: list[str], langs: set[str] | None, out: Path) -> None:
    snap: dict = {"files": {}, "clones": {}}
    for root in roots:
        files, clones = snapshot_root(Path(root), langs)
        snap["files"].update(files)
        snap["clones"][root] = clones
        print(f"  {root}: {len(files)} files, {len(clones)} clones", file=sys.stderr)
    blob = json.dumps(snap, sort_keys=True, default=str).encode()
    out.write_bytes(blob)
    n_funcs = sum(len(v["functions"]) for v in snap["files"].values())
    print(
        f"{len(snap['files'])} files, {n_funcs} functions, sha256 {hashlib.sha256(blob).hexdigest()[:16]}"
    )


def compare(before_path: Path, after_path: Path) -> int:
    before, after = json.loads(before_path.read_bytes()), json.loads(after_path.read_bytes())
    changed_files = diff_files(before["files"], after["files"])
    changed_roots = diff_clones(before["clones"], after["clones"])
    return 1 if changed_files or changed_roots else 0


def diff_files(before: dict, after: dict) -> list[str]:
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    print(f"files: {len(before) - len(changed)} of {len(before)} identical")
    for path in changed[:20]:
        b, a = before.get(path, {}), after.get(path, {})
        print(f"  differs: {path} -> {sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))}")
    return changed


def diff_clones(before: dict, after: dict) -> list[str]:
    changed = [root for root in before if before[root] != after.get(root)]
    print(f"clone lists: {len(before) - len(changed)} of {len(before)} identical (incl. order)")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("roots", nargs="*")
    ap.add_argument("--out", type=Path, help="write a snapshot of ROOTs here")
    ap.add_argument("--lang", help="only these adapters, e.g. python,dart,make")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = ap.parse_args()
    if args.compare:
        return compare(*args.compare)
    if not args.out or not args.roots:
        ap.error("give --out and at least one ROOT, or --compare BEFORE AFTER")
    take(args.roots, set(args.lang.split(",")) if args.lang else None, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
