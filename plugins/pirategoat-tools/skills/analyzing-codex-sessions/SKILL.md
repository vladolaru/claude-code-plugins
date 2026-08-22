---
name: analyzing-codex-sessions
description: Use when analyzing Codex CLI raw session logs, parsing rollout JSONL files, investigating Codex subagent behavior, extracting metrics from Codex threads, or debugging why a Codex agent failed. Triggers on ~/.codex/sessions paths, rollout-*.jsonl filenames, Codex thread IDs, or requests to understand what happened in a Codex run.
---

## Skill Directory

Resolve `SKILL_DIR` to the absolute directory containing this `SKILL.md`, as
shown by the current host, before using any bundled path below.
Before a shell command uses `$SKILL_DIR`, assign it in that command or replace
it with the resolved path. It is not a host-exported environment variable.

# Analyzing Codex Raw Sessions

## Overview

Codex CLI writes every thread — the root conversation and each subagent alike — as its own JSONL rollout file, one JSON object per line. Each line has a top-level `type`, and the two types that matter carry a nested `payload.type` that does the real discriminating.

Core principle: **`event_msg/item_completed` is the digested layer; `response_item` is the raw layer. Analyze items first — they carry semantic types with timing and exit codes already attached. Drop to `response_item` only for prompt text and reasoning content.**

A `CommandExecution` item hands you the command, its `exit_code`, and its `duration` in a single object. The equivalent question asked of `response_item` requires correlating a `custom_tool_call` with its `custom_tool_call_output` and reconstructing timing from entry timestamps. Start at the digested layer and stay there unless you need something it does not carry.

## When to Use

- Analyzing what happened in a Codex run (commands, failures, file changes, cost)
- Reconstructing a thread tree — which subagents a root spawned and what each did
- Extracting metrics (tokens, wall duration, command counts, failure counts) across Codex threads
- Debugging why a Codex subagent failed or produced poor results
- Comparing Codex reviewer roles (`code-reviewer`, `php-tests-reviewer`, …) across runs
- Building or maintaining Codex rollout analysis scripts

## Before You Start

| Your goal | Start here |
|-----------|-----------|
| Understand what a whole Codex run did | `codex_session_analyzer.py` — it resolves the root and walks its subagent tree for you |
| Analyze one thread you already have the id for | The rollout file whose name ends `-{thread-id}.jsonl`, then its `item_completed` entries |
| Find sessions for a specific project | Scan line 1 of each rollout in the date window and match `session_meta.payload.cwd` — there is no per-project directory |
| Compare metrics across threads or roles | `codex_session_metrics.py` |
| Debug a failing command | `item_completed` → `CommandExecution` items with non-zero `exit_code`; `stderr` and `aggregated_output` are inline |
| Recover the prompt text or the model's reasoning | `response_item` entries of type `message` and `reasoning` — items do not carry full prompts |
| Attribute cost to a thread | The **last** `event_msg/token_count` entry's `info.total_token_usage` |

## Session Data Locations

Codex stores rollouts under a single global root, partitioned by date:

```
~/.codex/sessions/YYYY/MM/DD/rollout-{ISO-8601-timestamp}-{thread-id}.jsonl
```

Example: `~/.codex/sessions/2026/08/22/rollout-2026-08-22T16-38-47-01a029b2-053e-7140-b8d0-c6581e7af083.jsonl`

Two properties of this layout drive every design decision downstream:

**There is no per-project partitioning.** The working directory is a field — `cwd` — inside the `session_meta` payload on line 1. Filtering by project therefore costs a read of line 1 of every candidate file. Claude Code gets that filter free from its directory names; Codex does not.

**Subagents are sibling files, not a subdirectory.** A subagent thread is a top-level rollout in the same `YYYY/MM/DD` folder as any other, indistinguishable by path. Its relationship to its parent lives only in `session_meta.payload.source`. There is nothing on disk grouping a run's threads together.

### Compared to Claude Code

| | Claude Code | Codex |
|---|---|---|
| Layout | `~/.claude/projects/{path-hash}/` | `~/.codex/sessions/YYYY/MM/DD/` |
| Project filter | Free, from the directory name | Requires reading line 1 of every file |
| Subagents | `{session}/subagents/agent-{id}.jsonl` | Sibling rollouts, linked by thread id |
| Entry discriminator | `type` (5 values) | `type` (7 values) + `payload.type` |
| Tool calls | `tool_use` / `tool_result` | `custom_tool_call` / `custom_tool_call_output`, plus `function_call` / `function_call_output` |
| Large tool output | Externalized to `tool-results/{id}.txt` | Inline in the rollout |
| Per-command timing | Must be correlated across entries | Present on the item itself |

`~/.codex/logs_2.sqlite` is a tracing and debug log, not a session index. It carries no conversation content and is multiple gigabytes. Do not reach for it.

## Finding Sessions

Discovery is a line-1 scan over a date window. Read the first line of each rollout, parse the `session_meta` payload, and you have the thread id, `cwd`, `originator`, `cli_version`, and full lineage without touching the rest of the file.

Measured on a machine with 10,162 rollouts (2,069 of them in a single month): one month of line-1 scanning costs ~5.3s wall, and a full-history scan extrapolates to ~25s. A few days is sub-second. That is why the bundled scripts take `--since` in days and default it to 7 rather than maintaining a persistent index — the date partitioning already makes recent queries cheap, and an index would buy a staleness story to solve a problem that mostly does not exist.

**Only finished sessions are trustworthy.** A rollout belonging to a live session is still being appended to while you read it, so any count you take from it is a count as of some arbitrary moment. The bundled scripts skip rollouts touched in the last 5 minutes; `--include-active` overrides that, with no consistency guarantee.

When the scan finds a rollout whose lineage cannot be determined — because `source` has an unexpected shape, or the file is truncated — treat it as a root thread and note it rather than dropping it.

## Entry Types

Each line has a top-level `type`. There are 7. Counts below come from a frozen snapshot of one representative 11,366-line worker rollout — treat them as proportions, not absolutes.

| `type` | Count | Analysis value |
|---|---|---|
| `event_msg` | 5,629 | **HIGH** — `item_completed` is the digested layer; `token_count` carries usage |
| `response_item` | 5,194 | **HIGH** — raw model turns, prompt text, reasoning content |
| `turn_context` | 266 | MEDIUM — per-turn model, sandbox and approval policy |
| `inter_agent_communication_metadata` | 263 | LOW — mostly `{"trigger_turn": true}` |
| `world_state` | 8 | LOW — keys `full`, `state` |
| `compacted` | 5 | MEDIUM — rewrites history; must be accounted for |
| `session_meta` | 1 | **HIGH** — line 1; identity, `cwd`, thread lineage |

`event_msg` breaks down by `payload.type` as `item_completed` 3,165, `token_count` 1,942, `task_started` 261, `task_complete` 261.

`response_item` breaks down by `payload.type` as `custom_tool_call` 1,571, `custom_tool_call_output` 1,571, `reasoning` 1,050, `message` 547, `agent_message` 263, `function_call` 96, `function_call_output` 96.

## Item Types

`item_completed` is the only `payload.type` that carries an `item`, so filtering on the item's own type alone is safe — you cannot accidentally match something else. Discriminate on **`payload.item.type`**.

| `item.type` | Count | Fields |
|---|---|---|
| `CommandExecution` | 1,163 | `command`, `cwd`, `duration`, `exit_code`, `parsed_cmd`, `status`, `stdout`, `stderr`, `aggregated_output`, `formatted_output`, `process_id`, `source`, `id` |
| `Reasoning` | 1,050 | `summary_text`, `raw_content`, `id` |
| `AgentMessage` | 539 | `content`, `phase`, `id` |
| `FileChange` | 404 | `changes`, `status`, `stdout`, `stderr`, `id` |
| `ContextCompaction` | 5 | `id` only |
| `SubAgentActivity` | 4 | `agent_thread_id`, `agent_path`, `kind`, `id` |

`CommandExecution` is the richest object in the format: command, exit code, and duration arrive together, so failure analysis and slow-command analysis need no cross-entry correlation at all. `duration` is per-command, not cumulative.

`FileChange.changes` is a dict keyed by absolute file path — see Gotchas.

## Thread Trees

Line 1 of every rollout is a `session_meta` entry. Its payload carries the thread's identity and its position in the tree:

```json
{
  "session_id": "01a0135a-962d-76f2-9234-e1f76cdf30f3",
  "id": "01a0159b-1de8-7453-a1db-de9518e0fe83",
  "parent_thread_id": "01a0135a-962d-76f2-9234-e1f76cdf30f3",
  "cwd": "/Users/jane/projects/my-app",
  "originator": "codex-tui",
  "cli_version": "0.147.0",
  "source": {
    "subagent": {
      "thread_spawn": {
        "parent_thread_id": "01a0135a-962d-76f2-9234-e1f76cdf30f3",
        "depth": 1,
        "agent_path": "/root/execute_mutation_0885",
        "agent_nickname": "Fermat the 3rd",
        "agent_role": "worker"
      }
    }
  }
}
```

There are three independent ways to reconstruct the tree:

1. **`(session_id, agent_path)`** — the tree position as a literal path string (`/root/execute_mutation_0885`), scoped by session. This is the primary mechanism, and the one the bundled scripts implement: the whole tree reconstructs from line 1 of each file, with no traversal. **The `session_id` half is mandatory** — see Gotchas.
2. **`parent_thread_id`** — a bottom-up link, but it carries two different relations depending on where it appears, so `ThreadMeta` splits it into `spawn_parent_thread_id` (from the spawn block: the thread that spawned me) and `resumed_from_thread_id` (payload level on a root: the thread I was resumed from). The resume meaning dominates real data — 4,047 of 4,290 resumed roots carry it — so a single merged field would answer "who spawned this?" with a resume pointer most of the time.
3. **`SubAgentActivity.agent_thread_id`** — a top-down link to a child, recorded in the parent's own log as an `item_completed` item.

Observed `agent_role` values: `root`, `default`, `worker`, `explorer`, `code-reviewer`, `php-tests-reviewer`, `e2e-tests-reviewer`. The reviewer roles correspond to pirategoat dispatches, which makes `agent_role` the Codex analogue of the `--agent {name}` convention in Claude Code bootstrap prompts — it is the field to group and filter on when comparing runs of the same kind.

## Turn Context

`turn_context` entries record the configuration in force for one turn, and it can change mid-thread — a resumed session or a model switch produces a new one. The fields worth reading:

| Field | Example | Why it matters |
|---|---|---|
| `model` | `gpt-5.6-terra` | Cost and capability attribution; do not assume one model per thread |
| `sandbox_policy` | `{"type": "danger-full-access"}` | Explains what the agent was permitted to touch |
| `approval_policy` | `never` | Explains why a command ran unprompted, or why the agent stalled |
| `personality` | `pragmatic` | Behavioral profile in force |
| `cwd`, `workspace_roots` | `/Users/jane/projects/my-app` | Per-turn working directory, which can differ from `session_meta.cwd` |
| `effort`, `collaboration_mode.settings.reasoning_effort` | `medium` | Reasoning budget, which moves token counts substantially |

When reporting "the model used" for a thread, say which turn you took it from, or take the last one and say so.

## Token Accounting

`event_msg/token_count` entries carry `info.total_token_usage` (cumulative for the thread so far), `info.last_token_usage` (that turn only), `info.model_context_window`, and a `rate_limits` block.

**Take the last `token_count` entry for a thread total. Never sum them.** `total_token_usage` is already cumulative, so summing multiply-counts by roughly the number of entries — and there are a lot of them: the 11,366-line rollout above carries 1,942. Summing that thread's totals would overstate its cost by three orders of magnitude.

If you want per-turn cost, sum `info.last_token_usage` instead — that field is the non-cumulative one.

## Gotchas

| Trap | Reality |
|------|---------|
| The item discriminator is `item_type` | **No — it is `payload.item.type`.** The `item_type` spelling occurs zero times in real data. Reading it returns `None`, so the failure presents as "no items found" rather than raising, and a parser using it reports a clean empty result. |
| `session_meta.payload.source` has a stable shape | **No.** It is usually a dict (`{"subagent": {...}}`) and sometimes a bare string. Naive `.source.subagent.thread_spawn` access broke on roughly 200 of 2,069 sampled August files. Type-check before descending. |
| `agent_path` is globally unique | **No — it is scoped to one session.** Every root thread is `/root`, and paths like `/root/task1_quality_review` recur across sessions. Keying a tree on the bare path merges sessions: measured over six days of real rollouts, one root was reported as having 994 children, all 994 belonging to other sessions. Key on `(session_id, agent_path)`. Verified fix: parent and child share `session_id` in every one of 1,836 sampled child threads. |
| `FileChange.changes` is a list of changed files | **No — it is a dict keyed by absolute file path** (dict in 405/405 sampled items). Code testing `isinstance(changes, list)` silently counts one file per item and undercounts every multi-file edit. |
| Sum the `token_count` entries for a thread total | **No.** `info.total_token_usage` is cumulative — take the last entry. One 11,366-line rollout carries 1,942 of them. |
| A rollout is a stable file | **Not while its session is live.** One inspected rollout gained 349 lines mid-inspection. Only finished sessions give trustworthy numbers; skip anything touched in the last few minutes. |
| Wall span is working time | **No.** A thread's first-to-last-entry span includes every idle gap, and resumed threads carry huge ones — real resumed threads report ~335,900 seconds (~93 hours). Report it as span, never as effort. |
| An empty root thread means the parse failed | **No.** A Codex root thread is nearly empty by design: real roots show 0 commands and 0 tokens because the orchestrator delegates everything to subagents. Expected, not a bug. |
| `compacted` entries are harmless metadata | **No.** They carry `replacement_history` and rewrite the conversation. Metrics that walk raw entries double-count across a compaction boundary. |
| Large tool output lives in a side file | Not in Codex — `stdout`, `stderr`, and `aggregated_output` are inline on the item. Rollouts get large as a result. |

## Existing Analysis Scripts

Use `SKILL_DIR`, resolved from the skill location shown by the current host. The **plugin root** is two levels up from that path. Derive it once and use it for all script references:

```bash
# PLUGIN_ROOT = base directory minus "skills/analyzing-codex-sessions"
# Example: if base dir is ~/.claude/plugins/cache/.../pirategoat-tools/1.43.3/skills/analyzing-codex-sessions
# then PLUGIN_ROOT is ~/.claude/plugins/cache/.../pirategoat-tools/1.43.3
SKILL_DIR="<absolute path to the directory containing this SKILL.md>"
PLUGIN_ROOT="$SKILL_DIR/../.."
```

| When you need to... | Use |
|---------------------|-----|
| Trace one thread tree in depth — commands, exit codes, file changes, per-node timing and tokens | `$SKILL_DIR/../../scripts/analysis/codex_session_analyzer.py` |
| Compare metrics across threads, or roll up by `agent_role` | `$SKILL_DIR/../../scripts/analysis/codex_session_metrics.py` |
| Analyze a Claude Code session instead | The sibling `analyzing-cc-sessions` skill and its `session_analyzer.py` / `session_metrics.py` |
| Do something not covered above | Write a targeted script against the entry and item structure documented here |

Both scripts share these flags: `--sessions-dir` (default `~/.codex/sessions`), `--cwd` (exact working-directory match), `--since` (days back, default 7), `--agent` (comma-separated `agent_role` list), `--limit` (default 20), `--format`, `--output`, and `--include-active`. `--format` values differ by script because the outputs differ: the analyzer emits `text` or `json`, the metrics script emits `markdown`, `json`, or `both`. Metric names and output shapes match the Claude Code `session_metrics.py`, so figures from both tools can be read side by side.

### Digging into one specific session

This is the common case, and it needs no window at all. `--thread-id` accepts the session's own id, its root thread, or **any subagent inside it**, searches the whole archive, and ignores `--since`:

```bash
python3 "$PLUGIN_ROOT/scripts/analysis/codex_session_analyzer.py" --thread-id 019f50c7-b8d8-77a0-9e3d-e3495401787a
python3 "$PLUGIN_ROOT/scripts/analysis/codex_session_metrics.py" --thread-id 019f50c7-b8d8-77a0-9e3d-e3495401787a --format markdown
```

The report names the thread you asked for, not a different one: naming a subagent reports that subagent and prints its `session_id` so you can re-run against the whole session. Lookup is a filename glob first — the thread id is part of the rollout filename — falling back to a content scan, so it stays fast without depending on the naming convention.

**Neither tool applies a window by default.** With no `--thread-id` and no `--since` both exit 2 and tell you to pick a scope. That is deliberate: a too-narrow window returns almost nothing, which is indistinguishable from having done no work, and silently guessing one would hide real sessions.

**Finding the id when you don't have it:** sweep with `codex_session_metrics.py --since <days>` to list recent sessions with their thread ids, then dig with `--thread-id`.

**Large sessions are capped.** The analyzer deep-scans the 20 largest subagents by default, because reading them all means reading their whole rollouts — one real session has 621 subagents totalling 11 GB, about 85 seconds of I/O for a list too long to read. Pass `--children N`, or `--children 0` for all.

**`--since` selects whole sessions, not individual threads.** A session qualifies when one of its root rollouts falls in the window, and then every thread of that session is included regardless of its own date. This matters because a subagent is frequently dated days after the session that spawned it — filtering threads by their own date would return subagents without their root and cut trees in half. The guarantee that makes it safe: no subagent is ever dated earlier than its session's first root rollout (0 exceptions in 5,248 sampled subagents), so a rooted session's whole tree is already inside the window.

**Threads whose session started before the window are excluded, and both CLIs say so.** Look for the `Note:` line — `637 thread(s) from 4 session(s) excluded because the session started before the window; widen --since to include them.` A thin result with that note means widen the window, not that the run had no subagents. The default is 30 days.

**`total_tokens` counts billed input including re-sent context, not unique tokens.** A real subagent shows 5.1 billion tokens across 35,434 turns in one 86 MB rollout — each turn re-sends roughly 178k of context, 98% of it cache reads. The figure is correct, not a runaway; read it alongside `cached_pct` before drawing any cost conclusion.

**`agent_role` is absent on more than half of real subagents** — `agent_role: null` in the spawn block, 2,812 of 5,326 sampled. Those are reported as `unknown`. Do not default them to `root`: that claims a subagent IS the session root and corrupts both role filtering and the by-role rollup.

**Resuming a session does not start a new one.** A resume writes an additional root rollout that keeps the original `session_id` but takes a fresh thread id, so one session commonly has many — 4,920 root rollouts across 631 sessions on a real machine. The tools represent a session once, by its original root (the rollout whose thread id equals the session id), and list the rest as resumes. **Resume tokens are never summed into the session total**: a resume replays the prior context, so its token count is largely re-sent rather than new work. One real session showed six resumes carrying 10-21M tokens each while executing zero commands, against the original's 104M tokens and 1,157 commands.

### Quick Start

```bash
SKILL_DIR="<absolute path to the directory containing this SKILL.md>"
PLUGIN_ROOT="$SKILL_DIR/../.."

# Trace the newest thread tree for one project
python3 "$PLUGIN_ROOT/scripts/analysis/codex_session_analyzer.py" \
    --cwd /path/to/project --since 30

# Compare code-reviewer threads over the last month
python3 "$PLUGIN_ROOT/scripts/analysis/codex_session_metrics.py" \
    --agent code-reviewer --since 30 --format markdown
```
