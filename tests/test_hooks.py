"""Claude Code hooks (M10): post-edit and stop."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aihealth import hooks

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


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text(CLEAN)
    (tmp_path / "b.py").write_text(CLEAN)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def edit_payload(repo: Path, name: str) -> str:
    return json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "cwd": str(repo),
            "tool_input": {"file_path": str(repo / name), "old_string": "x", "new_string": "y"},
        }
    )


def context(out: str, event: str) -> str:
    data = json.loads(out)["hookSpecificOutput"]
    assert data["hookEventName"] == event
    return data["additionalContext"]


def test_post_edit_silent_when_healthy(repo):
    assert hooks.run("post-edit", edit_payload(repo, "a.py")) == ""


def test_post_edit_reports_only_the_edited_file(repo):
    (repo / "a.py").write_text(NESTED)
    (repo / "b.py").write_text(NESTED)
    text = context(hooks.run("post-edit", edit_payload(repo, "a.py")), "PostToolUse")
    assert "**DEGRADED**" in text and "a.py" in text and "b.py" not in text


def test_post_edit_ignores_unsupported_and_outside_repo(repo, tmp_path_factory):
    (repo / "notes.md").write_text("# x")
    assert hooks.run("post-edit", edit_payload(repo, "notes.md")) == ""
    outside = tmp_path_factory.mktemp("nogit")
    (outside / "x.py").write_text(NESTED)
    assert hooks.run("post-edit", edit_payload(outside, "x.py")) == ""  # fails open


def test_stop_blocks_once_on_degraded_changes(repo):
    payload = {"hook_event_name": "Stop", "cwd": str(repo), "stop_hook_active": False}
    assert hooks.run("stop", json.dumps(payload)) == ""  # clean tree
    (repo / "b.py").write_text(NESTED)
    text = context(hooks.run("stop", json.dumps(payload)), "Stop")
    assert text.startswith(hooks.STOP_PREAMBLE) and "b.py" in text
    # already continuing because of a stop hook: never fire again (no loops)
    payload["stop_hook_active"] = True
    assert hooks.run("stop", json.dumps(payload)) == ""


@pytest.mark.parametrize("bad", ["not json", "[]", '{"cwd": 5}', '{"cwd": "/nonexistent"}'])
def test_hooks_fail_open_on_bad_input(bad):
    assert hooks.run("post-edit", bad) == ""
    assert hooks.run("stop", bad) == ""


def test_post_edit_with_empty_stdin_does_nothing():
    # (for `stop`, empty stdin means "no payload": it checks the cwd)
    assert hooks.run("post-edit", "") == ""


def test_cli_hook_round_trip(repo):
    (repo / "a.py").write_text(NESTED)
    res = subprocess.run(
        [sys.executable, "-m", "aihealth.cli", "hook", "post-edit"],
        input=edit_payload(repo, "a.py"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "DEGRADED" in context(res.stdout, "PostToolUse")
