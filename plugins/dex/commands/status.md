---
description: Show knowledge health report for the current project
---

# /dex:status

Show a knowledge health report for the current project. Fully self-contained — no input needed, no modifications made.

## Step 1: Discover Project Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill.

## Step 2: Gather Metrics

For each component, gather:

**CLAUDE.md:**
- Line count
- Location (root or `.claude/CLAUDE.md`)
- Count of nested CLAUDE.md files (glob for `**/CLAUDE.md` excluding node_modules, vendor, etc.)

**Knowledge directories** (for each of `learnings/`, `patterns/`, `decisions/`):
- Whether the directory exists
- Number of `.md` files
- Most recent file (by filename date prefix `YYYY-MM-DD`)
- Oldest file (by filename date prefix)

## Step 3: Present Report

Format the report as follows:

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
```

**Adjustments:**
- If `.claude/docs/` doesn't exist, report: "No knowledge directory found. Run /dex:init to set up."
- If a subdirectory is empty, report: "[category]:  0 docs  (empty)"
- If a subdirectory doesn't exist, report: "[category]:  not set up"
- If CLAUDE.md is 500+ lines, add a warning: "CLAUDE.md is over budget — consider extracting sections."
- Omit Latest/Oldest lines for empty directories
