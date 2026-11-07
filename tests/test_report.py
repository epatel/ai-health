"""Report rendering (M6): text, markdown, json for scan and delta."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from aihealth.cli import main
from aihealth.config import Config
from aihealth.delta import DeltaResult, delta_for_contents
from aihealth.engine import scan
from aihealth.report import ReportOptions, cell, code, render_delta, render_scan

CLEAN = "def f(x):\n    return x + 1\n"
SMELLY = (
    "def g(a, b, c, d, e, f):\n"
    "    if a:\n"
    "        for x in b:\n"
    "            if x and c or d:\n"
    "                while x:\n"
    "                    x -= 1\n"
    "    return a\n"
)
NESTED_ONLY = SMELLY.replace("def g(a, b, c, d, e, f)", "def g(a, b, c, d)").replace(
    "x and c or d", "x"
)


def _delta(*pairs) -> DeltaResult:
    files = [delta_for_contents(p, b and b.encode(), a.encode(), Config()) for p, b, a in pairs]
    return DeltaResult("/repo", "HEAD", "worktree", files)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "ok.py").write_text(CLEAN)
    (tmp_path / "bad.py").write_text(SMELLY)
    (tmp_path / "bad2.py").write_text(NESTED_ONLY)
    return tmp_path


# --- delta markdown ------------------------------------------------------------------


def test_delta_markdown_degraded_groups_by_symbol():
    md = render_delta(_delta(("src/a.py", CLEAN, SMELLY)), "markdown")
    assert md.startswith("## Code health delta (working tree vs HEAD)")
    assert "**DEGRADED**" in md and "Fix before finishing" in md
    assert "| `src/a.py` | 10.00 |" in md
    # one numbered entry for g, carrying all three new smells and their hints
    entries = re.findall(r"^\d+\. ", md, flags=re.MULTILINE)
    assert len(entries) == 1
    assert "**src/a.py:1** `g`: Deep Nesting (new), Excess Arguments (new)" in md
    assert md.count("   - Fix: ") == 3


def test_delta_markdown_worsened_shows_previous_value():
    deeper = NESTED_ONLY.replace("x -= 1", "if x > 2:\n                        x -= 1")
    md = render_delta(_delta(("a.py", NESTED_ONLY, deeper)), "markdown")
    assert "Deep Nesting (worse, was 4)" in md


def test_delta_markdown_improved_and_unchanged():
    md = render_delta(_delta(("a.py", SMELLY, CLEAN)), "markdown")
    assert "**IMPROVED**" in md and "### Improvements" in md
    assert "fixed Deep Nesting" in md and "Fix before finishing" not in md
    md = render_delta(_delta(("a.py", CLEAN, CLEAN + "\n")), "markdown")
    assert "**UNCHANGED**" in md
    assert "No changed" in render_delta(DeltaResult("/r", "HEAD", "index", []), "markdown")


def test_delta_markdown_caps_findings():
    pairs = [(f"f{i}.py", CLEAN, SMELLY) for i in range(5)]
    md = render_delta(_delta(*pairs), "markdown", ReportOptions(max_findings=2))
    assert len(re.findall(r"^\d+\. ", md, flags=re.MULTILINE)) == 2
    assert "3 more places with regressions" in md


def test_delta_markdown_new_file_and_syntax_warning():
    md = render_delta(_delta(("n.py", None, "def broken(:\n    pass\n")), "markdown")
    assert "| `n.py` | new |" in md
    assert "Syntax errors" in md


def test_delta_json_matches_to_dict():
    result = _delta(("a.py", CLEAN, SMELLY))
    assert json.loads(render_delta(result, "json")) == result.to_dict()


def test_delta_text_hides_improvements_unless_verbose():
    result = _delta(("a.py", SMELLY, NESTED_ONLY))
    text = render_delta(result, "text")
    assert "improvement(s), -v to list" in text
    assert "✓ fixed" in render_delta(result, "text", ReportOptions(verbose=True))


# --- scan markdown -------------------------------------------------------------------


def test_scan_markdown(tree):
    md = render_scan(scan(tree, Config()), "markdown")
    assert md.startswith("## Code health: ")
    assert "3 files" in md and "### Worst files" in md
    assert "| `bad.py` |" in md and "`ok.py`" not in md  # clean files not listed
    assert "### Top refactoring targets" in md
    assert "1. **bad.py:1** `g`:" in md  # bad.py has the most total penalty


def test_scan_markdown_caps_and_clean_tree(tree, tmp_path):
    md = render_scan(scan(tree, Config()), "markdown", ReportOptions(max_findings=1, top=1))
    assert "1 more functions/files with findings" in md
    assert md.count("| `bad") == 1
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "ok.py").write_text(CLEAN)
    assert "No code smells found." in render_scan(scan(clean, Config()), "markdown")


def test_scan_text_and_json(tree):
    result = scan(tree, Config())
    text = render_scan(result, "text")
    assert "project score" in text and "worst 2 files" in text
    data = json.loads(render_scan(result, "json", ReportOptions(functions=True)))
    assert "functions" in data["files"][0]


# --- cli -------------------------------------------------------------------------------


def test_cli_markdown_flags(tree, capsys):
    assert main(["scan", str(tree), "--format", "markdown", "--max-findings", "1"]) == 0
    assert "## Code health" in capsys.readouterr().out

    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    assert main(["delta", "--repo", str(tree), "--format", "markdown"]) == 1
    out = capsys.readouterr().out
    assert "(working tree vs (empty repository))" in out and "**DEGRADED**" in out


# --- scan a single file ---------------------------------------------------------------


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.py").write_text(SMELLY)
    (tmp_path / "Makefile").write_text(".PHONY: test\ntest:\n\tpytest\n")
    return tmp_path


def test_scan_single_file_formats(git_project, capsys):
    path = str(git_project / "pkg" / "bad.py")  # absolute path from anywhere
    assert main(["scan", path]) == 0
    out = capsys.readouterr().out
    assert out.startswith("aihealth scan pkg/bad.py\nfile score: ")  # relative to project
    assert "Deep Nesting" in out

    assert main(["scan", path, "--format", "markdown"]) == 0
    assert capsys.readouterr().out.startswith("## pkg/bad.py: ")

    assert main(["scan", path, "--format", "json", "--functions"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == "pkg/bad.py" and data["functions"][0]["qualname"] == "g"

    assert main(["scan", str(git_project / "Makefile")]) == 0
    assert "file score: 10.00 (healthy)" in capsys.readouterr().out


def test_scan_single_file_uses_project_config(git_project, capsys):
    (git_project / ".aihealth.toml").write_text("[weights]\ndeep_nesting = 0\n")
    assert main(["scan", str(git_project / "pkg" / "bad.py")]) == 0
    assert "Deep Nesting" not in capsys.readouterr().out


def test_scan_single_file_errors_and_skips(git_project, capsys):
    (git_project / "notes.md").write_text("# hi")
    assert main(["scan", str(git_project / "notes.md")]) == 2
    assert "unsupported file type" in capsys.readouterr().err
    assert main(["scan", str(git_project / "missing.py")]) == 2
    assert "no such file or directory" in capsys.readouterr().err
    assert main(["scan", str(git_project / "pkg" / "bad.py"), "--save"]) == 2
    assert "--save needs a directory" in capsys.readouterr().err
    (git_project / "gen.py").write_text("# @generated by protoc\nx = 1\n")
    assert main(["scan", str(git_project / "gen.py")]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "generated file" in captured.err


def test_scan_single_file_fail_under(git_project):
    assert main(["scan", str(git_project / "pkg" / "bad.py"), "--fail-under", "9.99"]) == 1
    assert main(["scan", str(git_project / "Makefile"), "--fail-under", "9.99"]) == 0


# --- code text in markdown ------------------------------------------------------------

# text | markdown code span
CODE_SPANS = """
deploy.sh | `deploy.sh`
grep `x` | `` grep `x` ``
a``b | ```a``b```
`x | `` `x ``
"""


@pytest.mark.parametrize("line", CODE_SPANS.strip().splitlines())
def test_code_spans_survive_backticks(line):
    text, expected = line.split(" | ")
    assert code(text) == expected


def test_table_cells_escape_pipes():
    assert cell(code("a|b.sh")) == "`a\\|b.sh`"


NESTED_SH = (
    "for f in $(ls | grep `echo x`); do\n"
    + "".join("  " * (i + 1) + f"if [ -n x{i} ]; then\n" for i in range(4))
    + "echo\n"
    + "".join("  " * (4 - i) + "fi\n" for i in range(4))
    + "done\n"
)


def test_markdown_keeps_names_from_scanned_code_intact():
    """A shell block is named by its first line: backticks and pipes in it
    (or in a path) must not break code spans or table rows."""
    md = render_delta(_delta(("a|b.sh", "x=1\n", NESTED_SH)), "markdown")
    row = next(line for line in md.splitlines() if line.startswith("| `a"))
    assert row.startswith("| `a\\|b.sh` |")
    assert row.count(" | ") == 4  # five cells: the escaped pipe doesn't split
    assert "``for f in $(ls | grep `echo x`)``" in md
