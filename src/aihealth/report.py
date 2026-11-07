"""Render scan and delta results as text (humans), markdown (agents) or json.

The markdown formats are written for an AI coding agent reading tool
output. They lead with the verdict, give `path:line` locations it can
open, put the fix hint next to each finding, and are capped so they don't
flood its context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aihealth.scoring import band

if TYPE_CHECKING:
    from aihealth.delta import Change, DeltaResult, FileDelta
    from aihealth.engine import ScanResult
    from aihealth.scoring import FileScore
    from aihealth.smells import Finding

FORMATS = ("text", "markdown", "json")


@dataclass(frozen=True)
class ReportOptions:
    top: int = 10  # files listed in scan output
    max_findings: int = 10  # findings listed in markdown
    verbose: bool = False  # text: list every finding / improvement
    functions: bool = False  # json scan: include per-function metrics


def render_scan(result: ScanResult, fmt: str, opts: ReportOptions | None = None) -> str:
    opts = opts or ReportOptions()
    if fmt == "json":
        return json.dumps(result.to_dict(with_functions=opts.functions), indent=2)
    if fmt == "markdown":
        return scan_markdown(result, opts)
    return scan_text(result, opts)


def render_delta(result: DeltaResult, fmt: str, opts: ReportOptions | None = None) -> str:
    opts = opts or ReportOptions()
    if fmt == "json":
        return json.dumps(result.to_dict(), indent=2)
    if fmt == "markdown":
        return delta_markdown(result, opts)
    return delta_text(result, opts)


def render_file(fs: FileScore, fmt: str, opts: ReportOptions | None = None) -> str:
    """One file's score and findings (MCP score_file)."""
    opts = opts or ReportOptions()
    if fmt == "json":
        return json.dumps(fs.to_dict(with_functions=opts.functions), indent=2)
    out = [
        f"## {fs.path}: {_fmt_score(fs.score)}",
        "",
        f"{fs.loc} LoC, {len(fs.findings)} finding(s)",
    ]
    if fs.parse_errors:
        out.append(f"⚠ {fs.parse_errors} syntax error(s): score may be partial.")
    groups = _group_by_symbol(fs.findings)
    if not groups:
        return "\n".join([*out, "", "No code smells found."])
    shown = groups[: opts.max_findings]
    out += ["", *_numbered(shown, lambda f: f.label)]
    out += _more(len(groups), len(shown), "functions with findings")
    return "\n".join(out)


# --- shared helpers -------------------------------------------------------------


def _fmt_score(score: float | None) -> str:
    return "n/a" if score is None else f"{score:.2f} ({band(score)})"


def _band_counts(result: ScanResult) -> dict[str, int]:
    counts = {"healthy": 0, "warning": 0, "alert": 0}
    for f in result.files:
        counts[f.band] += 1
    return counts


def _by_penalty(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (-f.penalty, f.line))


def _symbol(f: Finding) -> str:
    return f" `{f.symbol}`" if f.symbol else ""


def _more(total: int, shown: int, what: str) -> list[str]:
    return [f"_…and {total - shown} more {what}._"] if total > shown else []


# --- scan -----------------------------------------------------------------------------


def _worst_files(result: ScanResult, top: int):
    files = sorted(result.files, key=lambda f: (f.score, -f.loc))
    return [f for f in files if f.findings][:top]


def scan_text(result: ScanResult, opts: ReportOptions) -> str:
    counts = _band_counts(result)
    out = [
        f"aihealth scan {result.root}",
        (
            f"project score: {_fmt_score(result.score)}  "
            f"({len(result.files)} files, {result.loc} LoC, LoC-weighted)"
        ),
        f"files: {counts['healthy']} healthy, {counts['warning']} warning, {counts['alert']} alert",
    ]
    worst = _worst_files(result, opts.top)
    if not worst:
        return "\n".join([*out, "", "no code smells found"])
    out += ["", f"worst {len(worst)} files:"]
    for fs in worst:
        out += ["", f"  {fs.score:5.2f}  {fs.path}  ({fs.loc} LoC)"]
        findings = _by_penalty(fs.findings)
        shown = findings if opts.verbose else findings[:5]
        out += [f"         L{f.line:<5} {f.label}: {f.message}" for f in shown]
        if len(findings) > len(shown):
            out.append(f"         ... {len(findings) - len(shown)} more (use -v)")
    return "\n".join(out)


def scan_markdown(result: ScanResult, opts: ReportOptions) -> str:
    counts = _band_counts(result)
    out = [
        f"## Code health: {_fmt_score(result.score)}",
        "",
        (
            f"{len(result.files)} files · {result.loc} LoC · {counts['healthy']} healthy / "
            f"{counts['warning']} warning / {counts['alert']} alert "
            "(scores 1-10, LoC-weighted; healthy ≥ 9, alert < 4)"
        ),
    ]
    worst = _worst_files(result, opts.top)
    if not worst:
        return "\n".join([*out, "", "No code smells found."])
    out += [
        "",
        "### Worst files",
        "",
        "| Score | File | LoC | Main issues |",
        "|---:|---|---:|---|",
    ]
    for fs in worst:
        labels = list(dict.fromkeys(f.label for f in _by_penalty(fs.findings)))
        issues = ", ".join(labels[:3]) + (", …" if len(labels) > 3 else "")
        out.append(f"| {fs.score:.2f} | `{fs.path}` | {fs.loc} | {issues} |")
    targets = _group_by_symbol([f for fs in result.files for f in fs.findings])
    shown = targets[: opts.max_findings]
    out += ["", "### Top refactoring targets", ""]
    out += _numbered(shown, lambda f: f.label)
    out += _more(len(targets), len(shown), "functions/files with findings (use --max-findings)")
    return "\n".join(out)


def _group_by_symbol(findings: list[Finding]) -> list[list[Finding]]:
    """One group per (file, symbol), most total penalty first; file-level
    findings (no symbol) stay one per group."""
    groups: dict[tuple, list[Finding]] = {}
    for f in findings:
        key = (f.path, f.symbol) if f.symbol else (f.path, f.smell, f.line)
        groups.setdefault(key, []).append(f)
    ordered = [_by_penalty(g) for g in groups.values()]
    return sorted(ordered, key=lambda g: (-sum(f.penalty for f in g), g[0].path, g[0].line))


def _numbered(groups: list[list[Finding]], label) -> list[str]:
    """Numbered entries: location, symbol, smells; messages; up to 3 distinct fix hints."""
    out = []
    for i, group in enumerate(groups, 1):
        top = group[0]
        line = min(f.line for f in group)
        labels = ", ".join(label(f) for f in group)
        out.append(f"{i}. **{top.path}:{line}**{_symbol(top)}: {labels}")
        out += [f"   - {f.message}" for f in group]
        hints = list(dict.fromkeys(f.hint for f in group))[:3]
        out += [f"   - Fix: {h}" for h in hints]
    return out


# --- delta ------------------------------------------------------------------------------

_HEAD_LABEL = {"worktree": "working tree", "index": "staged changes"}
_MARK = {
    "new": "+ new     ",
    "worsened": "↑ worse   ",
    "improved": "↓ better  ",
    "fixed": "✓ fixed   ",
}


def _compared(result: DeltaResult) -> str:
    head = _HEAD_LABEL.get(result.head, result.head)
    return f"{head} vs {result.base or '(empty repository)'}"


def _score_label(fd: FileDelta) -> str:
    if fd.score_before is None:
        return f"new file, {fd.score_after:.2f}"
    return f"{fd.score_before:.2f} → {fd.score_after:.2f} ({fd.score_delta:+.2f})"


def delta_text(result: DeltaResult, opts: ReportOptions) -> str:
    out = [f"aihealth delta: {_compared(result)}: {result.verdict.upper()}"]
    if not result.files:
        out.append("no changed Python/Dart files")
    for fd in result.files:
        out += _file_delta_text(fd, opts.verbose)
    return "\n".join(out)


def _file_delta_text(fd: FileDelta, verbose: bool) -> list[str]:
    rename = f" (from {fd.old_path})" if fd.status == "renamed" else ""
    out = ["", f"  {fd.verdict:9} {fd.path}{rename}  {_score_label(fd)}"]
    if fd.after and fd.after.parse_errors:
        out.append(f"            ! {fd.after.parse_errors} syntax error(s): results may be partial")
    shown = fd.changes if verbose else fd.of_kind("new", "worsened")
    for ch in shown:
        out += _change_text(ch)
    hidden = len(fd.changes) - len(shown)
    if hidden:
        out.append(f"    ({hidden} improvement(s), -v to list)")
    return out


def _was(ch: Change) -> str:
    return f" (was {ch.value_before:g})" if ch.kind in ("worsened", "improved") else ""


def _change_text(ch: Change) -> list[str]:
    f = ch.finding
    line = "" if ch.kind == "fixed" else f"L{f.line}"
    out = [f"    {_MARK[ch.kind]}{line:6} {f.label}: {f.message}{_was(ch)}"]
    if ch.kind in ("new", "worsened"):
        out.append(f"                     → {f.hint}")
    return out


_VERDICT_LINE = {
    "degraded": "**DEGRADED**: this change makes the code harder to maintain. "
    "Fix the issues below before finishing.",
    "improved": "**IMPROVED**: this change makes the code healthier.",
    "unchanged": "**UNCHANGED**: no code health regressions.",
}


def delta_markdown(result: DeltaResult, opts: ReportOptions) -> str:
    out = [f"## Code health delta ({_compared(result)})", "", _VERDICT_LINE[result.verdict]]
    if not result.files:
        return "\n".join([*out, "", "No changed Python/Dart files."])
    out += ["", "| File | Before | After | Δ | Verdict |", "|---|---:|---:|---:|---|"]
    out += [_delta_row(fd) for fd in result.files]
    out += _partial_parse_note(result)
    out += _regressions_section(result, opts.max_findings)
    out += _improvements_section(result, opts.max_findings)
    return "\n".join(out)


def _regressions_section(result: DeltaResult, limit: int) -> list[str]:
    bad = [ch for fd in result.files for ch in fd.of_kind("new", "worsened")]
    if not bad:
        return []
    bad.sort(key=lambda ch: (ch.kind != "new", -ch.finding.penalty))
    groups = _group_by_symbol([ch.finding for ch in bad])
    shown = groups[:limit]
    out = ["", "### Fix before finishing", ""]
    out += _numbered(shown, _change_label(bad))
    return out + _more(len(groups), len(shown), "places with regressions (use --max-findings)")


def _improvements_section(result: DeltaResult, limit: int) -> list[str]:
    good = [ch for fd in result.files for ch in fd.of_kind("fixed", "improved")]
    if not good:
        return []
    shown = good[:limit]
    out = ["", "### Improvements", ""]
    out += [
        f"- `{ch.finding.path}`{_symbol(ch.finding)}: {ch.kind} {ch.finding.label}" for ch in shown
    ]
    return out + _more(len(good), len(shown), "improvements")


def _delta_row(fd: FileDelta) -> str:
    before = "new" if fd.score_before is None else f"{fd.score_before:.2f}"
    delta = "" if fd.score_delta is None else f"{fd.score_delta:+.2f}"
    return f"| `{fd.path}` | {before} | {fd.score_after:.2f} | {delta} | {fd.verdict} |"


def _partial_parse_note(result: DeltaResult) -> list[str]:
    broken = [fd.path for fd in result.files if fd.after and fd.after.parse_errors]
    if not broken:
        return []
    return ["", f"⚠ Syntax errors in {', '.join(f'`{p}`' for p in broken)}: scores may be partial."]


def _change_label(changes: list[Change]):
    """Label function for a finding: 'Deep Nesting (new)' / '(worse, was 4)'."""
    by_finding = {id(ch.finding): ch for ch in changes}

    def label(f: Finding) -> str:
        ch = by_finding[id(f)]
        if ch.kind == "new":
            return f"{f.label} (new)"
        return f"{f.label} (worse, was {ch.value_before:g})"

    return label
