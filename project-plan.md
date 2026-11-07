# Project plan — aihealth

## Goal

A code-health tool that gives an AI coding agent fast, actionable feedback on
its changes (delta mode) and gives humans an overview of where an existing
codebase needs improvement (scan mode). Scores are on a CodeScene-inspired
1–10 scale, derived from explainable code smells. First languages: **Python**
and **Dart (Flutter)**.

Delivered as three faces over one engine:

1. CLI — `aihealth delta` / `aihealth scan`, exit codes usable as a gate.
2. MCP server — tools an agent calls to review its own diff.
3. Web dashboard (Python server) — hotspots, file drill-down, trends.

## Non-goals

- Reproducing CodeScene's exact numbers. We borrow the *model* (smells →
  penalties → 1–10, LoC-weighted aggregation), not their calibration.
- Behavioural/git-history analysis (hotspots by change frequency, knowledge
  loss, team coupling) in v1. Change frequency may be added later as a
  hotspot weight only.
- Languages beyond Python and Dart in v1. The adapter layer must make adding
  more cheap, but none are implemented.
- Auto-fixing code. We explain and hint; the agent refactors.
- A JS build step for the dashboard.

## Milestones

Status: `todo` / `wip` / `done`.

| # | Milestone | Status | Done when |
|---|---|---|---|
| M0 | Agentic setup (CLAUDE.md, cards, plan, hooks) | done | this file exists, checker passes |
| M1 | Skeleton + parser spike | done | `.venv`, `pyproject.toml`, `Makefile`, `aihealth --help`, pytest green; tree-sitter parses a real Flutter widget file and a Python module |
| M2 | Language adapters | done | Python + Dart adapters return a uniform `Module → Class → Function` model with spans, params, body nodes |
| M3 | Function metrics | done | LoC, cyclomatic, cognitive, max nesting, bumps, args, complex conditionals, widget-tree depth (Dart) — golden-file tests per language |
| M4 | Smells + scoring | done | smell detectors, penalty table, file score 1–10, LoC-weighted project score, `.aihealth.toml` overrides, duplication detector |
| M5 | Delta mode | done | before/after scoring for working tree, staged, or `base..head`; per-function new/worsened/fixed smells; verdict + exit code |
| M6 | Output formats | done | `text`, `json`, `markdown` (agent-oriented, concise with refactor hints) |
| M7 | MCP server | done | stdio server exposing `review_changes`, `score_file`, `scan_project`, `explain_smell`; registered in `.mcp.json` and smoke-tested from Claude Code |
| M8 | Storage + migrations | done | SQLite at `<repo>/.aihealth/health.db`, `PRAGMA user_version` migration runner, scans persisted |
| M9 | Dashboard | done | FastAPI + Jinja: project overview, treemap (size=LoC, colour=score), file view with smells per function, score trend |
| M10 | Agent loop integration | done | optional Claude Code hook / skill that runs delta after edits; dogfooded on this repo |
| M11 | Calibration | todo | run on 2–3 open-source Python and Flutter repos, tune thresholds/weights so scores spread sensibly; record results in the scoring card |

## Decisions

- **Parsing:** tree-sitter (`tree-sitter` + `tree-sitter-language-pack`)
  for all languages, not per-language native ASTs — one traversal model, Dart
  has no usable Python-side parser otherwise. See `cards/parser-choice.md`.
- **Scoring model:** smell-based penalties, 1–10, LoC-weighted aggregation,
  bands Healthy ≥ 9, Warning 4–9, Alert < 4. See `cards/code-health-model.md`.
- **Delta gate default:** fail when a touched file's score drops (beyond
  `delta.tolerance`, default 0.1, which absorbs share artefacts), or it
  gains/worsens a smell without a net score gain. New files fail if they
  have any smell.
- **Storage:** SQLite per analysed repo at `<repo>/.aihealth/health.db`
  (self-gitignored, `--db` to override), dependency-free migration runner
  (standing default). Settled in M8, no central DB.
- **Dashboard:** server-rendered (FastAPI + Jinja), charts via a pinned CDN
  library (ECharts) — no frontend build.
- **MCP:** official `mcp` Python SDK 2.x (`MCPServer`, formerly FastMCP), stdio
  transport, plain-text tool results (`structured_output=False`).
- **Dart test closures** passed to `group`/`test`/`testWidgets`/`setUp`/…
  are scored as their own units (option (a), user decision 2026-11-07).
  Configurable via `[dart] test_closure_calls`.
- **Generated Dart files** (`*.g.dart`, `*.freezed.dart`, `*.mocks.dart`)
  excluded by default.

## Current state / handoff

2026-11-07: M0 and M1 done. Skeleton package, CLI stubs (exit 2 with the
planned milestone), Makefile, 7 passing tests. tree-sitter-language-pack's
Dart grammar handles Dart 3 + Flutter cleanly — no source build needed.
Grammar quirks (sibling signature/body, no call node, unreliable boolean
precedence) are recorded in `cards/parser-choice.md`.
M2 done: `model.py` + `lang/{base,python,dart}.py` + registry. 23 tests.
Verified on the Flutter SDK: every Dart `function_body` is extracted.
M3 done: `metrics.py` plus adapter role tables. 50 tests, golden values
from the SonarSource white paper. Runs over the full Flutter SDK with no
crashes at about 31k LoC/s for Dart and 83k LoC/s for Python. Baseline
distributions are in `cards/code-health-model.md`.
M4 done: config, smells (13), LCOM4, primitive obsession, winnowing
duplication, scoring, `engine.py`, and a working `aihealth scan` (text/json,
`--top`, `-v`, `--functions`, `--fail-under`, `--config`). 76 tests.
Dart test closures are separate units (decision (a)). A function's LoC is
its own lines only. Score formula changed to exponential decay (see
code-health-model card).
Dogfooding: this repo scores 7.18. The worst files are `lang/dart.py`
(`member_refs`, `role`) and `smells.py` (`_Detector.function`). Refactor
candidates once delta mode exists to verify improvements.
M5 done: `gitutil.py` + `delta.py` + `aihealth delta` (worktree incl.
untracked / `--staged` / `--base` / `--head`, path filters, text and json,
exit 1 on degraded). 94 tests. The verdict rule was refined: new smells
are OK if the file's score rises on net (see card).
Dogfooded: delta flagged the new M5 code, so it was refactored until
delta.py and gitutil.py score 10.00. `smells.py` went from 5.03 to 7.52,
and the project from 7.18 to 8.09. Noise fixes: assert conditions and bool
params.
M6 done: `report.py` (renderers moved out of cli.py) and `--format
markdown` plus `--max-findings` for scan and delta. Entries are grouped per
function, with distinct hints. 105 tests. Project self-score is 8.20.
M7 done: `mcp_server.py` (5 tools incl. `preview_change`), `aihealth mcp`,
`.mcp.json`, README.md, and the `aihealth: disable=` comment directive. 116
tests incl. a stdio round trip. mcp SDK 2.x is pinned: v2 renamed FastMCP
to MCPServer, hides non-ToolError messages and uses snake_case result
fields.
M8 done: `store/` with 2 migrations (scan/file_score/finding, then
function_metric), atomic runner, scan persistence with git and config
metadata, `aihealth scan --save` and `aihealth history`. 127 tests,
including rollback of a half-failed migration.
M9 done: `web/` dashboard (overview, treemap, file view, scan-now, JSON
API) with `aihealth serve`. 145 tests. Visually checked in Chrome: dark and
light mode, and 390 px width with no horizontal overflow. Calibration fixes
found while building it: generic containers are judged by their contents,
and the delta tolerance default is 0.1.
Caution for agents: `aihealth scan X --save` writes `X/.aihealth/`. For
repos you don't own (e.g. the Flutter SDK), pass `--db` to a scratch path.
M10 done: `hooks.py` + `aihealth hook post-edit|stop`, wired in this repo
(`.claude/hooks/post-edit.sh` = format + health, `.claude/hooks/stop.sh`),
and a README section for other projects. 154 tests. Verified live: a
smelly Write produced the regression report as PostToolUse context, and
fixing it made the hook go silent. Calibration: Complex Conditional now
counts only branch/loop conditions.
Next agent: M11 calibration. Run on 2-3 open-source Python and Flutter
repos (use `--db` in a scratch dir and never write into foreign repos).
Compare score spreads (Flutter material was 4.17 and all Flutter packages
3.29, which looks harsh). Tune decay, weights and per-function caps, and
record the results in the code-health-model card.
Note: `pip install` may need user approval in this environment — ask the
user to run `! make setup` rather than retrying.

## Open questions

- Flutter `build()` methods are long by nature: separate, higher Large
  Method threshold for `build` in widget classes, or rely on the widget-depth
  metric instead? (Proposal: higher threshold + widget-depth smell; confirm in M11.)
- Scores look harsh on well-maintained code (Flutter material 4.17, all
  Flutter packages 3.29). Calibrate in M11. Candidate fixes: cap the
  penalty per function, higher decay, or normalise file penalty by
  function count.
