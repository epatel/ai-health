# Project plan — aihealth

## Goal

A code-health tool that gives an AI coding agent fast, actionable feedback on
its changes (delta mode) and gives humans an overview of where an existing
codebase needs improvement (scan mode). Scores are on a CodeScene-inspired
1–10 scale, derived from explainable code smells. Languages: **Python**,
**Dart (Flutter)**, **Swift (SwiftUI)**, **Rust**, **TypeScript/TSX**,
**JavaScript/JSX**, **C**, **C++**, **Objective-C(++)**, **shell scripts** (bash, zsh),
**Makefiles** and **CMake**
(more via the adapter layer:
README "Adding a language").

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
| M14 | Rust adapter | done | Rust scored (impl/trait blocks, let-else, let-chains, labeled loops), `#[cfg(test)]` modules left out by default, 99.99 % of 56k bodies on 4.8k files, calibrated on 13 crates |
| M15 | TypeScript adapter | done | `.ts`/`.mts`/`.cts`/`.tsx` scored, named function values and test closures as units, JSX depth, 100 % of 18k declarations/methods on 12.7k files, calibrated on 10 repos |
| M16 | JavaScript adapter | done | `.js`/`.mjs`/`.cjs`/`.jsx` via the TypeScript adapter, assigned functions (CommonJS, prototypes) as units, 100 % of 26k declarations/methods on 22.7k files, calibrated on 7 repos |
| M17 | CMake adapter | done | `CMakeLists.txt`/`*.cmake` scored (functions, macros, top-level blocks, `AND`/`OR`), all 639 definitions on 956 files, 90 % of 566 hand-written files healthy |
| M18 | C, C++, Objective-C(++) | done | one adapter, three grammars (`.h` routed by content), source prepared against macros, units recovered from ERROR nodes; Objective-C++ via the objc grammar with `::` rewritten; calibrated on 18 repos |
| M19 | Shell scripts | done | `.sh`/`.bash` and shebang scripts scored (functions + top-level blocks), bash roles shared with the Makefile adapter, 0 crashes on 5.5k scripts, calibrated on 11 repos |
| M20 | zsh | done | `.zsh`/`.zsh-theme`, zsh shebangs and `#compdef` functions via the shell adapter: zsh grammar guarded against endless loops and compared with the bash tree per file, 0 crashes on 1,158 files, calibrated on 9 repos |
| M21 | Next language | todo | which one is the user's call: ask first (see handoff) |

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
- **Rust** (M14): `#[cfg(test)]` modules are left out of scores by default
  (`[rust] score_test_modules`), because unit tests share the production
  file; builder setters and traits are not judged for cohesion. See the
  Rust sections of both cards.
- **TypeScript** (M15): units are declarations, class members, functions
  bound to a name and test closures; inline callbacks stay in their
  function. JSX nesting feeds Deep Widget Tree. Calibration corpora
  exclude test files kept in `src/`. See the TypeScript sections of both
  cards.
- **JavaScript** (M16): the TypeScript adapter with the `javascript`
  grammar; functions assigned to a name (`module.exports`, `app.f =`,
  prototypes) are units too, in JavaScript only.
- **CMake** (M17): units mirror the Makefile adapter (definitions and
  top-level blocks); generic thresholds fit. See the CMake sections of
  both cards.
- **C, C++, Objective-C** (M18): one adapter (`lang/cfamily.py`) with
  three grammars; `.h` routed by content. `#if` lines and lone ALL_CAPS
  macro lines are blanked before parsing, `typedef NS_ENUM` read as an
  enum. Parse errors not reported, `.mm` not supported, vendored folders
  not excluded, `goto` +1 (user decisions 2026-11-07). Objective-C++
  added on request: `objc` grammar with `a::b` → `a__b`, names from the
  original source, about 9 % of `.mm` code lines unscored (parse errors).
  C++ cohesion only for classes with all methods in the body. No C-family thresholds. See
  the C-family sections of both cards.
- **Shell scripts** (M19): bash grammar for `.sh`, `.bash` and
  extensionless files with a sh/bash/dash/ksh shebang (registry `head`
  argument); zsh not supported (55 % of its lines fail). Units are
  functions (plain names) and top-level blocks, as in CMake. `cmd || die`
  guards count (3 % of cognitive points). Parse errors not reported. See
  the shell sections of both cards.
- **zsh** (M20, user asked for it consolidated with bash): the same
  `ShellAdapter`, module language `zsh`. The zsh grammar can loop forever,
  so it runs in a forked child with a 1 s CPU / 3 s wall-clock limit first, and each file
  keeps the zsh or bash tree, whichever loses fewer lines. See the zsh
  sections of both cards.
- **Dependencies** (after the re-assessment): tree-sitter and the
  grammar pack pinned to the tested minor version, other runtime
  dependencies below their next major, ruff to a minor. CI
  (`.github/workflows/ci.yml`): ruff, tests and the self-scan on Python
  3.11–3.13 (Linux) and 3.12 (macOS), weekly too. No lockfile: users
  install with pip, so the ranges are what they get.
- **Generated Dart files** (`*.g.dart`, `*.freezed.dart`, `*.mocks.dart`)
  excluded by default.

## Current state / handoff

**2026-11-07, third session.** M20 (zsh) is done: `lang/shell_zsh.py`
(zsh detection, `prepare_zsh`, the guarded `ZshParser` in
`ShellAdapter._parsers["zsh"]`), zsh roles/units in `lang/shell.py`,
registry routing via `is_shell_head`, zsh tests in `tests/test_shell.py`,
`zsh:` corpora in `scripts/calibrate.py` (same `AIHEALTH_SH_CORPORA`).
Snapshots: all 6,536 non-zsh shell and Makefile files identical.

Before that, M19 (shell scripts) was done:
`lang/shell.py`, `tests/test_shell.py`, shebang routing in the registry
(`is_supported`/`adapter_for` take a `head`; engine, delta, hooks, CLI,
MCP and `scripts/snapshot.py` pass it), `sh:` corpora in
`scripts/calibrate.py` (`AIHEALTH_SH_CORPORA`), autoconf/configure/libtool
generated-file markers, and docs. The Makefile adapter now imports the
bash roles from `lang/shell.py`.

Before that, M18 (C, C++, Objective-C) was done, and
Objective-C++ (`.mm`, language `objcpp`) was added after it:
`lang/cfamily.py` (adapter, header router, source preparation),
`lang/cfamily_decl.py` (declarators, params, types),
`lang/cfamily_objc.py` (selectors, cohesion), `tests/test_cfamily.py`,
units recovered from ERROR nodes,
spike tests, `c:`/`cpp:`/`objc:` corpora in `scripts/calibrate.py`, and
docs. Shared code changed only by adding hooks (`grammars`,
`_grammar_for`, `_prepare` in `lang/base.py`), C-family default excludes
and three generated-file markers (Qt, bison, flex). Snapshots of every
earlier language (49,533 files plus 1,217 Makefiles) compare identical.
Now 501 tests, ruff clean, and this repo scans at 9.94 (60 healthy
files, 1 warning).

**What exists:** an engine for Python, Dart/Flutter, Swift, Rust,
TypeScript/TSX, JavaScript/JSX, C, C++, Objective-C(++), shell scripts,
Makefiles and CMake
(13 generic smells plus Makefile `missing_phony`). Interfaces: CLI `scan`
(dir or file) / `delta` / `history` / `serve` / `mcp` / `hook`, an MCP
server (5 tools), SQLite history with migrations, a dashboard, and
Claude Code hooks. Scoring is calibrated on 90 corpora, plus
distribution checks on Makefiles and CMake files.

**Next task (M21): ask the user which language.** Candidates: Go
(`if err != nil` needs a calibration decision), Kotlin (Compose maps
onto widget depth; labelled args like Swift).
Follow README "Adding a language". `lang/cfamily.py` shows several
grammars in one adapter and source preparation; `lang/swift.py` a grammar
without an `alternative` field; `lang/rust.py` a wrapped else-if, tests in
the same file, and cohesion idiom exclusions.

**Prove existing languages are unaffected** with `scripts/snapshot.py
--lang python,dart,make` before and after, then `--compare` (exit 0 =
identical). Corpora on this machine, all read-only:
- Python: `.venv/lib/python3.12/site-packages`
- Dart: `~/Development/Flutter/flutter/packages`, `~/.pub-cache/hosted/pub.dev`
- Makefiles and CMake: `~/Development` (CMake: OrcaSlicer, Pico SDK,
  Flutter, OpenSCAD; Xcode `SourcePackages/checkouts`)
- TypeScript: shallow clones (zod, TanStack Query, tRPC, date-fns, Vite,
  ky, TypeORM, Excalidraw, Nest), set `AIHEALTH_TS_CORPORA` for
  `scripts/calibrate.py --only ts:`.
- JavaScript: shallow clones (Express, axios, lodash, Preact, webpack,
  ESLint, three.js), set `AIHEALTH_JS_CORPORA` for `--only js:`.
- Rust: the crates in `~/.cargo/registry/src` (calibrate.py finds the
  newest version of each by name).
- Shell: shallow clones (bats-core, rbenv, pyenv, n, docker-install,
  bash-it, Homebrew install, dehydrated, nvm, acme.sh, dokku; zsh:
  ohmyzsh, prezto, zsh-completions, zsh-z, spaceship-prompt,
  zsh-autocomplete, powerlevel10k, zinit, zimfw) in one directory, set
  `AIHEALTH_SH_CORPORA` for `calibrate.py --only sh:` / `--only zsh:`.
- C / C++ / Objective-C: Pico SDK (`~/Development/pico-sdk`), Flipper
  (`~/Development/flipper/flipperzero-firmware`, skip its `toolchain/`),
  OrcaSlicer `src/`, the Flutter engine, Xcode checkouts; shallow clones
  (lua, cJSON, libuv, redis, curl, fmt, leveldb, googletest,
  nlohmann/json, abseil, SDWebImage, AFNetworking, fmdb, Mantle,
  KVOController, Texture, ComponentKit), set `AIHEALTH_C_CORPORA` for `calibrate.py --only c`
  (C and C++) and `--only objc`.
- Swift: shallow clones (Alamofire, swift-argument-parser,
  swift-collections, swift-nio, Kingfisher, IceCubesApp, vapor,
  swift-composable-architecture, SwiftLint), set
  `AIHEALTH_SWIFT_CORPORA` for `scripts/calibrate.py --only swift:`.
  firebase-ios-sdk is under `~/Library/Developer/Xcode/DerivedData/*/
  SourcePackages/checkouts`.

The full Dart corpus takes about 9 minutes, so run it in the background.

**Refactoring:** no file below 10 is left after `smells.py` (8.64 →
10.00: a `_Hit` parameter object, a `_DuplicateSite` accumulator, a
union-find class, `Detector.detect` replacing the 5-parameter `detect`).
The method that worked for metrics, dart, python, duplication and smells:
snapshot, refactor with small handlers / named predicates / parameter
objects, then compare.

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
