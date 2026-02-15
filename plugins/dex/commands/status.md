---
description: Show knowledge health report for the current project
---

# /dex:status

Show a read-only knowledge health report for the current project. This command reads and reports — it modifies nothing.

## Step 1: Discover Project Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill.

## Step 2: Gather Metrics

For each component, gather:

**CLAUDE.md:**
- Line count
- Location (root or `.claude/CLAUDE.md`)
- Count of nested CLAUDE.md files (glob for `**/CLAUDE.md` excluding node_modules, vendor, etc.)

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
  Latest:         [date] — [title from filename slug]
  Oldest:         [date] — [title from filename slug]

Patterns:         [count] docs  (.claude/docs/patterns/)
  Latest:         [date] — [title from filename slug]
  Oldest:         [date] — [title from filename slug]

Decisions:        [count] docs  (.claude/docs/decisions/)
  Latest:         [date] — [title from filename slug]
  Oldest:         [date] — [title from filename slug]

Research:         [count] docs  (.claude/docs/research/)
  Latest:         [date] — [title from filename slug]
  Oldest:         [date] — [title from filename slug]
```

**Adapt for missing infrastructure:**

| Condition | Report |
|---|---|
| `.claude/docs/` missing | "No knowledge directory found. Run /dex:init to set up." |
| Subdirectory empty | "[category]: 0 docs (empty)" — omit Latest/Oldest |
| Subdirectory missing | "[category]: not set up" — omit Latest/Oldest |
| CLAUDE.md 500+ lines | Append warning: "CLAUDE.md is over budget — consider extracting sections." |

## Step 4: Freshness Analysis

Parse the `YYYY-MM-DD` date prefix from each document filename across all four subdirectories. Calculate the age of each document relative to today.

**Staleness threshold:** 90 days. Any document older than 90 days is considered stale.

If stale documents exist, append a freshness section to the report:

```
Freshness:
  Stale (>90 days):  [count] docs
  Consider reviewing: [list oldest 3 filenames]
```

**Freshness warnings:**

| Condition | Warning |
|---|---|
| 1+ docs older than 90 days | "N docs are older than 90 days — consider reviewing for accuracy." |
| >50% of docs are stale | "Most knowledge is over 90 days old — consider reviewing and updating stale documents." |
| No stale docs | Omit the freshness section entirely |

Stop after presenting the report. This command is read-only.
