#!/bin/bash

# Re-enable Mac sleep only when all Claude Code sessions have stopped
# Removes session marker, cleans stale sessions, kills caffeinate when none remain

LOCK_DIR="/tmp/claude_caffeinate"
SESSIONS_DIR="$LOCK_DIR/sessions"
PID_FILE="$LOCK_DIR/pid"

[ ! -d "$SESSIONS_DIR" ] && exit 0

# Remove this session's marker
rm -f "$SESSIONS_DIR/$PPID"

# Clean up stale sessions (process no longer exists)
for f in "$SESSIONS_DIR"/*; do
    [ -f "$f" ] || continue
    sid=$(basename "$f")
    if ! ps -p "$sid" > /dev/null 2>&1; then
        rm -f "$f"
    fi
done

# Count remaining active sessions
remaining=$(find "$SESSIONS_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')

if [ "$remaining" -eq 0 ]; then
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if ps -p "$pid" -o args= 2>/dev/null | grep -q '^caffeinate'; then
            kill "$pid" 2>/dev/null
        fi
    fi
    rm -rf "$LOCK_DIR"
fi
