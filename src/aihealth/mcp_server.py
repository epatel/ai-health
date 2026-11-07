"""MCP server (stdio) exposing aihealth to AI coding agents.

Tools are plain functions (unit-testable without an MCP client) that
``build_server`` registers on an ``MCPServer`` (mcp SDK 2.x). They return markdown by
default (see report.py) or json. Paths are resolved against ``repo`` (or
the server's working directory, which agent hosts set to the project).
"""

# Tool parameters are the MCP JSON schema: primitives and flat argument lists
# are imposed by the protocol, so these smells don't apply here.
# aihealth: disable=primitive_obsession, excess_arguments

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from aihealth import gitutil
from aihealth.config import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS, Config, ConfigError, load_config
from aihealth.delta import DeltaResult, delta_for_contents, run_delta
from aihealth.engine import scan, score_source
from aihealth.report import ReportOptions, render_delta, render_file, render_scan
from aihealth.smells import EXPLANATIONS, HINTS, LABELS

Output = Literal["markdown", "json"]


class ToolInputError(ValueError):
    """Bad tool input (missing file, unsupported language, unknown smell)."""


INSTRUCTIONS = """\
aihealth estimates code health (1-10, CodeScene-style code smells) for Python and Dart/Flutter.
- After editing Python/Dart code, call `review_changes` (working tree vs HEAD). If the verdict \
is DEGRADED, fix the listed new/worsened smells in the code you touched before finishing.
- Before writing a large rewrite of a file, `preview_change` scores the proposed content \
against the current file without touching disk.
- `score_file` shows all smells in one file; `scan_project` lists the worst files and top \
refactoring targets; `explain_smell` explains a smell and how to fix it.
Scores: healthy >= 9, warning 4-9, alert < 4. Fix hints are suggestions; keep behaviour unchanged.
"""


def _base_dir(repo: str | None) -> Path:
    return Path(repo).expanduser().resolve() if repo else Path.cwd()


def _project_root(start: Path) -> Path:
    """Git work tree root containing ``start``, else ``start`` (or its dir)."""
    directory = start if start.is_dir() else start.parent
    try:
        return gitutil.repo_root(directory)
    except gitutil.GitError:
        return directory


def _resolve(path: str, repo: str | None) -> tuple[Path, Path, str]:
    """(absolute file, project root, path relative to root)."""
    p = Path(path).expanduser()
    absolute = (p if p.is_absolute() else _base_dir(repo) / p).resolve()
    root = _project_root(absolute)
    try:
        rel = absolute.relative_to(root).as_posix()
    except ValueError:
        rel = absolute.name
    return absolute, root, rel


def _options(max_findings: int, top: int = 10) -> ReportOptions:
    return ReportOptions(top=top, max_findings=max(1, max_findings))


# --- tools ------------------------------------------------------------------------


def review_changes(
    repo: str = ".",
    paths: list[str] | None = None,
    staged: bool = False,
    base: str = "HEAD",
    output: Output = "markdown",
    max_findings: int = 10,
) -> str:
    """Code health delta of uncommitted changes (working tree incl. untracked
    files, or only staged changes) against `base` (default HEAD). Returns the
    verdict (DEGRADED/IMPROVED/UNCHANGED), before/after scores per changed
    file, and new/worsened smells with locations and fix hints. Optionally
    limit to `paths` (files or directories)."""
    root = gitutil.repo_root(_base_dir(repo))
    config = load_config(root)
    head = gitutil.INDEX if staged else gitutil.WORKTREE
    abs_paths = [str(_base_dir(repo) / p) for p in paths] if paths else None
    result = run_delta(root, config, gitutil.Revisions(base, head), abs_paths)
    return render_delta(result, output, _options(max_findings))


def score_file(
    path: str, repo: str | None = None, output: Output = "markdown", max_findings: int = 10
) -> str:
    """Code health score (1-10) and all code smells of one Python or Dart
    file on disk, grouped per function with fix hints."""
    absolute, root, rel = _resolve(path, repo)
    if not absolute.is_file():
        raise ToolInputError(f"file not found: {absolute}")
    fs = score_source(absolute.read_bytes(), rel, load_config(root))
    if fs is None:
        raise ToolInputError(f"unsupported file type (Python/Dart only): {rel}")
    return render_file(fs, output, _options(max_findings))


def preview_change(
    path: str,
    content: str,
    repo: str | None = None,
    output: Output = "markdown",
    max_findings: int = 10,
) -> str:
    """Score proposed new `content` for `path` against the file's current
    content on disk (or as a new file) WITHOUT writing it. Use before
    writing a large edit to check it doesn't degrade code health."""
    absolute, root, rel = _resolve(path, repo)
    config = load_config(root)
    before = absolute.read_bytes() if absolute.is_file() else None
    if score_source(b"", rel, config) is None:
        raise ToolInputError(f"unsupported file type (Python/Dart only): {rel}")
    fd = delta_for_contents(rel, before, content.encode(), config)
    result = DeltaResult(str(root), "current file", "proposed content", [fd])
    return render_delta(result, output, _options(max_findings))


def scan_project(
    path: str = ".", output: Output = "markdown", top: int = 10, max_findings: int = 10
) -> str:
    """Code health overview of every Python/Dart file under `path`: project
    score (LoC-weighted), worst files, and top refactoring targets."""
    root = _base_dir(path)
    if not root.is_dir():
        raise ToolInputError(f"not a directory: {root}")
    result = scan(root, load_config(root))
    return render_scan(result, output, _options(max_findings, top))


def explain_smell(smell: str) -> str:
    """Explain a code smell reported by aihealth (key like `bumpy_road` or
    label like `Bumpy Road`): what it measures, default threshold, and how
    to fix it."""
    key = _smell_key(smell)
    threshold = DEFAULT_THRESHOLDS.get(key)
    trigger = "" if threshold is None else f"Triggers when the value exceeds {threshold:g}. "
    if key == "brain_method":
        trigger = "Triggers when Large Method, Complex Method and Deep Nesting all apply. "
    if key == "code_duplication":
        dup = Config().section("duplication")
        trigger = (
            f"Triggers for clones of {dup['min_tokens']}+ tokens and {dup['min_lines']}+ lines. "
        )
    return (
        f"## {LABELS[key]} (`{key}`)\n\n{EXPLANATIONS[key]}\n\n"
        f"{trigger}Default weight {DEFAULT_WEIGHTS[key]:g} "
        "(override in `.aihealth.toml` under [thresholds] / [weights]).\n\n"
        f"**How to fix:** {HINTS[key]}"
    )


def _smell_key(smell: str) -> str:
    wanted = smell.strip().lower().replace(" ", "_").replace("-", "_")
    for key, label in LABELS.items():
        if wanted in (key, label.lower().replace(" ", "_")):
            return key
    raise ToolInputError(f"unknown smell {smell!r}; known: {', '.join(sorted(LABELS))}")


TOOLS = (review_changes, score_file, preview_change, scan_project, explain_smell)


def _as_tool_errors(fn, tool_error):
    """Re-raise our input errors as the SDK's ToolError: the SDK reports any
    other exception as a generic crash and hides its message from the agent."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ToolInputError, gitutil.GitError, ConfigError) as e:
            raise tool_error(str(e)) from e

    return wrapper


def build_server():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer("aihealth", instructions=INSTRUCTIONS)
    for tool in TOOLS:
        # markdown/json text for the agent to read; structured output would
        # send the same payload a second time as {"result": ...}
        server.tool(structured_output=False)(_as_tool_errors(tool, ToolError))
    return server


def main() -> None:
    build_server().run()  # stdio transport
