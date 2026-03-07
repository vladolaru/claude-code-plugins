#!/usr/bin/env bash
# Prompt-timestamp: injects a timestamp system message on each prompt.
# Tests that UserPromptSubmit hooks don't interfere with PreToolUse hooks.
timestamp=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)

cat <<EOF
{
  "continue": true,
  "suppressOutput": false,
  "systemMessage": "[e2e-test] Prompt received at $timestamp"
}
EOF
