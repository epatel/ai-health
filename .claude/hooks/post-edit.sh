#!/bin/sh
# PostToolUse(Edit|Write): format the edited file, then report code-health
# regressions in it to Claude (aihealth hook post-edit: silent unless the file
# got worse). One script so health is checked after formatting, not racing it.
# Fails open when a tool is missing.
input=$(cat)
py="$CLAUDE_PROJECT_DIR/.venv/bin/python"
[ -x "$py" ] || py=python3
f=$(printf '%s' "$input" | "$py" -I -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path") or "")' 2>/dev/null)
[ -n "$f" ] && [ -f "$f" ] || exit 0
case "$f" in
  *.py)
    ruff="$CLAUDE_PROJECT_DIR/.venv/bin/ruff"
    [ -x "$ruff" ] && "$ruff" format -q "$f"
    ;;
  *.dart)
    command -v dart >/dev/null 2>&1 && dart format -o write "$f" >/dev/null 2>&1
    ;;
esac
aihealth="$CLAUDE_PROJECT_DIR/.venv/bin/aihealth"
[ -x "$aihealth" ] && printf '%s' "$input" | "$aihealth" hook post-edit
exit 0
