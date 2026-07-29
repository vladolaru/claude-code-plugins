---
name: status
description: "Show knowledge health report for the current project"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/status.md -->

## Codex Host Adapter

This skill is generated from the canonical Claude Code command named above. To execute it in Codex:

1. Treat the text supplied after the skill mention as the invocation arguments. Substitute that exact text for `${CODEX_SKILL_ARGUMENTS}` before executing shell commands.
2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the plugin root is two directories above the directory containing this `SKILL.md`.
3. Assign both variables explicitly in any shell call that uses them. Codex does not export these instruction variables automatically.
4. Use Codex's available user-input and subagent tools when the workflow requests them.
5. Follow the canonical workflow below without skipping its gates or artifact checks.

## Canonical Workflow


# $dex:status

Show a read-only knowledge health report for the current project. This command reads and reports - it modifies nothing.

## Step 1: Discover Project Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill.

## Step 2: Gather Metrics

For each component, gather:

**CLAUDE.md:**
- Line count
- Location (root or `.claude/CLAUDE.md`)
- Count of nested CLAUDE.md files (glob for `**/CLAUDE.md` excluding dependency and build directories - node_modules, vendor, .git, dist, build)

**Knowledge directories** (for each of `learnings/`, `patterns/`, `decisions/`, `research/`):
- Whether the directory exists
- Number of `.md` files
- Most recent file (by filename date prefix `YYYY-MM-DD`)
- Oldest file (by filename date prefix)

## Step 3: Present Report

Use this exact format:

```
Project Knowledge Health

CLAUDE.md:        [line_count]/500 lines ([location])
                  [+ N nested CLAUDE.md files, if any]

Learnings:        [count] docs  (.claude/docs/learnings/)
  Latest:         [date] - [title from filename slug]
  Oldest:         [date] - [title from filename slug]

Patterns:         [count] docs  (.claude/docs/patterns/)
  Latest:         [date] - [title from filename slug]
  Oldest:         [date] - [title from filename slug]

Decisions:        [count] docs  (.claude/docs/decisions/)
  Latest:         [date] - [title from filename slug]
  Oldest:         [date] - [title from filename slug]

Research:         [count] docs  (.claude/docs/research/)
  Latest:         [date] - [title from filename slug]
  Oldest:         [date] - [title from filename slug]
```

**Adapt for missing infrastructure:**

| Condition | Report |
|---|---|
| `.claude/docs/` missing | "No knowledge directory found. Run $dex:init to set up." |
| Subdirectory empty | "[category]: 0 docs (empty)" - omit Latest/Oldest |
| Subdirectory missing | "[category]: not set up" - omit Latest/Oldest |
| CLAUDE.md 500+ lines | Append warning: "CLAUDE.md is over budget - consider extracting sections." |
| Migration mismatch detected | Append warning: "Knowledge is in `.claude/docs/` but project uses AGENTS.md - run `$dex:init` to migrate to `.ai/docs/`." |

## Step 4: Freshness Analysis

Parse the `YYYY-MM-DD` date prefix from each document filename across all four subdirectories. Calculate the age of each document relative to today.

**Staleness threshold:** 90 days. Any document older than 90 days is considered stale.

If stale documents exist, append a freshness section to the report:

```
Freshness:
  Stale (>90 days):  [count] docs
  Consider reviewing: [list oldest 3 filenames]
```

**Freshness warnings** (use the first matching row):

| Condition | Warning |
|---|---|
| >50% of docs are stale | "Most knowledge is over 90 days old - consider reviewing and updating stale documents." |
| 1+ docs older than 90 days | "N docs are older than 90 days - consider reviewing for accuracy." |
| No stale docs | Omit the freshness section entirely |

Stop after presenting the report. This command is read-only.
