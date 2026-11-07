# Project plan — aihealth

## Goal

A code-health tool that gives an AI coding agent fast, actionable feedback on
its changes (delta mode) and gives humans an overview of where an existing
codebase needs improvement (scan mode). Scores are on a CodeScene-inspired
1–10 scale, derived from explainable code smells. Languages: **Python**,
**Dart (Flutter)** and **Makefiles** (more via the adapter layer: README
"Adding a language").

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
| M11 | Calibration | done | run on open-source Python and Flutter code, tune thresholds/weights so scores spread sensibly; results in the scoring card |
| M12 | Makefile adapter | done | Makefiles scored (rules/recipes via bash, conditionals, defines), Missing .PHONY smell, validated on 2,190 real Makefiles |
| M13 | Next language | todo | which one is the user's call: ask first (see handoff) |

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
- **Flutter `build()`** (settled in M11): keep both the higher Large Method
  limit for `build` (120 lines, about p97 of Flutter's own `build()`
  methods) and the Deep Widget Tree smell (> 8, about p95). Flutter corpora
  land in the target band with them.
- **Dart test closures** passed to `group`/`test`/`testWidgets`/`setUp`/…
  are scored as their own units (option (a), user decision 2026-11-07).
  Configurable via `[dart] test_closure_calls`.
- **Generated Dart files** (`*.g.dart`, `*.freezed.dart`, `*.mocks.dart`)
  excluded by default.

## Current state / handoff

**2026-11-07, end of the first session.** All milestones M0–M12 are done
and pushed to https://github.com/epatel/ai-health (public, MIT). 205
tests, ruff clean, and this repo scans at 9.89 (39 healthy files, 1
warning). The per-milestone history is in git; the design and the numbers
are in `cards/`.

**What exists:** an engine for Python, Dart/Flutter and Makefiles (13
generic smells plus Makefile `missing_phony`). Interfaces: CLI
`scan` (dir or file) / `delta` / `history` / `serve` / `mcp` / `hook`,
an MCP server (5 tools), SQLite history with migrations, a dashboard, and
Claude Code hooks. Scoring is calibrated on 22 corpora (~520k LoC).

**Next task (user request): add a new language (M13).** The user has not
said which one, so ask. Good candidates: TypeScript/JavaScript (biggest
reach; JSX maps onto widget depth), Swift (SwiftUI `body` maps onto widget
depth), Kotlin, Go, and shell scripts (cheapest: the bash role table
already exists in `lang/make.py`). Follow the README section "Adding a
language" step by step (grammar spike, adapter, register, golden tests,
coverage on real code, calibration, docs). `lang/python.py` and
`lang/dart.py` are clean reference adapters. Use `base.ExtractionScope` for
module/class/prefix, `language_smells()` for language-only smells (see
`lang/make_phony.py`), and the generic hooks `is_alternative` / `tokens` /
`hint` / `reports_parse_errors` / `filenames` where the grammar needs them.

**Prove existing languages are unaffected** with `scripts/snapshot.py`:
take a snapshot before and after, then `--compare` (exit 0 = identical).
Corpora on this machine, all read-only:
- Python: `.venv/lib/python3.12/site-packages` (httpx, starlette, click,
  pydantic, pip/_internal, ...)
- Dart: `~/Development/Flutter/flutter/packages` (the SDK), and
  `~/.pub-cache/hosted/pub.dev` (provider, http, dio, go_router, ...)
- Makefiles: `~/Development` (2,190 found; about 900 are generated and
  skipped)

The full Dart corpus takes about 9 minutes, so run it in the background.
Calibration: add the new language's corpora to `CORPORA` in
`scripts/calibrate.py`.

**Remaining refactor candidate:** `src/aihealth/smells.py` (8.64):
`_Detector.add` takes 10 params, plus `_Detector.duplication` (cognitive
20) and `lcom4`. The method that worked for metrics, dart, python and
duplication: snapshot, refactor with small handlers / named predicates /
parameter objects, then compare.

**Environment notes:**
- `pip install` is denied for the agent: edit `pyproject.toml`, then ask
  the user to run `! make setup`.
- Auto mode blocks running downloaded scripts.
- Hooks are active: every Edit/Write of a supported file reports
  regressions (fix them), and the Stop hook nudges once.
- Never write into repos you don't own: `scan --save` writes
  `<path>/.aihealth/`, so use `--db` with a scratch path.
- `.aihealth.toml` excludes `tests/fixtures/*` from this repo's scans.

## Open questions

None open. Raise new ones here.
