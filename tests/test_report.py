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
from aihealth.report import ReportOptions, render_delta, render_scan

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
