#!/usr/bin/env bash
#
# Records the pirategoat-tools plugin root for THIS session, so agents
# dispatched into target repositories can find scripts and shared protocols
# without searching. PreToolUse:Bash hook: runs before every Bash command,
# a subagent's included, with CLAUDE_PLUGIN_ROOT injected by the plugin
# system.
#
# One file per session, at <state root>/sessions/<session id>/plugin-root,
# where <state root> is the directory run_paths.state_root() owns
# ($PIRATEGOAT_TOOLS_HOME when absolute, else ~/.pirategoat-tools). A single
# machine-wide /tmp file was last-writer-wins across every open session, so a
# dev checkout and the installed release overwrote each other's pointer before
# every command. Every shell in a session, subagents included, carries the same
# CLAUDE_CODE_SESSION_ID, so readers name the file by their own session id.
# Session directories whose pointer is older than a day are swept: a live
# session rewrites its pointer before every Bash call. Exit 0 on every path:
# a hook that fails must never block a Bash call.

[ -n "$CLAUDE_PLUGIN_ROOT" ] || exit 0

session="$CLAUDE_CODE_SESSION_ID"
if [ -z "$session" ]; then
    # The hook's stdin is the event JSON, which carries session_id.
    session=$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("session_id", ""))' 2>/dev/null)
fi
[ -n "$session" ] || exit 0
case "$session" in
    .|..|*[!A-Za-z0-9._-]*) exit 0 ;;   # never build a path from an unexpected id
esac

root="$PIRATEGOAT_TOOLS_HOME"
case "$root" in
    /*) ;;
    *) root="$HOME/.pirategoat-tools" ;;   # a relative override is ignored, as state_root() ignores it
esac
sessions="$root/sessions"

mkdir -p "$sessions/$session" 2>/dev/null || exit 0
printf '%s\n' "$CLAUDE_PLUGIN_ROOT" > "$sessions/$session/plugin-root" 2>/dev/null

find "$sessions" -mindepth 2 -maxdepth 2 -name plugin-root -mmin +1440 -print0 2>/dev/null \
    | while IFS= read -r -d '' stale; do rm -rf "$(dirname "$stale")"; done
exit 0
