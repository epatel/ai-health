# aihealth

Code health estimates (1–10, CodeScene-style) for **Python** and **Dart/Flutter**, built as
feedback for AI coding agents.

- **delta**: did this change make the code healthier or worse? Verdict, score before → after,
  new/worsened smells with `path:line` and fix hints. Exit code 1 when health degraded.
- **scan**: overview of a codebase. Project score, worst files, top refactoring targets.
- **MCP server**: the same, as tools an agent calls itself.

Scores come from 13 explainable code smells: Brain Method, Large/Complex Method, Cognitive
Complexity, Deep Nesting, Bumpy Road, Excess Arguments, Complex Conditional, Deep Widget
Tree (Flutter), Large File, Low Cohesion, Primitive Obsession and Code Duplication. Bands:
healthy ≥ 9, warning 4–9, alert < 4.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e .     # or: make setup
```

## CLI

```bash
aihealth delta                      # working tree (incl. untracked) vs HEAD
aihealth delta --staged             # staged changes only
aihealth delta --base main --head HEAD lib/   # a commit range, limited to lib/
aihealth delta --format markdown    # agent-oriented output (also: text, json)

aihealth scan path/to/repo --format markdown --max-findings 20
aihealth scan . --fail-under 7      # CI gate on the project score
```

## History

```bash
aihealth scan . --save     # store in .aihealth/health.db (self-gitignored)
aihealth history           # score per saved scan, Δ, config changes
```

## Dashboard

```bash
aihealth serve --repo path/to/repo     # http://127.0.0.1:8765
```

It shows an overview (score, trend, where the penalty comes from, worst files), a treemap
(area = lines of code, colour = health), and per-file findings and function metrics. "Scan now"
runs and saves a scan. Charts load ECharts from a CDN.

## MCP server (Claude Code and other agents)

Register it for a project (run inside that project; the server analyses its working directory):

```bash
claude mcp add aihealth -- /path/to/ai-health/.venv/bin/aihealth mcp
```

Tools:

| Tool | Use |
|---|---|
| `review_changes` | Delta of uncommitted (or `staged`) changes vs `base`. Call after editing |
| `preview_change` | Score proposed `content` for a file against the current file without writing it |
| `score_file` | All smells in one file |
| `scan_project` | Project overview |
| `explain_smell` | What a smell measures and how to fix it |

All tools return markdown by default (`output: "json"` for machine use).

## Automatic feedback in Claude Code (hooks)

Instead of relying on the agent to remember, let Claude Code run aihealth itself. Add to the
project's `.claude/settings.json` (use the absolute path to your aihealth install):

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [{ "type": "command", "command": "/path/to/ai-health/.venv/bin/aihealth hook post-edit" }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "/path/to/ai-health/.venv/bin/aihealth hook stop" }] }
    ]
  }
}
```

- **post-edit**: after each edit of a Python/Dart file, checks that file against HEAD. Silent
  unless it got worse; then Claude gets the regressions with fix hints next to the edit result.
- **stop**: when Claude is about to finish, checks all uncommitted changes. If health degraded,
  Claude gets one more turn to fix them (or to say why they are intended). It never fires twice
  in a row, so it cannot loop.
- Both fail open: outside a git repo, for other languages, or on any error they do nothing.

## Configuration

Optional `.aihealth.toml` at the repository root (all keys in `src/aihealth/config.py`):

```toml
[thresholds]
large_method = 100

[weights]
primitive_obsession = 0          # 0 disables a smell

[scan]
exclude = ["legacy/*"]           # added to defaults (*.g.dart, *.freezed.dart, build/, ...)

[dart]
test_closure_calls = ["group", "test", "testWidgets", "patrolTest"]
```

Turn smells off for one file where they are imposed by an interface (e.g. CLI/JSON/MCP
boundaries) with a comment (strings and docstrings don't count):

```python
# aihealth: disable=primitive_obsession, excess_arguments
```

## License

MIT, see [LICENSE](LICENSE).
