# Install aihealth with your coding agent

Paste the prompt below into Claude Code (or another coding agent), from the project you
want to check. The agent asks whether to set aihealth up **for you** (every project on
this machine) or **for this project** (committed config the whole team shares), then
installs it, wires it in and runs a first scan.

What you end up with:

- the `aihealth` CLI (`scan`, `delta`, `serve`, ...),
- the MCP server, so the agent can review its own changes,
- hooks that report health regressions after each edit and before the agent finishes,
- for a project: a `CLAUDE.md` note and an optional CI gate.

Supported languages: Python, Dart/Flutter, Swift/SwiftUI and Makefiles. Other files are
ignored, and every part fails open (no git repo or unsupported file: nothing happens).

---

## Prompt

````text
Set up aihealth (https://github.com/epatel/ai-health) for me. It estimates code health
(1-10, CodeScene-style code smells) for Python, Dart/Flutter, Swift and Makefiles, and
gives a coding agent feedback on its own changes.

Before you change anything, ask me which setup I want:
  A) For me: one install, used in every project on this machine. User-level MCP
     server and hooks.
  B) For this project: config committed in this repo, so everyone working on it (and
     their agents) gets the same tool. Each person still installs the CLI once.

Then follow the steps below. Show me each config file change before you write it. Merge
into existing JSON files: never overwrite settings, servers or hooks that are already
there. If a step fails, stop and tell me; don't work around it.

1. Install the CLI (both setups). Needs Python 3.11+.
   - If `uv` is available:   uv tool install "git+https://github.com/epatel/ai-health"
   - else if `pipx` is:       pipx install "git+https://github.com/epatel/ai-health"
   - else ask me before creating a venv at ~/.local/share/aihealth and linking
     ~/.local/bin/aihealth to its bin/aihealth.
   If `aihealth` is already on PATH, ask whether to upgrade it
   (`uv tool upgrade aihealth` / `pipx upgrade aihealth`) instead.
   Check: `aihealth --help` works. Note the absolute path from `command -v aihealth`.

2. Register the MCP server.
   - A: `claude mcp add --scope user aihealth -- <absolute path> mcp`
   - B: add to .mcp.json in the repo root (create it if missing):
       { "mcpServers": { "aihealth": { "command": "aihealth", "args": ["mcp"] } } }
   The server analyses the directory the agent runs in, so one registration covers
   every project.

3. Add the hooks (Claude Code). Merge into
   - A: ~/.claude/settings.json, with the absolute path from step 1,
   - B: .claude/settings.json in the repo, with plain `aihealth`, so it works for
     everyone who has it on PATH:
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
   (For A, put the absolute path in place of both `aihealth` words.)
   post-edit: after an edit to a supported file, reports new or worsened smells in that
   file compared with HEAD, and is silent otherwise. stop: before the agent finishes,
   if the uncommitted changes degrade health, it gets one more turn to fix them. It
   never fires twice in a row.

4. Project setup only (B):
   - Add this section to CLAUDE.md (create it if missing; keep what's there):
       ## Code health
       - Before committing, run `aihealth delta` on your changes. Fix the new or worsened
         smells it reports in code you touched (exit code 1 = health degraded).
       - `aihealth scan .` gives an overview of the codebase. MCP tools:
         `review_changes`, `preview_change`, `score_file`, `scan_project`,
         `explain_smell`.
   - Only if I ask for it: add a `.aihealth.toml` (thresholds, weights, excludes; all
     keys are in src/aihealth/config.py of the aihealth repo), and/or a CI step
     `aihealth scan . --fail-under <score>` that installs aihealth the same way as
     step 1.
   - Don't commit anything: list the changed files and let me commit them.

5. Try it out and report back:
   - `aihealth scan . --max-findings 5`: tell me the project score, the band
     (healthy >= 9, warning 4-9, alert < 4) and the three worst files, with what
     drives each of them.
   - `aihealth delta`: whether the current uncommitted changes degrade health.
   - Tell me that the MCP server and hooks load in a new Claude Code session (or after
     /mcp), and how to undo the setup: remove the hook entries and the MCP server, and
     `uv tool uninstall aihealth` / `pipx uninstall aihealth`.

Notes:
- `aihealth scan . --save` stores history in .aihealth/health.db (it gitignores
  itself); `aihealth history` and `aihealth serve --repo .` (dashboard on
  http://127.0.0.1:8765) use it.
- Generated files, build output (.build/, Pods/, build/, .dart_tool/, ...) and
  vendored code are skipped by default. A file can opt out of a smell with a comment
  such as `// aihealth: disable=code_duplication` (give the reason in the comment).
````

---

## Manual setup

The same steps by hand:

```bash
uv tool install "git+https://github.com/epatel/ai-health"     # or: pipx install ...
claude mcp add --scope user aihealth -- "$(command -v aihealth)" mcp
aihealth scan .            # project overview
aihealth delta             # does my uncommitted change degrade health? (exit 1 = yes)
```

For the hooks, see "Automatic feedback in Claude Code" in [README.md](README.md).
