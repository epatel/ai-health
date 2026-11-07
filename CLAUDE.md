# aihealth

## About this project

aihealth estimates code health (CodeScene-style, 1–10 from explainable code
smells) for **Python**, **Dart/Flutter**, **Swift**, **Rust**, **TypeScript**, **JavaScript**, **C**, **C++**, **Objective-C(++)**, **shell scripts** (bash, zsh), **Makefiles** and **CMake**. Two modes: **delta** scores a code
change as feedback for an AI coding agent; **scan** gives an overview of an
existing codebase. Exposed as a CLI, an MCP server and a FastAPI dashboard;
parsing via tree-sitter, history in SQLite.

@project-plan.md

## Commands

- Setup: `make setup` (creates `.venv`, installs `-e '.[dev]'`)
- Test: `make test` (one test: `.venv/bin/pytest tests/test_cli.py::test_help_exits_cleanly`)
- Lint / format: `make lint` · `make fmt`
- Scan: `.venv/bin/aihealth scan PATH [--format text|markdown|json] [--max-findings N] [-v] [--fail-under 7]`
  (PATH = directory, or a single file scored on its own with its project's config)
  (config: `PATH/.aihealth.toml`, documented in `src/aihealth/config.py`)
- Delta: `.venv/bin/aihealth delta [PATHS] [--staged | --base REV [--head REV]] [-v] [--format text|markdown|json]`
  (default: working tree incl. untracked vs HEAD; exit 1 = degraded)
- Run `aihealth delta` on your own changes before committing. Fix new or worsened smells
  it reports in code you touched.
- Version: `src/aihealth/__init__.py` is the only source. Bump the minor version when the
  same code can score differently (a language, thresholds, smell or metric logic), the patch
  otherwise, and add an entry to `CHANGELOG.md`. A release also updates the `@vX.Y.Z` in the
  install lines of README.md and AGENT-INSTALL.md (a test checks it), then gets a git tag.
- History: `.venv/bin/aihealth scan PATH --save` then `.venv/bin/aihealth history PATH`
  (DB `PATH/.aihealth/health.db`; schema changes = append to `store/migrations.py`)
- Dashboard: `.venv/bin/aihealth serve --repo PATH [--db DB] [--port 8765]` (http://127.0.0.1:8765)
- Behaviour-preserving check: `.venv/bin/python scripts/snapshot.py --out before.json ROOT...`,
  change, `--out after.json`, then `--compare before.json after.json` (snapshots go in a scratch dir)
- MCP: `.venv/bin/aihealth mcp` (stdio; registered in `.mcp.json`; usage in README.md)
- CI: `.github/workflows/ci.yml` runs ruff, the tests and `aihealth scan . --fail-under 9` on
  Python 3.11–3.13 (Linux) and 3.12 (macOS), on push, pull requests and weekly; actions are
  pinned to commit SHAs, updated by Dependabot

## Enforced rules

- Global `pip install` is blocked: use `.venv/bin/pip` (see standing-defaults).
- Edited `.py` / `.dart` files are auto-formatted by a hook (ruff / dart format), then checked
  by `aihealth hook post-edit`. Regressions arrive as hook context, so fix them in code you touched.
- A Stop hook (`aihealth hook stop`) reports remaining regressions once before you finish.

## Cards

### Architecture
- [architecture](cards/architecture.md) — cross-module work, adding a component, how engine/CLI/MCP/store/dashboard connect

### Decisions
- [code-health-model](cards/code-health-model.md) — touching smells, thresholds, penalties, score bands, or delta verdict logic
- [parser-choice](cards/parser-choice.md) — adding a language, changing grammars, or adapter/tree-sitter problems
- [standing-defaults](cards/standing-defaults.md) — creating venvs, adding dependencies, or adding/changing database tables

When you commit a change that adds non-obvious logic, settles an architectural choice,
or makes a card above wrong, update or add the card in that commit and keep this index
in sync. Proposed diffs and answers need no card edits. Cards stay self-contained: no
card links to another.
