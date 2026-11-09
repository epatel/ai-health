"""Full scan reports saved as a file: AI-HEALTH.md (GitHub) or ai-health.html.

Unlike the capped agent output in `report.py`, a report lists every file
with findings, every finding with its fix hint, a breakdown by language and
by smell, and links each path and line to the source. Links are relative to
the report's own folder, so they work on GitHub (`path#L12` jumps to the
line) and when the HTML file is opened from disk in a browser.

The HTML page is self-contained: inline CSS (light and dark), no scripts,
no network requests.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from aihealth import __version__
from aihealth.report import cell, code
from aihealth.scoring import ALERT, HEALTHY, WARNING, band, project_score

if TYPE_CHECKING:
    from aihealth.engine import ScanResult
    from aihealth.scoring import FileScore
    from aihealth.smells import Finding

REPORT_FORMATS = {".md": "markdown", ".markdown": "markdown", ".html": "html", ".htm": "html"}
_BANDS = (HEALTHY, WARNING, ALERT)
_DOT = {HEALTHY: "🟢", WARNING: "🟡", ALERT: "🔴"}


def report_format(path: str | Path) -> str:
    """'markdown' or 'html' from the file extension; ValueError otherwise."""
    fmt = REPORT_FORMATS.get(Path(path).suffix.lower())
    if fmt is None:
        raise ValueError(f"report must end in .md or .html: {path}")
    return fmt


@dataclass(frozen=True)
class ReportMeta:
    """What the report says about its origin, and where its links point."""

    title: str
    generated: str
    link_base: str = ""  # scan root relative to the report's folder ("" = same)
    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    version: str = __version__

    @classmethod
    def for_output(cls, root: str | Path, out: str | Path) -> ReportMeta:
        from aihealth.store.scans import git_info

        root, out = Path(root).resolve(), Path(out).resolve()
        rel = Path(os.path.relpath(root, out.parent)).as_posix()
        git = git_info(root)
        return cls(
            title=root.name,
            generated=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            link_base="" if rel == "." else rel + "/",
            commit=git.commit,
            branch=git.branch,
            dirty=git.dirty,
        )

    def href(self, path: str, line: int | None = None) -> str:
        anchor = f"#L{line}" if line else ""
        return quote(self.link_base + path) + anchor

    def origin(self) -> str:
        parts = [f"Generated {self.generated} by aihealth {self.version}"]
        if self.commit:
            where = ", ".join(filter(None, [self.branch, self.dirty and "uncommitted changes"]))
            parts.append(f"commit {self.commit[:7]}" + (f" ({where})" if where else ""))
        return " · ".join(parts)


def render_report(result: ScanResult, fmt: str, meta: ReportMeta) -> str:
    data = _ReportData(result)
    if fmt == "html":
        return _HtmlReport(data, meta).render()
    return _MarkdownReport(data, meta).render()


# --- data shaping (shared by both formats) -------------------------------------


@dataclass(frozen=True)
class _Group:
    """A row of a breakdown table: files by language, or findings by smell."""

    name: str
    count: int
    loc: int = 0
    score: float | None = None
    penalty: float = 0.0


class _ReportData:
    def __init__(self, result: ScanResult):
        self.result = result
        self.score = result.score
        ordered = sorted(result.files, key=lambda f: (f.score, -f.loc, f.path))
        self.flagged = [f for f in ordered if f.findings]
        self.clean = sorted((f for f in result.files if not f.findings), key=lambda f: f.path)

    def band_files(self) -> dict[str, int]:
        counts = dict.fromkeys(_BANDS, 0)
        for f in self.result.files:
            counts[f.band] += 1
        return counts

    def band_loc(self) -> dict[str, int]:
        loc = dict.fromkeys(_BANDS, 0)
        for f in self.result.files:
            loc[f.band] += f.loc
        return loc

    def languages(self) -> list[_Group]:
        by_lang: dict[str, list[FileScore]] = {}
        for f in self.result.files:
            by_lang.setdefault(f.language, []).append(f)
        groups = [
            _Group(lang, len(fs), sum(f.loc for f in fs), project_score(fs))
            for lang, fs in by_lang.items()
        ]
        return sorted(groups, key=lambda g: (-g.loc, g.name))

    def smells(self) -> list[_Group]:
        by_smell: dict[str, list[Finding]] = {}
        for f in self.result.files:
            for finding in f.findings:
                by_smell.setdefault(finding.label, []).append(finding)
        groups = [
            _Group(label, len(fs), penalty=round(sum(f.penalty for f in fs), 2))
            for label, fs in by_smell.items()
        ]
        return sorted(groups, key=lambda g: (-g.penalty, g.name))


def _ordered(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (f.line, -f.penalty))


def _main_issues(fs: FileScore, limit: int = 3) -> str:
    labels = list(dict.fromkeys(f.label for f in sorted(fs.findings, key=lambda f: -f.penalty)))
    return ", ".join(labels[:limit]) + (", …" if len(labels) > limit else "")


def _hints(fs: FileScore) -> dict[str, str]:
    """One fix hint per smell label (the first finding's)."""
    hints: dict[str, str] = {}
    for f in _ordered(fs.findings):
        hints.setdefault(f.label, f.hint)
    return hints


def _score(score: float | None) -> str:
    return "n/a" if score is None else f"{score:.2f}"


def _band(score: float | None) -> str:
    return "n/a" if score is None else band(score)


# --- markdown (GitHub) -----------------------------------------------------------


class _Report:
    """Both formats render the same sections in the same order."""

    SECTIONS = (
        "_header",
        "_languages",
        "_smells",
        "_file_table",
        "_file_details",
        "_clean_files",
        "_skipped",
    )

    def __init__(self, data: _ReportData, meta: ReportMeta):
        self.data, self.meta = data, meta

    def sections(self) -> list[str]:
        rendered = (getattr(self, name)() for name in self.SECTIONS)
        return [s for s in rendered if s]


class _MarkdownReport(_Report):
    def render(self) -> str:
        return "\n\n".join(self.sections()) + "\n"

    def link(self, path: str, line: int | None = None, text: str | None = None) -> str:
        return f"[{text or code(path)}]({self.meta.href(path, line)})"

    def _header(self) -> str:
        d = self.data
        files, loc = d.band_files(), d.band_loc()
        dot = _DOT.get(_band(d.score), "")
        rows = [f"| {_DOT[b]} {b} | {files[b]} | {loc[b]} |" for b in _BANDS if files[b] or loc[b]]
        return "\n".join(
            [
                f"# Code health: {cell(self.meta.title)}",
                "",
                f"## {dot} {_score(d.score)} / 10 · {_band(d.score)}",
                "",
                (
                    f"{len(d.result.files)} files · {d.result.loc} lines of code · "
                    "scores 1–10, weighted by lines of code; healthy ≥ 9, alert < 4"
                ),
                "",
                "| Band | Files | LoC |",
                "|---|---:|---:|",
                *rows,
                "",
                f"_{self.meta.origin()}_",
            ]
        )

    def _languages(self) -> str:
        groups = self.data.languages()
        if len(groups) < 2:
            return ""
        rows = [f"| {g.name} | {g.count} | {g.loc} | {_score(g.score)} |" for g in groups]
        head = ["## By language", "", "| Language | Files | LoC | Score |", "|---|---:|---:|---:|"]
        return "\n".join(head + rows)

    def _smells(self) -> str:
        groups = self.data.smells()
        if not groups:
            return ""
        rows = [f"| {g.name} | {g.count} | {g.penalty:g} |" for g in groups]
        head = [
            "## Where the penalty comes from",
            "",
            "| Smell | Findings | Penalty |",
            "|---|---:|---:|",
        ]
        return "\n".join(head + rows)

    def _file_table(self) -> str:
        flagged = self.data.flagged
        if not flagged:
            return "## Files\n\nNo code smells found."
        rows = [
            f"| {_DOT[f.band]} {f.score:.2f} | {cell(self.link(f.path))} | {f.loc} | "
            f"{len(f.findings)} | {_main_issues(f)} |"
            for f in flagged
        ]
        head = [
            f"## Files with findings ({len(flagged)})",
            "",
            "| Score | File | LoC | Findings | Main issues |",
            "|---:|---|---:|---:|---|",
        ]
        return "\n".join(head + rows)

    def _file_details(self) -> str:
        if not self.data.flagged:
            return ""
        return "\n\n".join(["## Findings", *(self._file(f) for f in self.data.flagged)])

    def _file(self, fs: FileScore) -> str:
        out = [
            f"### {_DOT[fs.band]} {fs.path} · {fs.score:.2f}",
            "",
            f"{self.link(fs.path, text='Open file')} · {fs.loc} LoC · {len(fs.findings)} finding(s)",
        ]
        if fs.parse_errors:
            out += ["", f"> ⚠ {fs.parse_errors} syntax error(s): the score may be partial."]
        out += ["", "| Line | Symbol | Smell | Detail |", "|---:|---|---|---|"]
        out += [self._finding_row(f) for f in _ordered(fs.findings)]
        hints = [f"- **{label}**: {hint}" for label, hint in _hints(fs).items()]
        return "\n".join([*out, "", "**How to fix**", "", *hints])

    def _finding_row(self, f: Finding) -> str:
        line = self.link(f.path, f.line, str(f.line))
        symbol = cell(code(f.symbol)) if f.symbol else ""
        return f"| {line} | {symbol} | {f.label} | {cell(f.message)} |"

    def _clean_files(self) -> str:
        clean = self.data.clean
        if not clean:
            return ""
        items = [f"- {self.link(f.path)} ({f.loc} LoC)" for f in clean]
        summary = f"{len(clean)} file(s) without findings"
        return "\n".join(["<details>", f"<summary>{summary}</summary>", "", *items, "</details>"])

    def _skipped(self) -> str:
        skipped = self.data.result.skipped
        if not skipped:
            return ""
        items = [f"- {code(path)}: {reason}" for path, reason in skipped]
        summary = f"{len(skipped)} file(s) not scored"
        return "\n".join(["<details>", f"<summary>{summary}</summary>", "", *items, "</details>"])


# --- html (browser) ----------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f7f7f5; --surface: #ffffff; --text: #1d1d1f; --muted: #6b6b70;
  --border: #e2e2e0; --link: #2457c5;
  --healthy: #1f8a4c; --warning: #b7791f; --alert: #c53030;
  --healthy-bg: #e6f4ec; --warning-bg: #fbf1de; --alert-bg: #fbe5e5;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161618; --surface: #1f1f22; --text: #ececee; --muted: #9a9aa2;
    --border: #2f2f34; --link: #7aa5ff;
    --healthy: #4cc27e; --warning: #e0a94a; --alert: #f07171;
    --healthy-bg: #173324; --warning-bg: #3a2d14; --alert-bg: #3d1c1c;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 40px 0 12px; }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
code { font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
.muted { color: var(--muted); }
.hero { display: flex; flex-wrap: wrap; gap: 24px; align-items: center; margin: 20px 0; }
.big { font-size: 48px; font-weight: 700; line-height: 1; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 16px; min-width: 110px; }
.tile b { display: block; font-size: 22px; }
.bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: 8px 0 4px;
  background: var(--border); }
.bar span { display: block; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-weight: 600;
  font-size: 13px; white-space: nowrap; }
.healthy { color: var(--healthy); } .warning { color: var(--warning); } .alert { color: var(--alert); }
.badge.healthy { background: var(--healthy-bg); } .badge.warning { background: var(--warning-bg); }
.badge.alert { background: var(--alert-bg); }
.fill-healthy { background: var(--healthy); } .fill-warning { background: var(--warning); }
.fill-alert { background: var(--alert); }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; background: var(--surface);
  border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
th, td { padding: 7px 12px; text-align: left; border-bottom: 1px solid var(--border);
  vertical-align: top; overflow-wrap: anywhere; }
th { font-size: 13px; color: var(--muted); font-weight: 600; }
tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap;
  overflow-wrap: normal; }
details.file { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; margin: 10px 0; }
details.file > summary { cursor: pointer; padding: 10px 14px; display: flex; gap: 12px;
  align-items: center; flex-wrap: wrap; }
details.file > div { padding: 0 14px 14px; }
details.file table { border: none; }
ul.hints { margin: 8px 0 0; padding-left: 20px; }
ul.plain { columns: 2 320px; padding-left: 20px; }
footer { margin-top: 48px; font-size: 13px; }
"""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _badge(score: float | None) -> str:
    b = _band(score)
    return f'<span class="badge {b}">{_score(score)}</span>'


def _table(head: list[str], rows: list[str], numeric: set[int]) -> str:
    ths = "".join(
        f'<th class="num">{_e(h)}</th>' if i in numeric else f"<th>{_e(h)}</th>"
        for i, h in enumerate(head)
    )
    body = "\n".join(rows)
    return f'<div class="scroll"><table><thead><tr>{ths}</tr></thead>\n<tbody>\n{body}\n</tbody></table></div>'


def _tr(cells: list[str], numeric: set[int]) -> str:
    tds = "".join(
        f'<td class="num">{c}</td>' if i in numeric else f"<td>{c}</td>"
        for i, c in enumerate(cells)
    )
    return f"<tr>{tds}</tr>"


class _HtmlReport(_Report):
    def render(self) -> str:
        body = "\n".join(self.sections())
        title = f"Code health: {self.meta.title}"
        return (
            '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n<main>\n"
            f'{body}\n<footer class="muted">{_e(self.meta.origin())}</footer>\n'
            "</main>\n</body>\n</html>\n"
        )

    def link(self, path: str, line: int | None = None, text: str | None = None) -> str:
        label = _e(text) if text else f"<code>{_e(path)}</code>"
        return f'<a href="{_e(self.meta.href(path, line))}">{label}</a>'

    def _header(self) -> str:
        d = self.data
        b = _band(d.score)
        files = d.band_files()
        tiles = [
            ("Files", len(d.result.files)),
            ("Lines of code", d.result.loc),
            *((name.capitalize(), files[name]) for name in _BANDS),
        ]
        tile_html = "".join(f'<div class="tile"><b>{v}</b>{_e(k)}</div>' for k, v in tiles)
        return (
            f"<h1>Code health: {_e(self.meta.title)}</h1>\n"
            f'<p class="muted">{_e(self.meta.origin())}</p>\n'
            f'<div class="hero"><div><div class="big {b}">{_score(d.score)}</div>'
            f'<div class="muted">of 10 · {_e(b)}</div></div>'
            f'<div class="tiles">{tile_html}</div></div>\n'
            f"{self._loc_bar()}\n"
            '<p class="muted">Scores 1–10, weighted by lines of code; '
            "healthy ≥ 9, warning 4–9, alert &lt; 4.</p>"
        )

    def _loc_bar(self) -> str:
        loc = self.data.band_loc()
        total = sum(loc.values()) or 1
        spans = "".join(
            f'<span class="fill-{b}" style="width:{100 * loc[b] / total:.2f}%"></span>'
            for b in _BANDS
        )
        legend = "".join(
            f'<span><span class="{b}">●</span> {_e(b)}: {loc[b]} LoC '
            f"({100 * loc[b] / total:.0f}%)</span>"
            for b in _BANDS
        )
        return f'<div class="bar">{spans}</div><div class="legend muted">{legend}</div>'

    def _languages(self) -> str:
        groups = self.data.languages()
        if len(groups) < 2:
            return ""
        num = {1, 2, 3}
        rows = [_tr([_e(g.name), str(g.count), str(g.loc), _badge(g.score)], num) for g in groups]
        return "<h2>By language</h2>\n" + _table(["Language", "Files", "LoC", "Score"], rows, num)

    def _smells(self) -> str:
        groups = self.data.smells()
        if not groups:
            return ""
        num = {1, 2}
        rows = [_tr([_e(g.name), str(g.count), f"{g.penalty:g}"], num) for g in groups]
        head = ["Smell", "Findings", "Penalty"]
        return "<h2>Where the penalty comes from</h2>\n" + _table(head, rows, num)

    def _file_table(self) -> str:
        flagged = self.data.flagged
        if not flagged:
            return "<h2>Files</h2>\n<p>No code smells found.</p>"
        num = {0, 2, 3}
        rows = [
            _tr(
                [
                    _badge(f.score),
                    f'<a href="#{_anchor(f.path)}"><code>{_e(f.path)}</code></a>',
                    str(f.loc),
                    str(len(f.findings)),
                    _e(_main_issues(f)),
                ],
                num,
            )
            for f in flagged
        ]
        head = ["Score", "File", "LoC", "Findings", "Main issues"]
        return f"<h2>Files with findings ({len(flagged)})</h2>\n" + _table(head, rows, num)

    def _file_details(self) -> str:
        if not self.data.flagged:
            return ""
        return "<h2>Findings</h2>\n" + "\n".join(self._file(f) for f in self.data.flagged)

    def _file(self, fs: FileScore) -> str:
        num = {0}
        rows = [
            _tr(
                [
                    self.link(f.path, f.line, str(f.line)),
                    f"<code>{_e(f.symbol)}</code>" if f.symbol else "",
                    _e(f.label),
                    _e(f.message),
                ],
                num,
            )
            for f in _ordered(fs.findings)
        ]
        hints = "".join(f"<li><b>{_e(k)}</b>: {_e(v)}</li>" for k, v in _hints(fs).items())
        warn = (
            f'<p class="alert">⚠ {fs.parse_errors} syntax error(s): the score may be partial.</p>'
            if fs.parse_errors
            else ""
        )
        return (
            f'<details class="file" id="{_anchor(fs.path)}" open>\n'
            f"<summary>{_badge(fs.score)} <code>{_e(fs.path)}</code>"
            f'<span class="muted">{fs.loc} LoC · {len(fs.findings)} finding(s)</span>'
            f"{self.link(fs.path, text='open')}</summary>\n<div>{warn}"
            + _table(["Line", "Symbol", "Smell", "Detail"], rows, num)
            + f'<p><b>How to fix</b></p><ul class="hints">{hints}</ul></div>\n</details>'
        )

    def _clean_files(self) -> str:
        clean = self.data.clean
        if not clean:
            return ""
        items = "".join(
            f'<li>{self.link(f.path)} <span class="muted">{f.loc} LoC</span></li>' for f in clean
        )
        return (
            f"<h2>Files without findings ({len(clean)})</h2>\n"
            f'<details><summary>Show all</summary><ul class="plain">{items}</ul></details>'
        )

    def _skipped(self) -> str:
        skipped = self.data.result.skipped
        if not skipped:
            return ""
        items = "".join(f"<li><code>{_e(p)}</code>: {_e(r)}</li>" for p, r in skipped)
        return (
            f"<h2>Not scored ({len(skipped)})</h2>\n"
            f'<details><summary>Show all</summary><ul class="plain">{items}</ul></details>'
        )


def _anchor(path: str) -> str:
    return "f-" + quote(path, safe="").replace("%", "_")
