# Agent Analysis & Observability

Tools and references for analyzing reviewer agent behavior from raw Claude Code session logs.

## Scripts

### `scripts/analyze-reviewer-sessions.py`

Parses subagent JSONL logs from Claude Code sessions to extract tool call sequences, categorize behavior patterns, and generate efficiency metrics.

**Usage:**

```bash
# The --sessions-dir value is your project's absolute path with '/' replaced by '-':
# e.g. /Users/alice/code/myproject → ~/.claude/projects/-Users-alice-code-myproject

# Analyze all patterns-reviewer dispatches from the last 20 sessions
python3 plugins/pirategoat-tools/scripts/analyze-reviewer-sessions.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --agent patterns-reviewer \
    --max-sessions 20

# JSON output for programmatic analysis
python3 plugins/pirategoat-tools/scripts/analyze-reviewer-sessions.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --agent security-reviewer \
    --format json

# Analyze all agents (no --agent filter)
python3 plugins/pirategoat-tools/scripts/analyze-reviewer-sessions.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --max-sessions 5

# Write to file
python3 plugins/pirategoat-tools/scripts/analyze-reviewer-sessions.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --agent patterns-reviewer \
    --output /tmp/patterns-analysis.txt
```

**What it extracts per dispatch:**
- Tool call sequence with categorization (git-grep, git-show, git-log, git-diff, bootstrap, file-read-bash, file-list, other)
- Dispatch classification (reviewer vs reconciliator vs crashed)
- File read patterns (unique files, duplicates, most-read files)
- Output file details (Write tool usage, content size, finding counts)
- Aggregate statistics (tool call breakdown, cross-dispatch patterns)

**Output formats:**
- `text` (default) — human-readable report with full tool sequences
- `json` — structured data for downstream analysis

### `scripts/extract-session-metrics.py`

General-purpose tool for extracting operational metrics (runtime, model, cache tokens, verdict) from session transcripts. Documented in-file.

## Session Data Locations

Claude Code stores session transcripts at:

```
~/.claude/projects/<encoded-project-path>/   # absolute path with '/' replaced by '-'
├── {session-uuid}.jsonl           # Main session transcript
├── {session-uuid}/
│   ├── subagents/
│   │   └── agent-{id}.jsonl       # Subagent logs (one per dispatched agent)
│   └── tool-results/
│       └── {hash}.txt             # Cached tool results
```

Each subagent JSONL file contains one JSON object per line, with the first line being the dispatch message (containing the prompt). Subsequent lines alternate between assistant tool calls and tool results.
