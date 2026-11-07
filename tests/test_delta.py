"""Delta mode (M5): git plumbing, pairing, verdicts and CLI."""

import json
import subprocess
from pathlib import Path

import pytest

from aihealth import gitutil
from aihealth.cli import main
from aihealth.config import Config
from aihealth.delta import compare, delta_for_contents, run_delta
from aihealth.gitutil import Revisions

CLEAN = "def f(x):\n    return x + 1\n"
NESTED = (
    "def f(x):\n"
    "    if x:\n"
    "        for y in x:\n"
    "            if y:\n"
    "                while y:\n"
    "                    y -= 1\n"
    "    return x\n"
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text(CLEAN)
    (tmp_path / "keep.py").write_text(CLEAN)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _by_path(result):
    return {f.path: f for f in result.files}


# --- pure comparison ------------------------------------------------------------


def test_delta_for_contents_new_smell():
    fd = delta_for_contents("a.py", CLEAN.encode(), NESTED.encode(), Config())
    assert fd.verdict == "degraded"
    assert [c.finding.smell for c in fd.of_kind("new")] == ["deep_nesting"]
    assert fd.score_delta < 0


def test_delta_for_contents_fix_and_unchanged():
    fd = delta_for_contents("a.py", NESTED.encode(), CLEAN.encode(), Config())
    assert fd.verdict == "improved"
    assert [c.kind for c in fd.changes] == ["fixed"]
    same = delta_for_contents("a.py", CLEAN.encode(), (CLEAN + "\n").encode(), Config())
    assert same.verdict == "unchanged" and same.changes == []


def test_worsened_and_improved_severity():
    deeper = NESTED.replace("y -= 1", "if y > 2:\n                        y -= 1")
    fd = delta_for_contents("a.py", NESTED.encode(), deeper.encode(), Config())
    assert [(c.kind, c.finding.smell) for c in fd.changes] == [("worsened", "deep_nesting")]
    assert fd.changes[0].severity_before < fd.changes[0].finding.severity
    back = compare(fd.after, fd.before)
    assert [c.kind for c in back] == ["improved"]


def test_trading_smells_without_net_gain_is_degraded():
    # fix nesting but add a 6-arg signature: still a new smell, and if the
    # score doesn't rise the verdict is degraded
    many_args = "def f(a, b, c, d, e, g):\n    return a\n"
    fd = delta_for_contents("a.py", CLEAN.encode(), many_args.encode(), Config())
    assert fd.verdict == "degraded"


def test_new_file_judged_on_own_findings():
    assert delta_for_contents("n.py", None, NESTED.encode(), Config()).verdict == ("degraded")
    assert delta_for_contents("n.py", None, CLEAN.encode(), Config()).verdict == ("unchanged")


def test_tolerance_absorbs_small_score_drops():
    cfg = Config()
    # same smell, function grows: share rises -> penalty rises, no new smell
    big_before = NESTED.replace("    return x\n", "    return x\n\n" + CLEAN * 20)
    fd = delta_for_contents("a.py", big_before.encode(), NESTED.encode(), cfg)
    assert fd.score_delta < 0 and not fd.of_kind("new", "worsened")
    assert fd.verdict == "degraded"
    cfg.data["delta"]["tolerance"] = 5.0
    assert delta_for_contents("a.py", big_before.encode(), NESTED.encode(), cfg).verdict == (
        "unchanged"
    )


# --- git modes ------------------------------------------------------------------------


def test_worktree_includes_untracked(repo):
    (repo / "app.py").write_text(NESTED)
    (repo / "fresh.py").write_text(CLEAN)
    (repo / "notes.md").write_text("ignored")
    files = _by_path(run_delta(repo, Config()))
    assert set(files) == {"app.py", "fresh.py"}
    assert files["app.py"].verdict == "degraded"
    assert files["fresh.py"].status == "added"


def test_staged_ignores_unstaged(repo):
    (repo / "app.py").write_text(NESTED)
    assert run_delta(repo, Config(), Revisions(head=gitutil.INDEX)).files == []
    git(repo, "add", "app.py")
    (repo / "app.py").write_text(CLEAN)  # unstaged revert
    result = run_delta(repo, Config(), Revisions(head=gitutil.INDEX))
    assert result.verdict == "degraded"


def test_commit_range_and_path_filter(repo):
    (repo / "app.py").write_text(NESTED)
    (repo / "keep.py").write_text(NESTED)
    git(repo, "commit", "-qam", "nest")
    result = run_delta(repo, Config(), Revisions("HEAD~1", "HEAD"))
    assert {f.path for f in result.files} == {"app.py", "keep.py"}
    only = run_delta(repo, Config(), Revisions("HEAD~1", "HEAD"), [str(repo / "keep.py")])
    assert [f.path for f in only.files] == ["keep.py"]


def test_rename_pairs_findings(repo):
    (repo / "app.py").write_text(NESTED)
    git(repo, "commit", "-qam", "nest")
    git(repo, "mv", "app.py", "moved.py")
    fd = run_delta(repo, Config(), Revisions(head=gitutil.INDEX)).files[0]
    assert (fd.status, fd.old_path, fd.path) == ("renamed", "app.py", "moved.py")
    assert fd.changes == [] and fd.verdict == "unchanged"


def test_deleted_and_excluded_files(repo):
    (repo / "keep.py").unlink()
    (repo / "model.g.dart").write_text("int f() => 1;\n")
    result = run_delta(repo, Config())
    assert result.files == []
    assert result.skipped == [("model.g.dart", "excluded")]


def test_empty_repository(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text(NESTED)
    result = run_delta(tmp_path, Config())
    assert result.base is None
    assert result.files[0].status == "added" and result.verdict == "degraded"


def test_unknown_base_raises(repo):
    with pytest.raises(gitutil.GitError, match="unknown revision"):
        run_delta(repo, Config(), Revisions("nope"))


# --- cli ------------------------------------------------------------------------------


def test_cli_exit_codes_and_text(repo, capsys):
    assert main(["delta", "--repo", str(repo)]) == 0
    assert "no changed" in capsys.readouterr().out
    (repo / "app.py").write_text(NESTED)
    assert main(["delta", "--repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "DEGRADED" in out and "Deep Nesting" in out and "→" in out


def test_cli_json(repo, capsys):
    (repo / "app.py").write_text(NESTED)
    assert main(["delta", "--repo", str(repo), "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "degraded" and data["head"] == "worktree"
    change = data["files"][0]["changes"][0]
    assert change["kind"] == "new" and change["smell"] == "deep_nesting" and change["hint"]


def test_cli_errors(tmp_path, capsys):
    assert main(["delta", "--repo", str(tmp_path)]) == 2  # not a git repo
    assert "error" in capsys.readouterr().err
