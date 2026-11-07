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


def test_default_tolerance_never_excuses_new_smells():
    cfg = Config()
    assert cfg.section("delta")["tolerance"] == 0.1
    tiny = NESTED.replace("def f(x):", "def f(x, a, b, c, d):")  # new smell, small drop
    fd = delta_for_contents("a.py", NESTED.encode(), tiny.encode(), cfg)
    assert fd.of_kind("new") and fd.verdict == "degraded"


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
    # file shrinks around an unchanged smell: its share rises -> penalty rises,
    # no new or worse smell
    big_before = NESTED.replace("    return x\n", "    return x\n\n" + CLEAN * 20)
    fd = delta_for_contents("a.py", big_before.encode(), NESTED.encode(), cfg)
    assert fd.score_delta < 0 and not fd.of_kind("new", "worsened")
    cfg.data["delta"]["tolerance"] = 0.0  # strict: any drop fails
    strict = delta_for_contents("a.py", big_before.encode(), NESTED.encode(), cfg)
    assert strict.verdict == "degraded"
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


def test_unknown_head_raises(repo):
    with pytest.raises(gitutil.GitError, match="unknown revision: nope"):
        run_delta(repo, Config(), Revisions("HEAD", "nope"))


# --- option injection (AUDIT-REPORT.md finding 1) ----------------------------------


@pytest.mark.parametrize("flag", ["--base", "--head"])
def test_cli_revision_cannot_inject_git_options(repo, tmp_path_factory, capsys, flag):
    """`--head=--output=FILE` once made `git diff` write FILE."""
    target = tmp_path_factory.mktemp("out") / "pwned.txt"
    assert main(["delta", "--repo", str(repo), f"{flag}=--output={target}"]) != 0
    assert "revision" in capsys.readouterr().err
    assert not target.exists()


def test_git_layer_rejects_option_like_revisions(repo, tmp_path_factory):
    target = tmp_path_factory.mktemp("out") / "pwned.txt"
    evil = f"--output={target}"
    calls = [
        lambda: gitutil.changed_files(repo, Revisions("HEAD", evil)),
        lambda: gitutil.changed_files(repo, Revisions(evil, gitutil.INDEX)),
        lambda: gitutil.changed_files(repo, Revisions(None, evil)),  # ls-tree path
        lambda: gitutil.read_file(repo, evil, "app.py"),
    ]
    for call in calls:
        with pytest.raises(gitutil.GitError, match="invalid revision"):
            call()
    assert not gitutil.has_revision(repo, evil)
    assert not target.exists()


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


def test_repo_fsmonitor_command_never_runs(repo, tmp_path_factory, capsys):
    """A scanned repo's .git/config can set core.fsmonitor to a shell command,
    which git runs on ls-files/status/diff (AUDIT-REPORT.md finding 11)."""
    from aihealth import hooks

    marker = tmp_path_factory.mktemp("out") / "fsmonitor-ran"
    git(repo, "config", "core.fsmonitor", f"touch '{marker}'; false")
    (repo / "app.py").write_text(NESTED)
    db = tmp_path_factory.mktemp("db") / "health.db"
    assert main(["scan", str(repo)]) == 0
    assert main(["scan", str(repo), "--save", "--db", str(db)]) == 0
    assert main(["delta", "--repo", str(repo)]) == 1
    edit = {"cwd": str(repo), "tool_input": {"file_path": str(repo / "app.py")}}
    assert "DEGRADED" in hooks.run("post-edit", json.dumps(edit))
    capsys.readouterr()
    assert not marker.exists()


# --- size limit ---------------------------------------------------------------


def test_files_over_max_file_kb_are_skipped_with_the_reason(repo):
    config = Config()
    config.data["scan"]["max_file_kb"] = 1
    padding = "# padding\n" * 120  # over 1 KB
    (repo / "app.py").write_text(NESTED + padding)
    (repo / "keep.py").write_text(NESTED)
    result = run_delta(repo, config)
    assert [f.path for f in result.files] == ["keep.py"]
    assert result.skipped == [("app.py", "over scan.max_file_kb: 2 KB > 1 KB")]
    # a large previous version is skipped too: scoring it is just as slow
    git(repo, "commit", "-qam", "big")
    (repo / "app.py").write_text(CLEAN)
    assert [p for p, _ in run_delta(repo, config).skipped] == ["app.py"]


def test_sizes_and_heads_are_read_without_the_whole_file(repo):
    (repo / "app.py").write_text("x = 1\n" * 100)
    git(repo, "add", "app.py")
    (repo / "empty").write_text("")
    for rev in (gitutil.WORKTREE, gitutil.INDEX):
        assert gitutil.file_size(repo, rev, "app.py") == 600
        assert gitutil.read_head(repo, rev, "app.py", 10) == b"x = 1\nx = "
    assert gitutil.file_size(repo, "HEAD", "keep.py") == len(CLEAN)
    assert gitutil.read_head(repo, "HEAD", "keep.py", 5) == CLEAN[:5].encode()
    assert gitutil.read_head(repo, gitutil.WORKTREE, "empty") == b""
    for rev in (gitutil.WORKTREE, gitutil.INDEX, "HEAD"):
        assert gitutil.file_size(repo, rev, "nope.py") is None
        assert gitutil.read_head(repo, rev, "nope.py") is None


def test_files_over_the_limit_are_never_read_in_full(repo, monkeypatch):
    config = Config()
    config.data["scan"]["max_file_kb"] = 1
    (repo / "app.py").write_text(NESTED + "# padding\n" * 120)
    (repo / "blob").write_bytes(b"\0binary" * 300)  # extensionless, not a script
    (repo / "tool").write_text("#!/bin/sh\n" + "# padding\n" * 120)  # a script
    read = []
    original = gitutil.read_file
    monkeypatch.setattr(gitutil, "read_file", lambda *a: read.append(a[2]) or original(*a))
    result = run_delta(repo, config)
    assert read == []
    assert sorted(p for p, _ in result.skipped) == ["app.py", "tool"]  # `blob`: silently
