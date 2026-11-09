"""Full scan reports written to a file: AI-HEALTH.md and ai-health.html."""

from html.parser import HTMLParser
from pathlib import Path

import pytest

from aihealth.cli import main
from aihealth.config import Config
from aihealth.engine import scan
from aihealth.report_doc import ReportMeta, render_report, report_format

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
META = ReportMeta(title="demo", generated="2026-11-09 12:00", commit="abcdef123", branch="main")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src dir").mkdir(parents=True)
    (root / "ok.py").write_text(CLEAN)
    (root / "src dir" / "bad.py").write_text(SMELLY)
    (root / "Makefile").write_text("all:\n\techo hi\n")
    return root


def test_report_format_from_extension():
    assert report_format("AI-HEALTH.md") == "markdown"
    assert report_format("out/ai-health.HTML") == "html"
    with pytest.raises(ValueError, match=r"\.md or \.html"):
        report_format("report.txt")


def test_markdown_report_links_lines_and_lists_every_section(tree):
    md = render_report(scan(tree, Config()), "markdown", META)
    assert md.startswith("# Code health: demo\n")
    assert "commit abcdef1 (main)" in md
    assert "## By language" in md and "## Where the penalty comes from" in md
    # paths are URL-quoted, lines link to GitHub's #L anchors
    assert "[`src dir/bad.py`](src%20dir/bad.py)" in md
    assert "[1](src%20dir/bad.py#L1)" in md
    assert "Deep Nesting" in md and "**How to fix**" in md
    assert "<summary>1 file(s) without findings</summary>" in md


def test_links_are_relative_to_the_report_folder(tree):
    meta = ReportMeta.for_output(tree, tree / "docs" / "AI-HEALTH.md")
    assert meta.href("src dir/bad.py", 4) == "../src%20dir/bad.py#L4"
    assert ReportMeta.for_output(tree, tree / "x.md").href("a.py") == "a.py"


class _Checker(HTMLParser):
    """Collects tags and fails on unbalanced (non-void) tags."""

    VOID = frozenset({"meta", "br", "hr", "img", "link", "input"})

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)
        self.hrefs += [v for k, v in attrs if k == "href"]

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


def test_html_report_is_self_contained_balanced_and_escaped(tree):
    (tree / "a<b>.py").write_text(SMELLY)
    page = render_report(scan(tree, Config()), "html", META)
    checker = _Checker()
    checker.feed(page)
    assert checker.stack == []
    assert "<script" not in page and "http" not in page.split("<footer")[0].lower()
    assert "<code>a&lt;b&gt;.py</code>" in page
    assert "src%20dir/bad.py#L1" in checker.hrefs
    assert "prefers-color-scheme: dark" in page


def test_cli_writes_report_and_rejects_unknown_extension(tree, tmp_path, capsys):
    assert main(["scan", str(tree), "--report"]) == 0
    assert (tree / "AI-HEALTH.md").read_text().startswith("# Code health: proj")
    out = tmp_path / "ai-health.html"
    assert main(["scan", str(tree), "--report", str(out)]) == 0
    assert out.read_text().startswith("<!doctype html>")
    assert main(["scan", str(tree), "--report", str(tmp_path / "r.txt")]) == 2
    assert "must end in .md or .html" in capsys.readouterr().err


def test_cli_report_for_one_file(tree, tmp_path):
    out = tmp_path / "one.md"
    assert main(["scan", str(tree / "src dir" / "bad.py"), "--report", str(out)]) == 0
    assert "Deep Nesting" in out.read_text()
