#!/bin/sh
# Stop: if the uncommitted changes degrade code health, continue the turn once
# with the regressions as feedback (never re-fires: stop_hook_active). Fails open.
aihealth="$CLAUDE_PROJECT_DIR/.venv/bin/aihealth"
[ -x "$aihealth" ] || exit 0
exec "$aihealth" hook stop
