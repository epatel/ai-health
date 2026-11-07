"""Minimal git plumbing for delta mode (subprocess, no dependencies)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

WORKTREE = None  # sentinel "revision" meaning the files on disk
INDEX = ":"  # the staging area


class GitError(RuntimeError):
    pass


def _git(cwd: Path, *args: str, check: bool = True) -> bytes:
    try:
        res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except OSError as e:
        raise GitError(f"git not available: {e}") from e
    if check and res.returncode != 0:
        msg = res.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)}: {msg}")
    return res.stdout


def _rev(rev: str) -> str:
    """A revision safe to pass to git: one starting with `-` would be read
    as an option (`--output=FILE` makes `git diff` write a file)."""
    if rev.startswith("-"):
        raise GitError(f"invalid revision: {rev}")
    return rev


def repo_root(path: str | Path) -> Path:
    out = _git(Path(path), "rev-parse", "--show-toplevel").decode().strip()
    return Path(out)


def has_revision(root: Path, rev: str) -> bool:
    if rev.startswith("-"):
        return False
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        check=False,
        cwd=root,
        capture_output=True,
    )
    return res.returncode == 0


@dataclass
class ChangedFile:
    status: str  # added | modified | renamed | deleted
    path: str  # path after (or the deleted path)
    old_path: str | None  # path before, None if added


_STATUS = {
    "A": "added",
    "M": "modified",
    "R": "renamed",
    "C": "added",
    "D": "deleted",
    "T": "modified",
}


@dataclass(frozen=True)
class Revisions:
    """What to compare. ``base`` None means "no commits yet" (all added);
    ``head`` is WORKTREE (files on disk incl. untracked), INDEX, or a rev."""

    base: str | None = "HEAD"
    head: str | None = WORKTREE

    @property
    def head_label(self) -> str:
        if self.head is WORKTREE:
            return "worktree"
        return "index" if self.head == INDEX else str(self.head)


def changed_files(root: Path, revs: Revisions, paths: list[str] | None = None) -> list[ChangedFile]:
    """Files changed between ``revs.base`` and ``revs.head``."""
    spec = ["--", *paths] if paths else []
    if revs.base is None:
        return [ChangedFile("added", p, None) for p in _all_files(root, revs.head, spec)]
    result = _parse_name_status(_split(_git(root, *_diff_args(revs), *spec)))
    if revs.head is WORKTREE:
        untracked = _split(_git(root, "ls-files", "-z", "--others", "--exclude-standard", *spec))
        result.extend(ChangedFile("added", p, None) for p in untracked)
    return result


def _all_files(root: Path, head: str | None, spec: list[str]) -> list[str]:
    if head is WORKTREE:
        args = ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    elif head == INDEX:
        args = ["ls-files", "-z", "--cached"]
    else:
        args = ["ls-tree", "-r", "-z", "--name-only", "--end-of-options", _rev(head)]
    return _split(_git(root, *args, *spec))


def _diff_args(revs: Revisions) -> list[str]:
    args = ["diff", "--name-status", "-z", "-M", "--no-ext-diff"]
    if revs.head == INDEX:
        return [*args, "--cached", "--end-of-options", _rev(revs.base)]
    if revs.head is WORKTREE:
        return [*args, "--end-of-options", _rev(revs.base)]
    return [*args, "--end-of-options", _rev(revs.base), _rev(revs.head)]


def _parse_name_status(fields: list[str]) -> list[ChangedFile]:
    """`git diff --name-status -z` output: CODE NUL path [NUL new-path] ..."""
    result: list[ChangedFile] = []
    i = 0
    while i < len(fields):
        code = fields[i][0]
        if code in ("R", "C"):
            old, new = fields[i + 1], fields[i + 2]
            result.append(ChangedFile(_STATUS[code], new, old if code == "R" else None))
            i += 3
            continue
        status = _STATUS.get(code, "modified")
        path = fields[i + 1]
        result.append(ChangedFile(status, path, None if status == "added" else path))
        i += 2
    return result


def read_file(root: Path, rev: str | None, path: str) -> bytes | None:
    """Content of ``path`` at ``rev`` (WORKTREE, INDEX or a commit); None if absent."""
    if rev is WORKTREE:
        p = root / path
        return p.read_bytes() if p.is_file() else None
    spec = f":{path}" if rev == INDEX else f"{_rev(rev)}:{path}"
    res = subprocess.run(
        ["git", "show", "--end-of-options", spec], cwd=root, capture_output=True, check=False
    )
    return res.stdout if res.returncode == 0 else None


def _split(out: bytes) -> list[str]:
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def head_commit(root: Path) -> str | None:
    out = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).decode().strip()
    return out or None


def current_branch(root: Path) -> str | None:
    out = _git(root, "branch", "--show-current", check=False).decode().strip()
    return out or None  # None when detached


def is_dirty(root: Path) -> bool:
    return bool(_git(root, "status", "--porcelain", check=False).strip())
