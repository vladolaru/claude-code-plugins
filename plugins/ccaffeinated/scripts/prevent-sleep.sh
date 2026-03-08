#!/bin/bash

# Keep Mac awake during Claude Code sessions via caffeinate
# Uses per-session marker files (PPID) instead of counter to handle crashes gracefully

LOCK_DIR="/tmp/claude_caffeinate"
SESSIONS_DIR="$LOCK_DIR/sessions"
PID_FILE="$LOCK_DIR/pid"

mkdir -p "$SESSIONS_DIR"

# Register this session (PPID = Claude Code's node process)
touch "$SESSIONS_DIR/$PPID"

# Clean up stale sessions (process no longer exists)
for f in "$SESSIONS_DIR"/*; do
    [ -f "$f" ] || continue
    sid=$(basename "$f")
    if ! ps -p "$sid" > /dev/null 2>&1; then
        rm -f "$f"
    fi
done

# Restart caffeinate with fresh 1h timeout (safety net if Claude crashes)
if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE")
    if ps -p "$pid" -o args= 2>/dev/null | grep -q '^caffeinate'; then
        kill "$pid" 2>/dev/null
    fi
fi

nohup caffeinate -i -t 3600 > /dev/null 2>&1 &
echo $! > "$PID_FILE"
