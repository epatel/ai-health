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
| M2 | Language adapters | todo | Python + Dart adapters return a uniform `Module → Class → Function` model with spans, params, body nodes |
| M3 | Function metrics | todo | LoC, cyclomatic, cognitive, max nesting, bumps, args, complex conditionals, widget-tree depth (Dart) — golden-file tests per language |
| M4 | Smells + scoring | todo | smell detectors, penalty table, file score 1–10, LoC-weighted project score, `.aihealth.toml` overrides, duplication detector |
| M5 | Delta mode | todo | before/after scoring for working tree, staged, or `base..head`; per-function new/worsened/fixed smells; verdict + exit code |
| M6 | Output formats | todo | `text`, `json`, `markdown` (agent-oriented, concise with refactor hints) |
| M7 | MCP server | todo | stdio server exposing `review_changes`, `score_file`, `scan_project`, `explain_smell`; registered in `.mcp.json` and smoke-tested from Claude Code |
| M8 | Storage + migrations | todo | SQLite at `<repo>/.aihealth/health.db`, `PRAGMA user_version` migration runner, scans persisted |
| M9 | Dashboard | todo | FastAPI + Jinja: project overview, treemap (size=LoC, colour=score), file view with smells per function, score trend |
| M10 | Agent loop integration | todo | optional Claude Code hook / skill that runs delta after edits; dogfooded on this repo |
| M11 | Calibration | todo | run on 2–3 open-source Python and Flutter repos, tune thresholds/weights so scores spread sensibly; record results in the scoring card |

## Decisions

- **Parsing:** tree-sitter (`tree-sitter` + `tree-sitter-language-pack`)
  for all languages, not per-language native ASTs — one traversal model, Dart
  has no usable Python-side parser otherwise. See `cards/parser-choice.md`.
- **Scoring model:** smell-based penalties, 1–10, LoC-weighted aggregation,
  bands Healthy ≥ 9, Warning 4–9, Alert < 4. See `cards/code-health-model.md`.
- **Delta gate default:** fail when a touched file's score drops, or a touched
  function gains a smell. Tolerance configurable.
- **Storage:** SQLite, dependency-free migration runner (standing default).
- **Dashboard:** server-rendered (FastAPI + Jinja), charts via a pinned CDN
  library (ECharts) — no frontend build.
- **MCP:** official `mcp` Python SDK (FastMCP), stdio transport.
- **Generated Dart files** (`*.g.dart`, `*.freezed.dart`, `*.mocks.dart`)
  excluded by default.

## Current state / handoff

2026-11-07: M0 and M1 done. Skeleton package, CLI stubs (exit 2 with the
planned milestone), Makefile, 7 passing tests. tree-sitter-language-pack's
Dart grammar handles Dart 3 + Flutter cleanly — no source build needed.
Grammar quirks (sibling signature/body, no call node, unreliable boolean
precedence) are recorded in `cards/parser-choice.md`.
Next agent: M2 — uniform `Module → Class → Function` model + Python and
Dart adapters under `src/aihealth/lang/`, using the spike fixtures.
Note: `pip install` may need user approval in this environment — ask the
user to run `! make setup` rather than retrying.

## Open questions

- Flutter `build()` methods are long by nature: separate, higher Large
  Method threshold for `build` in widget classes, or rely on the widget-depth
  metric instead? (Proposal: higher threshold + widget-depth smell; confirm in M11.)
- Delta gate strictness: should a drop of < 0.1 in an already-Alert file
  fail the agent? (Proposal: yes by default — "leave it no worse".)
- Should scan history live in the analysed repo (`.aihealth/`) or a central
  per-user DB for the dashboard to compare repos? (Proposal: per repo, the
  dashboard takes a path.)
