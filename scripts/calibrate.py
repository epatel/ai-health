"""Calibration harness (M11): score reference corpora and compare scoring variants.

Usage:
    .venv/bin/python scripts/calibrate.py [--cache DIR] [--rescan]

Scans each corpus that exists on this machine (read-only: nothing is saved
into the corpora), caches the findings, then re-scores them under each
scoring variant without rescanning. Prints per-corpus project score, file
score percentiles, band shares and the smells that drive the penalty.
"""

# Formula variants and statistics: the floats (caps, decays, percentiles) are
# the domain here, not stand-ins for missing types.
# aihealth: disable=primitive_obsession

from __future__ import annotations

import argparse
import math
import os
import pickle
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from aihealth.config import Config
from aihealth.engine import scan
from aihealth.scoring import band, file_score, penalty

SITE = (
    Path(sys.prefix)
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)
FLUTTER = Path.home() / "Development/Flutter/flutter/packages"
PUB = Path.home() / ".pub-cache/hosted/pub.dev"
# shallow clones of the TypeScript repos below, in one directory
TS = Path(os.environ.get("AIHEALTH_TS_CORPORA", Path.home() / "Development/ts-corpora"))
# shallow clones of the JavaScript repos below, in one directory
JS = Path(os.environ.get("AIHEALTH_JS_CORPORA", Path.home() / "Development/js-corpora"))
CARGO = Path.home() / ".cargo/registry/src"


def crate(name: str) -> Path:
    """`src/` of the newest downloaded version of a crates.io crate."""
    found = sorted(CARGO.glob(f"*/{name}-[0-9]*"), key=lambda p: p.name)
    return found[-1] / "src" if found else CARGO / f"{name}-missing"


# shallow clones (`git clone --depth 1`) of the Swift repos below, in one directory
SWIFT = Path(os.environ.get("AIHEALTH_SWIFT_CORPORA", Path.home() / "Development/swift-corpora"))

# (name, path, expectation) - "lib": well-regarded library code, expected mostly
# healthy; "complex": large, inherently complex code, expected lower.
CORPORA = [
    ("py:click", SITE / "click", "lib"),
    ("py:httpx", SITE / "httpx", "lib"),
    ("py:starlette", SITE / "starlette", "lib"),
    ("py:jinja2", SITE / "jinja2", "lib"),
    ("py:fastapi", SITE / "fastapi", "lib"),
    ("py:uvicorn", SITE / "uvicorn", "lib"),
    ("py:anyio", SITE / "anyio", "lib"),
    ("py:pydantic", SITE / "pydantic", "complex"),
    ("py:pip", SITE / "pip/_internal", "complex"),
    ("dart:provider", PUB / "provider-6.1.5+1/lib", "lib"),
    ("dart:http", PUB / "http-1.6.0/lib", "lib"),
    ("dart:bloc", PUB / "bloc-9.2.1/lib", "lib"),
    ("dart:go_router", PUB / "go_router-18.1.1/lib", "lib"),
    ("dart:dio", PUB / "dio-5.11.1/lib", "lib"),
    ("dart:args", PUB / "args-2.7.0/lib", "lib"),
    ("dart:shelf", PUB / "shelf-1.4.3/lib", "lib"),
    ("dart:collection", PUB / "collection-1.19.1/lib", "lib"),
    ("dart:flutter_test", FLUTTER / "flutter_test/lib", "complex"),
    ("dart:flutter.widgets", FLUTTER / "flutter/lib/src/widgets", "complex"),
    ("dart:flutter.material", FLUTTER / "flutter/lib/src/material", "complex"),
    ("dart:flutter.rendering", FLUTTER / "flutter/lib/src/rendering", "complex"),
    ("dart:flutter_tools", FLUTTER / "flutter_tools/lib", "complex"),
    ("swift:Alamofire", SWIFT / "Alamofire/Source", "lib"),
    ("swift:argument-parser", SWIFT / "swift-argument-parser/Sources", "lib"),
    ("swift:collections", SWIFT / "swift-collections/Sources", "lib"),
    ("swift:Kingfisher", SWIFT / "Kingfisher/Sources", "lib"),
    ("swift:vapor", SWIFT / "vapor/Sources", "lib"),
    ("swift:tca", SWIFT / "swift-composable-architecture/Sources", "lib"),
    ("swift:IceCubesApp", SWIFT / "IceCubesApp", "complex"),
    ("swift:nio", SWIFT / "swift-nio/Sources", "complex"),
    ("swift:SwiftLint", SWIFT / "SwiftLint/Source", "complex"),
    ("rust:anyhow", crate("anyhow"), "lib"),
    ("rust:serde", crate("serde"), "lib"),
    ("rust:serde_json", crate("serde_json"), "lib"),
    ("rust:itertools", crate("itertools"), "lib"),
    ("rust:memchr", crate("memchr"), "lib"),
    ("rust:crossterm", crate("crossterm"), "lib"),
    ("rust:ratatui-widgets", crate("ratatui-widgets"), "lib"),
    ("rust:nom", crate("nom"), "lib"),
    ("rust:syn", crate("syn"), "complex"),
    ("rust:regex-syntax", crate("regex-syntax"), "complex"),
    ("rust:regex-automata", crate("regex-automata"), "complex"),
    ("rust:hashbrown", crate("hashbrown"), "complex"),
    ("rust:termwiz", crate("termwiz"), "complex"),
    ("ts:zod", TS / "zod/packages/zod/src", "lib"),
    ("ts:query-core", TS / "query/packages/query-core/src", "lib"),
    ("ts:react-query", TS / "query/packages/react-query/src", "lib"),
    ("ts:trpc-server", TS / "trpc/packages/server/src", "lib"),
    ("ts:date-fns", TS / "date-fns/pkgs/core/src", "lib"),
    ("ts:ky", TS / "ky/source", "lib"),
    ("ts:vite", TS / "vite/packages/vite/src", "complex"),
    ("ts:typeorm", TS / "typeorm/packages/typeorm/src", "complex"),
    ("ts:nest-core", TS / "nest/packages/core", "complex"),
    ("ts:excalidraw", TS / "excalidraw/packages/excalidraw", "complex"),
    ("js:express", JS / "express/lib", "lib"),
    ("js:axios", JS / "axios/lib", "lib"),
    ("js:preact", JS / "preact", "lib"),
    ("js:lodash", JS / "lodash", "lib"),
    ("js:webpack", JS / "webpack/lib", "complex"),
    ("js:eslint", JS / "eslint/lib", "complex"),
    ("js:three.js", JS / "three.js/src", "complex"),
]


@dataclass
class FileRec:
    path: str
    loc: int
    findings: list  # aihealth.smells.Finding


# TypeScript repos keep tests inside src/ (up to 91 % of the lines in
# TanStack Query). Calibration targets library code, as the Python and Dart
# corpora (which have few tests) do, so test files are left out here. Real
# scans still score test files in every language.
TS_TEST_FILES = [
    "*__tests__/*",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.ts",
    "*.spec.tsx",
    "*/test/*",
    "*/tests/*",
    "*/test-d/*",
]
# JavaScript repos: the same, plus built and vendored copies at the root
JS_EXCLUDES = [
    *(p.replace(".ts", ".js") for p in TS_TEST_FILES),
    "test/*",
    "dist/*",
    "vendor/*",
    "perf/*",
    "benchmarks/*",
]


def corpus_config(name: str) -> Config:
    config = Config()
    if name.startswith("ts:"):
        config.data["scan"]["exclude"] = list(TS_TEST_FILES)
    elif name.startswith("js:"):
        config.data["scan"]["exclude"] = list(JS_EXCLUDES)
    return config


def load(name: str, root: Path, cache: Path, rescan: bool) -> list[FileRec]:
    f = cache / (name.replace(":", "_").replace("/", "_") + ".pkl")
    if f.exists() and not rescan:
        return pickle.loads(f.read_bytes())
    t = time.time()
    result = scan(root, corpus_config(name))
    recs = [FileRec(fs.path, fs.loc, fs.findings) for fs in result.files]
    f.write_bytes(pickle.dumps(recs))
    print(f"  scanned {name}: {len(recs)} files in {time.time() - t:.1f}s", file=sys.stderr)
    return recs


# --- scoring variants -------------------------------------------------------------
# Each variant maps a file's findings to a score. "current" must equal scoring.py.


def _score(total: float, decay: float) -> float:
    return 10.0 - 9.0 * (1.0 - math.exp(-total / decay))


def v_m4(rec: FileRec, decay: float = 10.0) -> float:
    """The uncalibrated M4 formula, for comparison."""
    return _score(sum(penalty(f) for f in rec.findings), decay)


def v_current(rec: FileRec) -> float:
    """What scoring.py does now."""
    return file_score(rec.findings, rec.loc)


def _per_symbol(rec: FileRec) -> dict:
    groups = defaultdict(float)
    for f in rec.findings:
        groups[(f.level, f.symbol) if f.symbol else ("file", f.smell)] += penalty(f)
    return groups


def v_cap(cap: float, decay: float = 10.0):
    """Cap each symbol's summed penalty (stops one Brain Method's four
    overlapping findings from dominating)."""

    def score(rec: FileRec) -> float:
        return _score(sum(min(p, cap) for p in _per_symbol(rec).values()), decay)

    return score


def v_cap_density(cap: float, ref_loc: float, decay: float = 10.0):
    """Per-symbol cap, then dampen big files: the summed function/class
    penalty is divided by sqrt(loc / ref_loc) above ref_loc (file-level smells
    such as Large File are not dampened)."""

    def score(rec: FileRec) -> float:
        groups = _per_symbol(rec)
        local = sum(min(p, cap) for k, p in groups.items() if k[0] != "file")
        filewide = sum(p for k, p in groups.items() if k[0] == "file")
        damp = math.sqrt(max(1.0, rec.loc / ref_loc))
        return _score(local / damp + filewide, decay)

    return score


VARIANTS = {
    "m4": v_m4,
    "current": v_current,
    "cap4": v_cap(4.0),
    "cap4+dens500": v_cap_density(4.0, 500),
    "cap4+dens300": v_cap_density(4.0, 300),
    "cap3+dens300": v_cap_density(3.0, 300),
    "cap4+dens300+d15": v_cap_density(4.0, 300, decay=15.0),
}


# --- report -----------------------------------------------------------------------


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


def _weighted(scores: list[tuple[float, int]], keep) -> float:
    """Share of LoC whose file score satisfies ``keep``."""
    total = sum(loc for _, loc in scores) or 1
    return sum(loc for score, loc in scores if keep(score)) / total


def summarize(recs: list[FileRec], fn) -> dict:
    scores = [(fn(r), r.loc) for r in recs if r.loc > 0]
    files = [score for score, _ in scores]
    return {
        "project": sum(score * loc for score, loc in scores) / (sum(loc for _, loc in scores) or 1),
        "p25": pct(files, 0.25),
        "p50": pct(files, 0.5),
        "healthy%": 100 * sum(band(x) == "healthy" for x in files) / max(1, len(files)),
        "alertLoC%": 100 * _weighted(scores, lambda x: band(x) == "alert"),
    }


def smell_mix(recs: list[FileRec]) -> str:
    totals = defaultdict(float)
    for r in recs:
        for f in r.findings:
            totals[f.smell] += penalty(f)
    total = sum(totals.values()) or 1
    top = sorted(totals.items(), key=lambda kv: -kv[1])[:4]
    return ", ".join(f"{k} {100 * v / total:.0f}%" for k, v in top)


def _cell(s: dict) -> str:
    return f"{s['project']:4.1f} {s['p50']:4.1f} {s['healthy%']:4.0f} {s['alertLoC%']:7.0f}"


def print_table(data: list, names: list[str], mix: bool) -> None:
    head = f"{'corpus':24} {'kind':8} {'files':>5} {'kLoC':>6}  "
    print(head + "  ".join(f"{v:>22}" for v in names))
    print(" " * len(head) + "  ".join(f"{'proj p50 hlth% alrtLoC%':>22}" for _ in names))
    for name, kind, recs in data:
        kloc = sum(r.loc for r in recs) / 1000
        cells = "  ".join(f"{_cell(summarize(recs, VARIANTS[v])):>22}" for v in names)
        print(f"{name:24} {kind:8} {len(recs):5} {kloc:6.1f}  {cells}")
        if mix:
            print(f"{'':40}{smell_mix(recs)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="calibration-cache")
    ap.add_argument("--rescan", action="store_true")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--mix", action="store_true", help="show the smells driving the penalty")
    ap.add_argument("--only", default="", help="corpus name prefix, e.g. swift:")
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    corpora = [c for c in CORPORA if c[0].startswith(args.only) and c[1].is_dir()]
    data = [(n, kind, load(n, p, cache, args.rescan)) for n, p, kind in corpora]
    print_table(data, args.variants.split(","), args.mix)


if __name__ == "__main__":
    main()
