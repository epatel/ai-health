# Set up aihealth

aihealth scores code health (1–10) for Python, Dart/Flutter, Swift, Rust, Go, TypeScript,
JavaScript, C, C++, Objective-C(++), shell scripts (bash, zsh), Makefiles and CMake, and
gives a coding agent feedback on its own changes. Set it up **in a project's venv**
(recommended: installed inside the project, nothing global), **for a project** with a
global install (config committed for the whole team), or **for you** (every project on
this machine). Do it by hand with the steps below, or let your agent do the same steps.

## What changes

| | In a project's venv | For a project | For you |
|---|---|---|---|
| CLI | `.venv/bin/aihealth` in the project | `aihealth` in `~/.local/bin`, each person once | `aihealth` in `~/.local/bin` |
| MCP server | `.mcp.json` in the repo | `.mcp.json` in the repo | user config, via `claude mcp add` |
| Hooks | `.claude/settings.json` in the repo | `.claude/settings.json` in the repo | `~/.claude/settings.json` |
| Agent note | a section in `CLAUDE.md` | a section in `CLAUDE.md` | none |

Nothing else is touched. The hooks fail open: outside a git repo, for other languages,
or when aihealth isn't installed, they do nothing. A project's MCP entry shows as failed
in `/mcp` for anyone who hasn't installed aihealth yet.

## In a project's venv

aihealth goes into the project's own virtual environment, and the config points at
`.venv/bin/aihealth`, so nothing is installed globally and the project chooses
aihealth's version.

```bash
# 1. Install into the project's venv (needs Python 3.11+; macOS's python3 is 3.9,
#    so check `python3 --version` and use e.g. python3.12 from brew or uv if needed)
python3 -m venv .venv                # skip if the project already has one
.venv/bin/pip install "git+https://github.com/epatel/ai-health@v0.3.0"
.venv/bin/aihealth --version
```

`@v0.3.0` is the current release (scores can change between releases, see `CHANGELOG.md`).
Use the latest tag, or leave it out to follow `main`.

2. Add to `.mcp.json` in the repo root. Claude Code starts the server in the project
   root, so the relative path works:

   ```json
   { "mcpServers": { "aihealth": { "command": ".venv/bin/aihealth", "args": ["mcp"] } } }
   ```

3. Add these hooks to `.claude/settings.json` in the repo (merge them, and keep any
   hooks already there). They do nothing when the venv has no aihealth:

   ```json
   {
     "hooks": {
       "PostToolUse": [
         { "matcher": "Edit|Write",
           "hooks": [{ "type": "command",
             "command": "a=\"$CLAUDE_PROJECT_DIR/.venv/bin/aihealth\"; [ -x \"$a\" ] && \"$a\" hook post-edit || true" }] }
       ],
       "Stop": [
         { "hooks": [{ "type": "command",
             "command": "a=\"$CLAUDE_PROJECT_DIR/.venv/bin/aihealth\"; [ -x \"$a\" ] && \"$a\" hook stop || true" }] }
       ]
     }
   }
   ```

4. Add to `CLAUDE.md`:

   ```markdown
   ## Code health
   - Before committing, run `.venv/bin/aihealth delta` and fix new or worsened smells it
     reports in code you touched (exit code 1 = health degraded).
   - `.venv/bin/aihealth scan .` gives an overview of the codebase.
   ```

Notes:

- `.venv/` should be in `.gitignore`. Each person runs step 1 in their own clone; the
  committed config works for anyone who has.
- aihealth's dependencies (tree-sitter, tree-sitter-language-pack, mcp, fastapi,
  uvicorn) share the venv with the project's. If they clash with the project's pins,
  use a separate venv (say `~/.venvs/aihealth`) and put its absolute path in the config.
  That path is personal, so then keep the config in your own `~/.claude/settings.json`
  and `claude mcp add --scope user`, not in the repo.

## For a project

1. Install the CLI globally, as in [For you](#for-you) step 1.
2. Add to `.mcp.json` in the repo root:

   ```json
   { "mcpServers": { "aihealth": { "command": "aihealth", "args": ["mcp"] } } }
   ```

3. Add the [hooks](#hooks) to `.claude/settings.json` in the repo.
4. Add the `CLAUDE.md` section from [In a project's venv](#in-a-projects-venv), with
   `aihealth` in place of `.venv/bin/aihealth`.

## For you

```bash
# 1. Install the CLI (needs Python 3.11+; with pipx: pipx install "git+...")
uv tool install "git+https://github.com/epatel/ai-health@v0.3.0"   # the current release
aihealth --version       # not found? run `uv tool update-shell`, open a new terminal

# 2. Register the MCP server for all your projects
claude mcp add --scope user aihealth -- "$(command -v aihealth)" mcp
```

3. Add the [hooks](#hooks) to `~/.claude/settings.json`.

## Hooks

For a global install (For a project, For you). Merge these into the settings file and
keep any hooks already there.

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

What the hooks do (in every variant):

- **post-edit**: after an edit, reports new or worsened smells in that file. Silent
  otherwise.
- **stop**: before the agent finishes, gives it one more turn if the uncommitted changes
  make health worse. It never fires twice in a row.

## Try it

```bash
.venv/bin/aihealth scan . --max-findings 5     # project score and the worst files
.venv/bin/aihealth delta                       # do my uncommitted changes degrade health?
```

With a global install, drop the `.venv/bin/`.

## Let your agent do it

Paste one of these into Claude Code, in the project you want to check:

```text
Set up aihealth in this project's venv, following the "In a project's venv" steps in
https://github.com/epatel/ai-health/blob/main/AGENT-INSTALL.md
Show me each command and file change before you make it, and merge JSON files
instead of replacing them. Don't commit. Then run `.venv/bin/aihealth scan . --max-findings 5`
and summarise it.
```

```text
Set up aihealth for this project, following the "For a project" steps in
https://github.com/epatel/ai-health/blob/main/AGENT-INSTALL.md
Show me each command and file change before you make it, and merge JSON files
instead of replacing them. Don't commit. Then run `aihealth scan . --max-findings 5`
and summarise it.
```

```text
Set up aihealth for me, following the "For you" steps in
https://github.com/epatel/ai-health/blob/main/AGENT-INSTALL.md
Show me each command and file change before you make it, and merge JSON files
instead of replacing them. Then run `aihealth scan . --max-findings 5` and summarise it.
```

The prompts ask the agent to show each command and file change first, so you can check
it against this page before approving it.

## Remove

```bash
.venv/bin/pip uninstall aihealth       # in a project's venv
uv tool uninstall aihealth             # global (or: pipx uninstall aihealth)
claude mcp remove --scope user aihealth    # if registered for you
```

Then delete the hook entries from the settings file (and, for a project, the
`.mcp.json` entry and the `CLAUDE.md` section).
