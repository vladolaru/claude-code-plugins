---
name: analyzing-cc-sessions
description: Use when analyzing Claude Code raw session logs, parsing JSONL transcripts, investigating agent behavior, extracting metrics from session data, debugging subagent dispatches, or building session analysis tools. Triggers on session IDs, JSONL paths, ~/.claude/projects references, or requests to understand what happened in a CC session.
---

## Skill Directory

Resolve `SKILL_DIR` to the absolute directory containing this `SKILL.md`, as
shown by the current host, before using any bundled path below.
Before a shell command uses `$SKILL_DIR`, assign it in that command or replace
it with the resolved path. It is not a host-exported environment variable.

# Analyzing Claude Code Raw Sessions

## Overview

Claude Code stores every conversation as JSONL transcripts — one JSON object per line. Main sessions track the full human-agent dialogue. Subagent dispatches get their own JSONL files. Large tool outputs are persisted separately as text files.

Core principle: **Main session = orchestration log. Subagent files = the real work. Tool-results = the large payloads.** Most analysis targets subagent behavior, not the main session.

## When to Use

- Analyzing what happened in a CC session (efficiency, behavior, errors)
- Parsing subagent logs for tool call patterns or waste
- Extracting metrics (tokens, duration, tool counts, findings)
- Debugging why a subagent failed or produced poor results
- Building or maintaining session analysis scripts
- Correlating main session dispatches with subagent execution

## Before You Start

| Your goal | Start here |
|-----------|-----------|
| Understand what happened in a session | Main session JSONL → find Agent tool calls → follow to subagent files |
| Analyze a specific subagent's behavior | Subagent JSONL directly (`{session}/subagents/agent-{id}.jsonl`) |
| Extract metrics across sessions | Existing analysis scripts (see below) |
| Debug why a subagent failed | Subagent JSONL → last assistant entry → check for errors in preceding tool results |
| Correlate main session ↔ subagent | Main session Agent tool_use → progress entries with agentId → subagent file |

## Session Data Locations

### Project Directory

Sessions are stored per-project at:

```
~/.claude/projects/{path-hash}/
```

The `{path-hash}` is the absolute project path with `/` replaced by `-`, leading `-` stripped:

```
/Users/jane/projects/my-app
→ -Users-jane-projects-my-app
```

### Directory Structure

```
~/.claude/projects/{path-hash}/
├── {session-uuid}.jsonl              # Main session transcript
├── {session-uuid}/
│   ├── subagents/
│   │   ├── agent-{id}.jsonl          # One per dispatched subagent
│   │   └── agent-acompact-{id}.jsonl # System compaction agents (SKIP these)
│   └── tool-results/
│       └── {tool_use_id}.txt         # Large tool outputs persisted here
```

### Resolving a Project Directory

```python
from pathlib import Path

def resolve_project_dir(project_path: str) -> Path:
    """Convert project path to ~/.claude/projects/ hash directory."""
    absolute = Path(project_path).resolve()
    hash_name = str(absolute).replace("/", "-").lstrip("-")
    return Path.home() / ".claude" / "projects" / hash_name
```

### Finding Recent Sessions

Sessions are `.jsonl` files sorted by modification time. Only sessions with a `{session-uuid}/subagents/` subdirectory contain subagent data worth analyzing.

```python
from pathlib import Path

def find_sessions_with_subagents(project_dir: Path, limit: int = 10):
    """Find recent sessions that have subagent dispatches."""
    candidates = []
    for jsonl in project_dir.glob("*.jsonl"):
        subagents_dir = project_dir / jsonl.stem / "subagents"
        if subagents_dir.is_dir() and any(subagents_dir.iterdir()):
            candidates.append((jsonl.stat().st_mtime, jsonl.stem))
    candidates.sort(reverse=True)
    return [sid for _, sid in candidates[:limit]]
```

## Main Session JSONL Structure

Each line is a JSON object with a `type` field. There are 5 entry types:

### Entry Types

| Type | Purpose | Frequency | Analysis Value |
|------|---------|-----------|----------------|
| `assistant` | Model responses (text, tool calls, thinking) | ~15-20% | **HIGH** — tool sequences, reasoning |
| `user` | User messages + tool results | ~10-15% | **HIGH** — tool results, user corrections |
| `progress` | Streaming progress (agent, bash, hooks) | ~60-70% | **LOW** — mostly noise, but agent_progress has agentId |
| `system` | Turn duration, metadata | ~1-2% | LOW — timing data |
| `file-history-snapshot` | File backup snapshots | ~2-3% | SKIP |

### Common Fields (All Entry Types)

```json
{
  "type": "assistant|user|progress|system|file-history-snapshot",
  "uuid": "unique-entry-id",
  "parentUuid": "parent-entry-id",
  "timestamp": "2026-03-01T14:30:00.000Z",
  "sessionId": "session-uuid",
  "cwd": "/current/working/directory",
  "gitBranch": "feature/branch-name",
  "version": "1.0.43"
}
```

### Assistant Entries

```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "model": "claude-opus-4-6",
    "content": [
      {"type": "thinking", "text": "...reasoning..."},
      {"type": "text", "text": "...visible response..."},
      {"type": "tool_use", "id": "toolu_01ABC...", "name": "Read", "input": {"file_path": "/path"}}
    ],
    "usage": {"input_tokens": 1234, "output_tokens": 567},
    "stop_reason": "end_turn|tool_use"
  }
}
```

**Content block types in assistant messages:**
- `thinking` — extended thinking (main session only — see Gotchas). Field name is `thinking`, not `text`. Content is redacted (empty string + `signature` field for verification).
- `text` — visible text response
- `tool_use` — tool invocation with `id`, `name`, `input`

### User Entries

User entries carry two kinds of content:

**1. Human messages** — `content` is a plain string:

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": "Fix the authentication bug"
  }
}
```

**2. Tool results** — `content` is a list of `tool_result` blocks:

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "toolu_01ABC...",
        "content": "file contents here...",
        "is_error": false
      }
    ]
  }
}
```

**Key:** Match `tool_result.tool_use_id` to `tool_use.id` in the preceding assistant message to pair calls with results.

### Progress Entries

High-volume, mostly noise. Three subtypes via `data.type`:

| `data.type` | Contains | When to Use |
|---|---|---|
| `agent_progress` | `agentId`, streaming messages | Correlating which agentId maps to which subagent file |
| `bash_progress` | Streaming bash output | Rarely useful (final output is in tool_result) |
| `hook_progress` | Hook execution events | Debugging hook failures |

### Tool Results Persistence

When a tool result exceeds ~30KB, CC persists it to `tool-results/{tool_use_id}.txt` instead of embedding it inline. The tool_result content in the JSONL will reference this file.

**This matters for analysis because:** Bootstrap outputs, large file reads, and git command results often exceed this threshold. You'll see the tool_result in the JSONL but the actual content is in the separate file.

## Subagent JSONL Structure

Subagent files at `{session}/subagents/agent-{id}.jsonl` have a **simpler structure** than main sessions:

- **No `progress` entries** — just alternating `user` and `assistant` messages
- **First line** = dispatch prompt (the `user` message with the task)
- **Subsequent lines** = alternating assistant tool calls and user tool results
- **Similar content block format** as main session (tool_use, tool_result, text) but **no `thinking` blocks** — extended thinking is stripped from subagent JSONL even when it occurs (see Gotchas)

```
Line 0: user    → dispatch prompt (contains the full task/instructions)
Line 1: assistant → first response (may include text + tool_use)
Line 2: user    → tool results
Line 3: assistant → next response
...
Line N: assistant → final response (text with completion message)
```

### Selecting Task-Relevant Agents

Process only files matching `agent-{id}.jsonl` where `{id}` is a numeric or UUID identifier. Files named `agent-acompact-*.jsonl` are internal compaction agents — exclude them:

```python
def is_task_agent(filename: str) -> bool:
    """Return True for task-relevant agent files, False for system agents."""
    return filename.startswith("agent-") and "-acompact-" not in filename
```

### Identifying Agent Type

The dispatch prompt (first user message) usually contains the agent type:

```python
def infer_agent_type(prompt: str) -> str:
    """Infer agent type from dispatch prompt keywords."""
    patterns = {
        "security-reviewer": ["security review", "security issues"],
        "patterns-reviewer": ["pattern consistency", "codebase consistency"],
        "code-reviewer": ["code quality", "general review"],
        "Explore": ["explore", "find files", "search code"],
        "Plan": ["plan", "design", "architect"],
    }
    prompt_lower = prompt.lower()
    for agent, keywords in patterns.items():
        if any(kw in prompt_lower for kw in keywords):
            return agent
    return "unknown"
```

For reviewer agents dispatched via `bootstrap.py`, the prompt always contains `--agent {agent-name}`.

### Correlating Main Session ↔ Subagent

In the main session, an `Agent` tool call creates a subagent. The correlation chain:

1. Main session: `tool_use` with `name: "Agent"` → has an `id` (tool_use_id)
2. Main session: `progress` entries with `data.type: "agent_progress"` → contain `data.agentId`
3. Subagent file: named `agent-{agentId}.jsonl`
4. Main session: `tool_result` with matching `tool_use_id` → contains the agent's final output

## Parsing Recipes

Expect imperfect data. Session JSONL files regularly contain empty lines, truncated entries, and `content` fields that switch between string and list types within the same file. Every parser must handle `json.JSONDecodeError` per line (skip and continue), type-check `content` before iterating (string vs list), and treat missing fields as absent rather than raising errors.

### Extract All Tool Calls from a Subagent

```python
import json

def extract_tool_calls(filepath: str) -> list[dict]:
    """Extract ordered list of tool calls from a subagent JSONL."""
    calls = []
    with open(filepath) as f:
        for line in f:
            entry = json.loads(line.strip())
            msg = entry.get("message", {})
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append({
                        "name": block.get("name"),
                        "id": block.get("id"),
                        "input": block.get("input", {}),
                    })
    return calls
```

### Categorize Bash Commands

```python
def categorize_bash(command: str) -> str:
    """Classify a Bash command into a semantic category."""
    if "git grep" in command: return "git-grep"
    if "git show" in command: return "git-show"
    if "git log" in command:  return "git-log"
    if "git diff" in command: return "git-diff"
    if "bootstrap" in command or "python3" in command: return "bootstrap"
    if any(x in command for x in ["cat ", "head ", "tail "]): return "file-read-bash"
    if "ls " in command or "find " in command: return "file-list"
    return "other"
```

### Extract Token Usage

**IMPORTANT:** `input_tokens` alone is misleading — it only counts non-cached input. Real input cost = `input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`. The `output_tokens` field includes both visible text and hidden thinking tokens (see Gotchas).

```python
def extract_token_usage(filepath: str) -> dict:
    """Sum token usage across all assistant turns, including cache tokens."""
    total_input = 0
    total_output = 0
    total_cache_create = 0
    total_cache_read = 0
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = entry.get("message", {})
            if isinstance(msg, dict):
                usage = msg.get("usage", {})
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)
                total_cache_create += usage.get("cache_creation_input_tokens", 0)
                total_cache_read += usage.get("cache_read_input_tokens", 0)
    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_creation_input_tokens": total_cache_create,
        "cache_read_input_tokens": total_cache_read,
        "effective_input": total_input + total_cache_create + total_cache_read,
    }
```

## Existing Analysis Scripts

Use `SKILL_DIR`, resolved from the skill location shown by the current host.
The **plugin root** is two levels up from that path. Derive it once and use it
for all script references:

```bash
# PLUGIN_ROOT = base directory minus "skills/analyzing-cc-sessions"
# Example: if base dir is ~/.claude/plugins/cache/.../pirategoat-tools/1.43.3/skills/analyzing-cc-sessions
# then PLUGIN_ROOT is ~/.claude/plugins/cache/.../pirategoat-tools/1.43.3
SKILL_DIR="<absolute path to the directory containing this SKILL.md>"
PLUGIN_ROOT="$SKILL_DIR/../.."
```

| When you need to... | Use |
|---------------------|-----|
| Trace tool call sequences for a specific agent type | `$SKILL_DIR/../../scripts/analysis/session_analyzer.py` |
| Compare metrics (tokens, duration, findings) across sessions | `$SKILL_DIR/../../scripts/analysis/session_metrics.py` |
| Do something not covered above | Write a targeted script using the parsing recipes in this skill |

### Quick Start

```bash
# Derive plugin root from SKILL_DIR
SKILL_DIR="<absolute path to the directory containing this SKILL.md>"
PLUGIN_ROOT="$SKILL_DIR/../.."

# Analyze specific agent type across recent sessions
python3 "$PLUGIN_ROOT/scripts/analysis/session_analyzer.py" \
    --sessions-dir ~/.claude/projects/<project-path-hash> \
    --agent patterns-reviewer --limit 20

# Extract metrics from a specific session
python3 "$PLUGIN_ROOT/scripts/analysis/session_metrics.py" \
    --sessions-dir ~/.claude/projects/<project-path-hash> \
    --limit 5
```

## Common Analysis Patterns

### Efficiency Analysis

Extract all tool calls first, then classify each one in a single pass. Focus on boundaries between phases (bootstrap → search → analysis → write) — waste clusters at phase transitions where the agent re-orients.

For each subagent dispatch, classify every tool call as productive or wasted:

| Category | Productive | Wasted |
|----------|-----------|--------|
| Bootstrap | First successful run | Retries, cascade reads |
| git grep | Unique searches with results | Duplicate queries, bare symbol searches |
| git show | Files needed for analysis | Same-directory files read individually |
| Read | Unique files for context | Re-reads of own output, tool-results re-reads |
| Write | Successful output writes | API hallucination attempts, wrong method names |
| Post-write | — | Verification reads after save() succeeds |

**Efficiency % = productive calls / total calls**

Typical ranges: 50-85% depending on agent and PR size.

### Common Waste Patterns (Ranked by Impact)

1. **API hallucination** — Wrong method names on first write attempt (e.g., `add_finding()` instead of `add_issue()`). Agent introspects, retries. ~3 wasted calls/dispatch.
2. **Bootstrap size cascade** — Output >30KB gets persisted; agent re-reads the persisted file which is even larger; cascades until agent uses offset/limit. ~2-3 wasted calls on large PRs.
3. **Post-write verification** — Reading output files after `save()` already confirmed success. ~1-2 calls/dispatch.
4. **Duplicate searches** — Same `git grep` pattern run multiple times. Often 2-4 duplicates per dispatch.
5. **Sibling tool call cascade** — When parallel tool calls include one that fails (e.g., file path error), ALL sibling calls in the batch fail with "Sibling tool call errored".

### Cross-Session Comparison

When comparing multiple sessions for the same agent type, track:

- Tool call count distribution (min/max/avg)
- Finding count vs tool calls (efficiency ratio)
- Model used (Opus vs Sonnet vs Haiku — different cost profiles)
- Runtime (first_timestamp to last_timestamp)
- Verdict distribution (APPROVE / COMMENT / REQUEST_CHANGES)

## Gotchas

| Trap | Reality |
|------|---------|
| `content` is always a list | No — human messages use plain string, tool results use list |
| Progress entries are useful | Mostly noise (~60-70% of entries). Only `agent_progress` has useful `agentId` |
| All `agent-*.jsonl` files are real agents | `agent-acompact-*` files are system compaction agents — use `is_task_agent()` to filter |
| Tool result content is always inline | Large outputs (>~30KB) are persisted to `tool-results/{id}.txt` |
| Session JSONL = chronological | Mostly yes, but parallel tool calls create interleaved entries |
| `model` is on the entry | No — `model` is inside `message` on assistant entries: `entry.message.model` |
| `usage` is on the entry | Sometimes on the entry itself, sometimes inside `message.usage` — check both |
| Subagents have `thinking` blocks | **No.** Thinking blocks are stripped from subagent JSONL entirely. Main session records them but with redacted content (empty `thinking` field + `signature`). To detect thinking in subagents, compare `output_tokens` to visible output (text chars + tool_use input chars) / 4. A gap of 20-40% is typical and represents hidden thinking tokens. |
| `input_tokens` = total input cost | **No.** `input_tokens` only counts non-cached tokens (often single digits). Real input = `input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`. Using `input_tokens` alone will undercount by 99%+. |
| `output_tokens` = visible output | **No.** `output_tokens` includes both visible text/tool_use AND hidden extended thinking tokens. There is no separate field for thinking tokens in the usage data. |
