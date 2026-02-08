#!/usr/bin/env bash
#
# Writes the pirategoat-tools plugin root path to a well-known location
# so reviewer agents can find scripts and shared protocols without searching.
#
# Runs as PreToolUse:Bash hook — executes before every Bash command.
# Uses CLAUDE_PLUGIN_ROOT (injected by Claude Code plugin system).
#

if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
    echo "$CLAUDE_PLUGIN_ROOT" > /tmp/.pirategoat-tools-root
fi
