"""Claude Code hooks: feed code health back to the agent automatically.

`aihealth hook post-edit` (PostToolUse on Edit|Write): delta of the edited
file vs HEAD; if it degraded, return the regressions as `additionalContext`
(shown to Claude next to the tool result, not as an error). Silent otherwise.

`aihealth hook stop` (Stop): delta of all uncommitted changes; if degraded,
return Stop `additionalContext`, which continues the turn once so Claude can
fix the regressions (or say why they are intended). Never fires when
`stop_hook_active` is set, so it cannot loop.

Both read the hook JSON from stdin and fail open: any problem (not a git
repo, unsupported file, internal error) means no output and exit 0.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TypedDict

from aihealth import gitutil
from aihealth.config import load_config
from aihealth.delta import DEGRADED, run_delta
from aihealth.lang import is_supported
from aihealth.report import ReportOptions, render_delta

POST_EDIT_MAX_FINDINGS = 5
STOP_MAX_FINDINGS = 10

STOP_PREAMBLE = (
    "aihealth: the uncommitted changes make code health worse (details below). "
    "Before finishing, fix the new/worsened smells in code you changed this session. "
    "If a regression is intended or was already there before you started, say so "
    "briefly and finish."
)


class HookPayload(TypedDict, total=False):
    """The fields of Claude Code's hook JSON this module reads."""

    cwd: str
    tool_input: dict[str, Any]  # Edit/Write: {"file_path": absolute path, ...}
    stop_hook_active: bool


def _cwd(payload: HookPayload) -> Path:
    return Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _degraded_markdown(cwd: Path, paths: list[str] | None, max_findings: int) -> str | None:
    root = gitutil.repo_root(cwd)
    result = run_delta(root, load_config(root), gitutil.Revisions(), paths)
    if result.verdict != DEGRADED:
        return None
    return render_delta(result, "markdown", ReportOptions(max_findings=max_findings))


def _context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def post_edit(payload: HookPayload) -> dict | None:
    """Regressions in the file just edited, or None."""
    path = (payload.get("tool_input") or {}).get("file_path")
    if not path or not is_supported(path):
        return None
    report = _degraded_markdown(_cwd(payload), [path], POST_EDIT_MAX_FINDINGS)
    return _context("PostToolUse", report) if report else None


def stop(payload: HookPayload) -> dict | None:
    """Regressions across all uncommitted changes, or None. Never re-fires."""
    if payload.get("stop_hook_active"):
        return None
    report = _degraded_markdown(_cwd(payload), None, STOP_MAX_FINDINGS)
    return _context("Stop", f"{STOP_PREAMBLE}\n\n{report}") if report else None


HANDLERS = {"post-edit": post_edit, "stop": stop}


def run(event: str, stdin_text: str) -> str:
    """Hook entry point: JSON to print (possibly empty). Never raises."""
    try:
        payload = json.loads(stdin_text or "{}")
        out = HANDLERS[event](payload)
    except Exception:  # noqa: BLE001 - a health check must never break editing
        return ""
    return json.dumps(out) if out else ""
