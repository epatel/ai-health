# Changelog

Versions follow the scores, not only the API: the **minor** version goes
up when the same code can get a different score (a new language,
thresholds, smell or metric logic), the **patch** version otherwise.
Saved scans record the version (`scan.tool_version`), and `aihealth
history` notes when it changes, so a score jump can be traced to a
release here.

## Unreleased

- **Per-path overrides**: `[[overrides]]` tables in `.aihealth.toml` set
  `weights` and `thresholds` for the files their `paths` globs match
  (for example `code_duplication = 0.25` for `tests/*`). They apply in
  scan, delta, the hooks, the MCP tools and the dashboard's file view.
  Configs without overrides score exactly as before, and their saved
  scans keep the same config hash.
- **`aihealth config [PATH]`** and the MCP tool **`list_config`** list
  every `.aihealth.toml` setting with its default, the project's value
  and what it means: `text`, `json` (one entry per key, for agents) or
  `toml` (a complete commented config file that loads back unchanged).

## 0.4.0 (2026-11-23)

- **Java** (`*.java`): methods, constructors, initializers and lambdas
  bound to a name are scored; methods of anonymous classes and enum
  constants on their own. Classes, interfaces, enums and records are
  classes; accessors (getters, setters, fluent setters) and interfaces
  are left out of Low Cohesion. Java 25 `import module`, statements
  before `super(..)`/`this(..)` and annotated varargs
  (`Object @Nullable ... args`) are parsed. Maven's
  `target/generated-sources/` is excluded by default.
- **Go**: generic methods (Go 1.27) and `new(expr)` (Go 1.26) now parse,
  so files using them score fully instead of reporting syntax errors and
  losing functions to recovery (in the Go 1.27 standard library: 12 of
  7,975 files change). Other Go files and other languages score as
  before.

## 0.3.1 (2026-11-13)

- Simpler install: the README leads with one route (clone, `make setup`,
  optional `make link` to put `aihealth` in `~/.local/bin`), other ways
  after it. New Makefile targets `help` (the default), `link` and
  `unlink`; AGENT-INSTALL.md "For you" offers the clone route too. Scores
  unchanged.

## 0.3.0 (2026-11-13)

- **Go** (`*.go`): functions, methods (one class per receiver type and
  file), named `func` values and `t.Run` subtests are scored. Error checks
  that only propagate (`if err != nil { return .., err }`) add no
  complexity, like Rust's `?`; `[go] count_error_checks = true` counts
  them. New language, so a minor release: other languages
  score as before.

## 0.2.2 (2026-11-09)

- `aihealth scan --report [FILE]` also writes a full report: `AI-HEALTH.md`
  (default, in the scanned project; renders on GitHub, with links to each
  file and line) or a self-contained `.html` page for a browser (light and
  dark, no scripts). Output only, scores unchanged.

## 0.2.1 (2026-11-08)

- Markdown reports keep names from scanned code intact: code spans
  survive backticks (shell blocks are named by their first line), and `|`
  in a path no longer splits a table row. Output only, scores unchanged.

## 0.2.0 (2026-11-07)

First tagged release. Everything since the 0.1.0 skeleton:

- **Languages:** Python, Dart/Flutter, Swift/SwiftUI, Rust,
  TypeScript/TSX, JavaScript/JSX, C, C++, Objective-C, Objective-C++,
  shell scripts (bash, sh, dash, ksh, zsh), Makefiles and CMake, through
  tree-sitter adapters. Scoring is calibrated on about 100 open-source
  corpora.
- **Modes:** `aihealth scan` (directory or one file) and `aihealth delta`
  (working tree, staged, or a commit range, with a verdict and exit code),
  in text, markdown and JSON.
- **Agent integration:** an MCP server (`review_changes`, `score_file`,
  `preview_change`, `scan_project`, `explain_smell`), and Claude Code hooks
  (post-edit and stop). Install in a project's venv, per project or per
  user (`AGENT-INSTALL.md`).
- **History and dashboard:** SQLite scan history with migrations
  (`aihealth history`), and a FastAPI dashboard with trends, a treemap and
  per-file views (`aihealth serve`).
- **Hardening** (see `AUDIT-REPORT.md`): git option injection and
  `core.fsmonitor` closed, the dashboard's host and origin checks, frame
  protection and SRI, a file-size limit in every mode, linear-time source
  preparation, a guarded zsh parser, pinned dependency ranges, and CI on
  Python 3.11–3.13.
- Saved scans record the parser and grammar versions, and `aihealth
  history` and the dashboard trends mark where they changed.
