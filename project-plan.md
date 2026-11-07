# Project plan — aihealth

## Goal

A code-health tool that gives an AI coding agent fast, actionable feedback on
its changes (delta mode) and gives humans an overview of where an existing
codebase needs improvement (scan mode). Scores are on a CodeScene-inspired
1–10 scale, derived from explainable code smells. Languages: **Python**,
**Dart (Flutter)**, **Swift (SwiftUI)** and **Makefiles** (more via the
adapter layer: README "Adding a language").

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
| M13 | Swift adapter | done | Swift scored (types, accessors, `#Preview`, Quick closures, SwiftUI view depth), 99.96 % of 46k bodies on 5.6k files, calibrated on 9 corpora |
| M14 | Next language | todo | which one is the user's call: ask first (see handoff) |

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
- **Swift** (M13): every `class_declaration` kind (struct/class/enum/actor/
  extension) is a `Class`; labelled params count half; SwiftUI `body` keeps
  the generic Large Method limit (only 6 % exceed 70 LoC, p97 = 91) and
  view-call nesting feeds Deep Widget Tree. Parse errors stay reported
  (app code 0–2 % of files). See the Swift sections of both cards.
- **Generated Dart files** (`*.g.dart`, `*.freezed.dart`, `*.mocks.dart`)
  excluded by default.

## Current state / handoff

**2026-11-07, second session.** M13 (Swift) is done: `lang/swift.py`,
`tests/test_swift.py` (golden Sonar metrics plus Swift constructs),
spike tests, calibration and docs. Everything from the first session
(M0–M12) is unchanged: snapshots of Python, Dart and Makefile output
before and after this work compare identical. 262 tests, ruff clean, and this repo scans at 9.91 (42 healthy
files, 1 warning).

**What exists:** an engine for Python, Dart/Flutter, Swift and Makefiles
(13 generic smells plus Makefile `missing_phony`). Interfaces: CLI
`scan` (dir or file) / `delta` / `history` / `serve` / `mcp` / `hook`,
an MCP server (5 tools), SQLite history with migrations, a dashboard, and
Claude Code hooks. Scoring is calibrated on 31 corpora.

**Next task: another language (M14).** The user picks which, so ask.
Candidates: TypeScript/JavaScript (biggest reach; JSX maps onto widget
depth), Kotlin (Compose maps onto widget depth; labelled args like
Swift), Go, and shell scripts (cheapest: the bash role table already
exists in `lang/make.py`). Follow README "Adding a language".
`lang/swift.py` is the newest reference: it shows how to handle a grammar
without an `alternative` field and with unreliable operator precedence.

**Prove existing languages are unaffected** with `scripts/snapshot.py
--lang python,dart,make` before and after, then `--compare` (exit 0 =
identical). Corpora on this machine, all read-only:
- Python: `.venv/lib/python3.12/site-packages`
- Dart: `~/Development/Flutter/flutter/packages`, `~/.pub-cache/hosted/pub.dev`
- Makefiles: `~/Development`
- Swift: shallow clones (Alamofire, swift-argument-parser,
  swift-collections, swift-nio, Kingfisher, IceCubesApp, vapor,
  swift-composable-architecture, SwiftLint), set
  `AIHEALTH_SWIFT_CORPORA` for `scripts/calibrate.py --only swift:`.
  firebase-ios-sdk is under `~/Library/Developer/Xcode/DerivedData/*/
  SourcePackages/checkouts`.

The full Dart corpus takes about 9 minutes, so run it in the background.

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
  regressions (fix them), and the Stop hook nudges once. The post-edit
  hook formats `.py`/`.dart` only; `tests/fixtures/sample.swift` line
  numbers are pinned by tests, so don't reformat it.
- Never write into repos you don't own: `scan --save` writes
  `<path>/.aihealth/`, so use `--db` with a scratch path.
- `.aihealth.toml` excludes `tests/fixtures/*` from this repo's scans.

## Open questions

None open. Raise new ones here.
