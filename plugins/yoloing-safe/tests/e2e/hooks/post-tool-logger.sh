#!/usr/bin/env bash
# Post-tool-logger: logs every PostToolUse event to hook-log.jsonl.
set -euo pipefail

input=$(cat)
tool_name=$(echo "$input" | jq -r '.tool_name // empty')
agent_id=$(echo "$input" | jq -r '.agent_id // empty')
timestamp=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)

log_dir="/Users/testuser/results"
mkdir -p "$log_dir"

echo "{\"ts\":\"$timestamp\",\"event\":\"PostToolUse\",\"tool\":\"$tool_name\",\"agent_id\":\"$agent_id\"}" \
    >> "$log_dir/hook-log.jsonl"

exit 0
