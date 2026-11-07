# aihealth

## About this project

aihealth estimates code health (CodeScene-style, 1–10 from explainable code
smells) for **Python** and **Dart/Flutter**. Two modes: **delta** scores a code
change as feedback for an AI coding agent; **scan** gives an overview of an
existing codebase. Exposed as a CLI, an MCP server and a FastAPI dashboard;
parsing via tree-sitter, history in SQLite.

@project-plan.md

## Commands

Planned for M1. Not yet implemented, so confirm each one before relying on it.

- Setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`
- Test: `.venv/bin/pytest` (one test: `.venv/bin/pytest tests/test_metrics.py::test_nesting`)
- Lint: `.venv/bin/ruff check .`
- Run: `.venv/bin/aihealth scan PATH` · `.venv/bin/aihealth delta [--staged|--base REV]`
- MCP: `.venv/bin/aihealth mcp` (stdio) · Dashboard: `.venv/bin/aihealth serve --repo PATH`

## Enforced rules

- Global `pip install` is blocked: use `.venv/bin/pip` (see standing-defaults).
- Edited `.py` / `.dart` files are auto-formatted by a hook (ruff / dart format).

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
