#!/usr/bin/env bash
# Pre-tool-logger: logs every PreToolUse event to hook-log.jsonl.
# Always allows (exit 0, no stdout). Runs alongside yoloing-safe.
set -euo pipefail

input=$(cat)
tool_name=$(echo "$input" | jq -r '.tool_name // empty')
command=$(echo "$input" | jq -r '.tool_input.command // empty')
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')
agent_id=$(echo "$input" | jq -r '.agent_id // empty')
timestamp=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)

log_dir="/Users/testuser/results"
mkdir -p "$log_dir"

echo "{\"ts\":\"$timestamp\",\"event\":\"PreToolUse\",\"tool\":\"$tool_name\",\"command\":\"$command\",\"file_path\":\"$file_path\",\"agent_id\":\"$agent_id\"}" \
    >> "$log_dir/hook-log.jsonl"

exit 0
