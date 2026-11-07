# Set up aihealth

aihealth scores code health (1–10) for Python, Dart/Flutter, Swift, Rust, TypeScript,
JavaScript, Makefiles and CMake, and gives a coding agent feedback on its own changes. Set
it up **for you** (every project on this machine) or **for a project** (config committed for
the whole team). Do it by hand with the steps below, or let your agent do the same steps.

## What changes

| | For you | For a project |
|---|---|---|
| CLI | `aihealth` in `~/.local/bin` | each person installs it once (step 1) |
| MCP server | user config, via `claude mcp add` | `.mcp.json` in the repo |
| Hooks | `~/.claude/settings.json` | `.claude/settings.json` in the repo |
| Agent note | none | a section in `CLAUDE.md` |

Nothing else is touched. The hooks fail open: outside a git repo, for other languages,
or when aihealth isn't installed, they do nothing. A project's MCP entry shows as failed
in `/mcp` for anyone who hasn't installed aihealth yet.

## For you

```bash
# 1. Install the CLI (needs Python 3.11+; with pipx: pipx install "git+...")
uv tool install "git+https://github.com/epatel/ai-health"
aihealth --version       # not found? run `uv tool update-shell`, open a new terminal

# 2. Register the MCP server for all your projects
claude mcp add --scope user aihealth -- "$(command -v aihealth)" mcp
```

3. Add the [hooks](#hooks) to `~/.claude/settings.json`.

## For a project

1. Install the CLI as above.
2. Add to `.mcp.json` in the repo root:

   ```json
   { "mcpServers": { "aihealth": { "command": "aihealth", "args": ["mcp"] } } }
   ```

3. Add the [hooks](#hooks) to `.claude/settings.json` in the repo.
4. Add to `CLAUDE.md`:

   ```markdown
   ## Code health
   - Before committing, run `aihealth delta` and fix new or worsened smells it reports
     in code you touched (exit code 1 = health degraded).
   - `aihealth scan .` gives an overview of the codebase.
   ```

## Hooks

Merge these into the settings file and keep any hooks already there.

```json
{
  "hooks": {
    "PostToolUse": [
      { "matcher": "Edit|Write",
        "hooks": [{ "type": "command",
          "command": "command -v aihealth >/dev/null 2>&1 && aihealth hook post-edit || true" }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command",
          "command": "command -v aihealth >/dev/null 2>&1 && aihealth hook stop || true" }] }
    ]
  }
}
```

- **post-edit**: after an edit, reports new or worsened smells in that file. Silent
  otherwise.
- **stop**: before the agent finishes, gives it one more turn if the uncommitted changes
  make health worse. It never fires twice in a row.

## Try it

```bash
aihealth scan . --max-findings 5     # project score and the worst files
aihealth delta                       # do my uncommitted changes degrade health?
```

## Let your agent do it

Paste one of these into Claude Code, in the project you want to check:

```text
Set up aihealth for me, following the "For you" steps in
https://github.com/epatel/ai-health/blob/main/AGENT-INSTALL.md
Show me each command and file change before you make it, and merge JSON files
instead of replacing them. Then run `aihealth scan . --max-findings 5` and summarise it.
```

```text
Set up aihealth for this project, following the "For a project" steps in
https://github.com/epatel/ai-health/blob/main/AGENT-INSTALL.md
Show me each command and file change before you make it, and merge JSON files
instead of replacing them. Don't commit. Then run `aihealth scan . --max-findings 5`
and summarise it.
```

The prompts ask the agent to show each command and file change first, so you can check
it against this page before approving it.

## Remove

```bash
uv tool uninstall aihealth           # or: pipx uninstall aihealth
claude mcp remove --scope user aihealth
```

Then delete the hook entries from the settings file (and, for a project, the
`.mcp.json` entry and the `CLAUDE.md` section).
