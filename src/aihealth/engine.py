"""Engine facade: score sources, files, and whole trees.

Everything user-facing (CLI, MCP, dashboard, delta) goes through here.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from aihealth import gitutil
from aihealth.config import Config
from aihealth.duplication import DuplicationIndex
from aihealth.lang import adapter_for, file_head, is_supported, may_be_script
from aihealth.lang.base import is_generated
from aihealth.metrics import function_metrics
from aihealth.model import Module
from aihealth.scoring import FileScore, ScoreParams, file_score, project_score
from aihealth.smells import detect


def score_module(module: Module, config: Config, clones=None) -> FileScore:
    adapter = adapter_for(module.path, config, module.source)
    metrics = [function_metrics(fn, adapter) for fn in module.all_functions()]
    findings = detect(module, adapter, config, metrics, clones)
    return FileScore(
        path=module.path,
        language=module.language,
        loc=module.loc,
        score=file_score(findings, module.loc, ScoreParams(**config.section("score"))),
        findings=findings,
        functions=metrics,
        parse_errors=module.error_count,
    )


def parse(source: bytes, path: str, config: Config) -> Module | None:
    adapter = adapter_for(path, config, source)
    return adapter.parse(source, path) if adapter else None


def score_source(source: bytes, path: str, config: Config | None = None) -> FileScore | None:
    """Score one file's content in isolation (duplication within the file only)."""
    config = config or Config()
    module = parse(source, path, config)
    if module is None:
        return None
    return score_module(module, config, _clones([module], config))


def _clones(modules: list[Module], config: Config):
    dup = config.section("duplication")
    if config.weight("code_duplication") <= 0:
        return []
    index = DuplicationIndex(min_tokens=dup["min_tokens"], min_lines=dup["min_lines"])
    for m in modules:
        index.add(m, adapter_for(m.path, config, m.source))
    return index.clones()


# --- paths -------------------------------------------------------------------


def project_root(path: Path) -> Path:
    """Git work tree root containing ``path``, else the directory itself (or
    the file's directory). Config (`.aihealth.toml`) is read from here."""
    directory = path if path.is_dir() else path.parent
    try:
        return gitutil.repo_root(directory)
    except gitutil.GitError:
        return directory


def project_path(path: str | Path) -> tuple[Path, str]:
    """(project root, path relative to it) for a file anywhere on disk."""
    absolute = Path(path).expanduser().resolve()
    root = project_root(absolute)
    try:
        return root, absolute.relative_to(root).as_posix()
    except ValueError:
        return root, absolute.name


# --- tree scan ----------------------------------------------------------------


def is_excluded(rel_path: str, patterns: Iterable[str]) -> bool:
    """fnmatch where `*` also crosses `/`; a pattern matches the path itself
    or any suffix starting at a directory boundary."""
    candidates = [rel_path] + [rel_path[i + 1 :] for i, ch in enumerate(rel_path) if ch == "/"]
    return any(fnmatch.fnmatch(c, p) for p in patterns for c in candidates)


def too_large(size: int, config: Config) -> str | None:
    """Why a file of ``size`` bytes isn't scored, or None. `scan.max_file_kb`
    bounds parse time in scan, delta, the hooks and the MCP tools."""
    limit_kb = config.section("scan")["max_file_kb"]
    if size <= limit_kb * 1024:
        return None
    return f"over scan.max_file_kb: {-(-size // 1024)} KB > {limit_kb} KB"


def discover(root: Path, config: Config) -> list[str]:
    """Supported source files under root (relative posix paths), honouring
    .gitignore when root is inside a git work tree, plus config excludes."""
    files = _git_files(root)
    if files is None:
        files = _walk_files(root)
    out = []
    for rel in files:
        head = file_head(root / rel) if may_be_script(rel) else None
        if not is_supported(rel, head):
            continue
        if is_excluded(rel, config.excludes):
            continue
        p = root / rel
        try:
            if not p.is_file() or too_large(p.stat().st_size, config):
                continue
        except OSError:
            continue
        out.append(rel)
    return sorted(out)


def _git_files(root: Path) -> list[str] | None:
    """Files git knows about (tracked + untracked, honouring .gitignore), or
    None when git can't tell: not a work tree, or `root` itself is ignored
    (e.g. a dependency inside .venv the user explicitly asked to scan)."""
    ignored = subprocess.run(
        [*gitutil.GIT, "check-ignore", "-q", "."], cwd=root, capture_output=True, check=False
    )
    if ignored.returncode == 0:
        return None
    try:
        res = subprocess.run(
            [*gitutil.GIT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in res.stdout.decode("utf-8", "replace").split("\0") if p]


_SKIP_DIRS = {".git", ".hg", ".venv", "venv", "node_modules", ".dart_tool", "build", "__pycache__"}


def _walk_files(root: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            out.append(Path(dirpath, name).relative_to(root).as_posix())
    return out


@dataclass
class ScanResult:
    root: str
    files: list[FileScore]
    skipped: list[tuple[str, str]]  # (path, reason)

    @property
    def score(self) -> float | None:
        return project_score(self.files)

    @property
    def loc(self) -> int:
        return sum(f.loc for f in self.files)

    def to_dict(self, with_functions: bool = False) -> dict:
        from aihealth.scoring import band

        score = self.score
        return {
            "root": self.root,
            "score": score,
            "band": band(score) if score is not None else None,
            "loc": self.loc,
            "file_count": len(self.files),
            "files": [
                f.to_dict(with_functions)
                for f in sorted(self.files, key=lambda f: (f.score, -f.loc))
            ],
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
        }


def scan(root: str | Path, config: Config) -> ScanResult:
    root = Path(root).resolve()
    modules: list[Module] = []
    skipped: list[tuple[str, str]] = []
    for rel in discover(root, config):
        try:
            source = (root / rel).read_bytes()
        except OSError as e:
            skipped.append((rel, str(e)))
            continue
        if is_generated(source):
            skipped.append((rel, "generated"))
            continue
        module = parse(source, rel, config)
        if module is not None:
            modules.append(module)
    cross = config.section("duplication")["cross_file"]
    if cross:
        clones = _clones(modules, config)
        files = [score_module(m, config, clones) for m in modules]
    else:
        files = [score_module(m, config, _clones([m], config)) for m in modules]
    return ScanResult(root=str(root), files=files, skipped=skipped)
