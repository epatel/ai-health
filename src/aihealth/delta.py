"""Delta mode: how does a change affect code health?

For each changed supported file, the *before* and *after* contents are
scored independently (duplication within the file only), then findings are
paired by (level, symbol, smell). Functions pair by qualname, so a rename
shows up as fixed-on-old-name plus new-on-new-name.

Per-file verdict:
- ``degraded``  score dropped by more than ``delta.tolerance``, or new/worsened
  smells without a net score gain (no trading one smell for another while
  the file gets worse)
- ``improved``  score rose, or smells were fixed/reduced
- ``unchanged`` otherwise
Overall: degraded if any file degraded, else improved if any improved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aihealth import gitutil
from aihealth.config import Config
from aihealth.engine import is_excluded, score_source, too_large
from aihealth.gitutil import ChangedFile, Revisions
from aihealth.lang import is_supported, may_be_script
from aihealth.lang.base import is_generated
from aihealth.scoring import FileScore
from aihealth.smells import Finding

EPS = 1e-6
DEGRADED, IMPROVED, UNCHANGED = "degraded", "improved", "unchanged"


@dataclass
class Change:
    kind: str  # new | worsened | improved | fixed
    finding: Finding  # after-finding (before-finding for `fixed`)
    severity_before: float | None = None
    value_before: float | None = None

    def to_dict(self) -> dict:
        d = {"kind": self.kind, **self.finding.to_dict()}
        d["severity_before"] = self.severity_before
        d["value_before"] = self.value_before
        return d


@dataclass
class FileDelta:
    path: str
    old_path: str | None
    status: str  # added | modified | renamed | deleted
    before: FileScore | None
    after: FileScore | None
    changes: list[Change] = field(default_factory=list)
    verdict: str = UNCHANGED

    @property
    def score_before(self) -> float | None:
        return self.before.score if self.before else None

    @property
    def score_after(self) -> float | None:
        return self.after.score if self.after else None

    @property
    def score_delta(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return round(self.after.score - self.before.score, 2)

    def of_kind(self, *kinds: str) -> list[Change]:
        return [c for c in self.changes if c.kind in kinds]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "old_path": self.old_path,
            "status": self.status,
            "verdict": self.verdict,
            "score_before": self.score_before,
            "score_after": self.score_after,
            "score_delta": self.score_delta,
            "parse_errors": self.after.parse_errors if self.after else 0,
            "changes": [c.to_dict() for c in self.changes],
        }


@dataclass
class DeltaResult:
    root: str
    base: str | None
    head: str  # "worktree" | "index" | rev
    files: list[FileDelta]
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        verdicts = {f.verdict for f in self.files}
        if DEGRADED in verdicts:
            return DEGRADED
        if IMPROVED in verdicts:
            return IMPROVED
        return UNCHANGED

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "base": self.base,
            "head": self.head,
            "verdict": self.verdict,
            "files": [f.to_dict() for f in self.files],
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
        }


def _key(f: Finding) -> tuple:
    return (f.level, f.symbol, f.smell)


_ORDER = {"new": 0, "worsened": 1, "improved": 2, "fixed": 3}


def _classify(prev: Finding | None, cur: Finding) -> Change | None:
    if prev is None:
        return Change("new", cur)
    if cur.severity > prev.severity + EPS:
        return Change("worsened", cur, prev.severity, prev.value)
    if cur.severity < prev.severity - EPS:
        return Change("improved", cur, prev.severity, prev.value)
    return None


def compare(before: FileScore | None, after: FileScore | None) -> list[Change]:
    """Pair findings and classify. Sorted most important first."""
    old = {_key(f): f for f in before.findings} if before else {}
    new = {_key(f): f for f in after.findings} if after else {}
    changes = [c for k, f in new.items() if (c := _classify(old.get(k), f)) is not None]
    changes += [Change("fixed", f, f.severity) for k, f in old.items() if k not in new]
    changes.sort(key=lambda c: (_ORDER[c.kind], -c.finding.penalty, c.finding.line))
    return changes


def file_verdict(fd: FileDelta, tolerance: float) -> str:
    bad = fd.of_kind("new", "worsened")
    good = fd.of_kind("fixed", "improved")
    if fd.before is None:  # added file: judged on its own findings
        return DEGRADED if bad else UNCHANGED
    delta = fd.score_delta or 0.0
    if delta < -tolerance - EPS:
        return DEGRADED
    if bad and delta <= EPS:
        return DEGRADED
    if delta > EPS or good:
        return IMPROVED
    return UNCHANGED


def delta_for_contents(
    change: ChangedFile | str, before: bytes | None, after: bytes | None, config: Config
) -> FileDelta:
    """Delta of one file given both contents (None = absent). ``change`` may
    be a plain path for a modified file."""
    if isinstance(change, str):
        change = ChangedFile("modified" if before is not None else "added", change, change)
    path, old_path = change.path, change.old_path
    # score the old content under the new path (same language) so findings,
    # which carry the path, pair across a rename
    before_path = path
    if old_path and Path(old_path).suffix.lower() != Path(path).suffix.lower():
        before_path = old_path
    sb = score_source(before, before_path, config) if before is not None else None
    sa = score_source(after, path, config) if after is not None else None
    status = change.status if sb is not None else "added"
    fd = FileDelta(path, old_path, status, sb, sa, compare(sb, sa))
    fd.verdict = file_verdict(fd, config.section("delta")["tolerance"])
    return fd


def _resolve_revisions(root: Path, revs: Revisions) -> Revisions:
    """Check both revisions exist. A repository without commits has no HEAD:
    compare against nothing."""
    head_is_rev = revs.head not in (gitutil.WORKTREE, gitutil.INDEX)
    if head_is_rev and not gitutil.has_revision(root, revs.head):
        raise gitutil.GitError(f"unknown revision: {revs.head}")
    if revs.base is None or gitutil.has_revision(root, revs.base):
        return revs
    if revs.base != "HEAD":
        raise gitutil.GitError(f"unknown revision: {revs.base}")
    return Revisions(None, revs.head)


def _skip_reason(ch: ChangedFile, config: Config) -> str | None:
    """Why a changed file is not scored ("" = silently ignored)."""
    if ch.status == "deleted":
        return ""  # removing code never degrades health
    if not is_supported(ch.path) and not may_be_script(ch.path):
        return ""  # extensionless files are checked by content in _file_delta
    if is_excluded(ch.path, config.excludes):
        return "excluded"
    return None


def _file_delta(root: Path, ch: ChangedFile, revs: Revisions, config: Config) -> FileDelta | str:
    """The file's delta, or why it was skipped."""
    after = gitutil.read_file(root, revs.head, ch.path)
    if after is None:
        return "unreadable"
    if not is_supported(ch.path, after):
        return ""  # an extensionless file without a shell shebang
    if is_generated(after):
        return "generated"
    before = None
    if revs.base is not None and ch.old_path:
        before = gitutil.read_file(root, revs.base, ch.old_path)
    largest = max(len(after), len(before or b""))
    return too_large(largest, config) or delta_for_contents(ch, before, after, config)


def run_delta(
    repo: str | Path,
    config: Config,
    revs: Revisions | None = None,
    paths: list[str] | None = None,
) -> DeltaResult:
    """Delta of every changed supported file between ``revs.base`` and ``revs.head``."""
    root = gitutil.repo_root(repo)
    revs = _resolve_revisions(root, revs or Revisions())
    rel_paths = [_rel(root, p) for p in paths] if paths else None
    result = DeltaResult(str(root), revs.base, revs.head_label, [])
    for ch in gitutil.changed_files(root, revs, rel_paths):
        reason = _skip_reason(ch, config)
        outcome = _file_delta(root, ch, revs, config) if reason is None else reason
        if isinstance(outcome, FileDelta):
            result.files.append(outcome)
        elif outcome:
            result.skipped.append((ch.path, outcome))
    result.files.sort(key=lambda f: ({DEGRADED: 0, IMPROVED: 1}.get(f.verdict, 2), f.path))
    return result


def _rel(root: Path, p: str) -> str:
    path = Path(p)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p
