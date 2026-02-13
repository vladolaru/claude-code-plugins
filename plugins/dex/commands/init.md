---
description: Scaffold knowledge capture infrastructure for the current project
---

# /dex:init

Scaffold the `.claude/docs/` knowledge directory for the current project. Use this to set up a project for knowledge capture before you have anything to capture, or to check what already exists.

## Step 1: Discover Existing Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill:

1. Find the project root via `git rev-parse --show-toplevel`
2. Find CLAUDE.md (check root, then `.claude/CLAUDE.md`)
3. Check for `.claude/docs/` and its subdirectories (`learnings/`, `patterns/`, `decisions/`)
4. Count CLAUDE.md lines

## Step 2: Report Current State

Present what was found:

```
Knowledge Infrastructure

Project root:     /path/to/project
CLAUDE.md:        Found (root, 245 lines)

.claude/docs/:
  learnings/      [exists: 7 files] or [missing]
  patterns/       [exists: 3 files] or [missing]
  decisions/      [exists: 2 files] or [missing]
```

## Step 3: Scaffold If Needed

**If everything already exists:**

Report "Knowledge infrastructure is already set up" and show the summary. Done.

**If `.claude/docs/` or any subdirectories are missing:**

Use AskUserQuestion:

**Question:** "Create missing knowledge directories?"
**Options:**
- **Yes, create them** — creates all missing directories (`.claude/docs/learnings/`, `.claude/docs/patterns/`, `.claude/docs/decisions/`)
- **No, not now** — skip

**If user selects yes:**

Create the missing directories using `mkdir -p`. Create empty directories only — no README files, no templates, no boilerplate.

Report what was created:

```
Created:
  .claude/docs/learnings/
  .claude/docs/patterns/
  .claude/docs/decisions/

Ready for knowledge capture. Use /dex:learn or /dex:pattern to start.
```
