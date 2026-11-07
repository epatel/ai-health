"""MCP server (M7): tool functions directly, plus a stdio round trip."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aihealth import mcp_server as srv

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
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text(CLEAN)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_review_changes(repo):
    assert "**UNCHANGED**" in srv.review_changes(str(repo))
    (repo / "pkg" / "app.py").write_text(NESTED)
    md = srv.review_changes(str(repo))
    assert "**DEGRADED**" in md and "**pkg/app.py:" in md and "Fix:" in md
    data = json.loads(srv.review_changes(str(repo), output="json"))
    assert data["verdict"] == "degraded"
    # staged only: nothing staged yet
    assert "**UNCHANGED**" in srv.review_changes(str(repo), staged=True)
    # path filter relative to repo
    assert "No changed" in srv.review_changes(str(repo), paths=["other"])


def test_score_file(repo):
    (repo / "pkg" / "app.py").write_text(NESTED)
    md = srv.score_file("pkg/app.py", repo=str(repo))
    assert md.startswith("## pkg/app.py: ") and "Deep Nesting" in md
    assert json.loads(srv.score_file(str(repo / "pkg/app.py"), output="json"))["path"] == (
        "pkg/app.py"
    )
    with pytest.raises(ValueError, match="not found"):
        srv.score_file("nope.py", repo=str(repo))
    (repo / "README.md").write_text("# hi")
    with pytest.raises(ValueError, match="unsupported"):
        srv.score_file("README.md", repo=str(repo))


def test_preview_change_does_not_write(repo):
    target = repo / "pkg" / "app.py"
    md = srv.preview_change("pkg/app.py", NESTED, repo=str(repo))
    assert "(proposed content vs current file)" in md and "**DEGRADED**" in md
    assert target.read_text() == CLEAN  # untouched
    assert "**UNCHANGED**" in srv.preview_change("pkg/app.py", CLEAN, repo=str(repo))
    target.write_text(NESTED)
    assert "**IMPROVED**" in srv.preview_change("pkg/app.py", CLEAN, repo=str(repo))
    new = srv.preview_change("pkg/new.py", CLEAN, repo=str(repo))
    assert "| `pkg/new.py` | new |" in new
    with pytest.raises(ValueError, match="unsupported"):
        srv.preview_change("notes.txt", "x", repo=str(repo))


def test_scan_project(repo):
    (repo / "pkg" / "app.py").write_text(NESTED)
    md = srv.scan_project(str(repo), max_findings=1)
    assert md.startswith("## Code health: ") and "Top refactoring targets" in md
    with pytest.raises(ValueError, match="not a directory"):
        srv.scan_project(str(repo / "missing"))


@pytest.mark.parametrize("name", ["bumpy_road", "Bumpy Road", "bumpy-road", " BUMPY ROAD "])
def test_explain_smell_accepts_key_or_label(name):
    text = srv.explain_smell(name)
    assert text.startswith("## Bumpy Road (`bumpy_road`)")
    assert "How to fix" in text and "exceeds 1" in text


def test_explain_every_smell_and_unknown():
    for key in srv.LABELS:
        assert srv.EXPLANATIONS[key] in srv.explain_smell(key)
    with pytest.raises(ValueError, match="known:"):
        srv.explain_smell("spaghetti")


# --- stdio round trip ---------------------------------------------------------


def test_stdio_server_round_trip(repo):
    pytest.importorskip("mcp")
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    (repo / "pkg" / "app.py").write_text(NESTED)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "aihealth.cli", "mcp"], cwd=str(repo)
    )

    async def run():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            review = await session.call_tool("review_changes", {})
            bad = await session.call_tool("explain_smell", {"smell": "nope"})
            return init, tools, review, bad

    init, tools, review, bad = anyio.run(run)
    assert "review_changes" in (init.instructions or "")
    assert tools == {
        "review_changes",
        "score_file",
        "preview_change",
        "scan_project",
        "explain_smell",
    }
    assert "**DEGRADED**" in review.content[0].text  # cwd = repo is the default
    assert bad.is_error and "unknown smell" in bad.content[0].text


def test_explain_missing_phony():
    text = srv.explain_smell("Missing .PHONY")
    assert text.startswith("## Missing .PHONY (`missing_phony`)")
    assert "undeclared phony-looking target" in text


def test_tools_refuse_files_over_max_file_kb(repo):
    (repo / ".aihealth.toml").write_text("[scan]\nmax_file_kb = 1\n")
    big = NESTED + "# padding\n" * 120
    (repo / "pkg" / "app.py").write_text(big)
    with pytest.raises(
        srv.ToolInputError, match=r"not scored: pkg/app.py \(over scan.max_file_kb: 2 KB > 1 KB\)"
    ):
        srv.score_file("pkg/app.py", repo=str(repo))
    with pytest.raises(srv.ToolInputError, match="max_file_kb"):
        srv.preview_change("pkg/new.py", big, repo=str(repo))
    md = srv.review_changes(str(repo))
    assert "No changed files were scored." in md
    assert "Not scored: `pkg/app.py` (over scan.max_file_kb: 2 KB > 1 KB)." in md
