#!/bin/sh
# PostToolUse(Edit|Write): format the edited file. Fails open when a tool is missing.
f=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
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
exit 0
