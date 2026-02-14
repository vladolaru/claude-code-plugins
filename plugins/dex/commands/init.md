---
description: Scaffold knowledge capture infrastructure for the current project
---

# /dex:init

Set up the `.claude/docs/` knowledge directory for the current project. This is a setup-only command — it creates directories and reports state, nothing else.

## Step 1: Discover Existing Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill:

1. Find the project root via `git rev-parse --show-toplevel`
2. Find CLAUDE.md (check root, then `.claude/CLAUDE.md`)
3. Check for `.claude/docs/` and its subdirectories (`learnings/`, `patterns/`, `decisions/`)
4. Count CLAUDE.md lines

## Step 2: Report Current State

Present what was found using this exact format:

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

**If everything already exists:** Report "Knowledge infrastructure is already set up." and show the summary. Stop here.

**If `.claude/docs/` or any subdirectories are missing:**

Use AskUserQuestion:

**Question:** "Create missing knowledge directories?"
**Options:**
- **Yes, create them** — creates all missing directories under `.claude/docs/`
- **No, not now** — skip

**If user selects yes:**

Create all missing directories using `mkdir -p`. Create empty directories only — no README files, no templates, no boilerplate.

Report what was created:

```
Created:
  .claude/docs/learnings/
  .claude/docs/patterns/
  .claude/docs/decisions/

Ready for knowledge capture. Use /dex:learn or /dex:pattern to start.
```

## Step 4: Suggest Capture Directive (Conditional)

If a CLAUDE.md file was found in Step 1, check whether it already contains a `/dex` capture directive (search for `/dex` in the file). If a directive is already present, skip this step silently.

If no directive exists, use AskUserQuestion:

**Question:** "Add a knowledge capture reminder to CLAUDE.md?"

Show in the description:
> This adds a one-liner that reminds future agents to suggest `/dex` after significant debugging, decisions, or pattern discovery.
>
> **Directive:** `- After significant debugging sessions, architectural decisions, or discovering non-obvious behavior, suggest using /dex to capture the knowledge.`

**Options:**
- **Yes, add it** — appends the directive to the most relevant section in CLAUDE.md
- **Skip** — no changes to CLAUDE.md

If "Yes", follow the **Auto-Placement** logic from the `knowledge-capture` skill to place the one-liner in the most relevant section.

Stop here. This command only scaffolds — it does not capture knowledge.
