---
name: init
description: "Scaffold knowledge capture infrastructure for the current project"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/init.md -->

## Codex Host Adapter

This skill is generated from the canonical Claude Code command named above. To execute it in Codex:

1. Treat the text supplied after the skill mention as the invocation arguments. Substitute that exact text for `${CODEX_SKILL_ARGUMENTS}` before executing shell commands.
2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the plugin root is two directories above the directory containing this `SKILL.md`.
3. Assign both variables explicitly in any shell call that uses them. Codex does not export these instruction variables automatically.
4. Use Codex's available user-input and subagent tools when the workflow requests them.
5. Follow the canonical workflow below without skipping its gates or artifact checks.

## Canonical Workflow


# $dex:init

Set up the `.claude/docs/` knowledge directory for the current project. This is a setup-only command - it creates directories and reports state, nothing else.

## Step 1: Discover Existing Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill.

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
  research/       [exists: 1 file] or [missing]
```

## Step 3: Scaffold If Needed

**If everything already exists:** Report "Knowledge infrastructure is already set up." and show the summary. Continue to Step 4.

**If `.claude/docs/` or any subdirectories are missing:**

Use the host's user-input mechanism:

**Question:** "Create missing knowledge directories?"
**Options:**
- **Yes, create them** - creates all missing directories under `.claude/docs/`
- **No, not now** - skip

**If user selects yes:**

Create all missing directories using `mkdir -p`. Create empty directories only - no README files, no templates, no boilerplate.

Report what was created:

```
Created:
  .claude/docs/learnings/
  .claude/docs/patterns/
  .claude/docs/decisions/
  .claude/docs/research/

Ready for knowledge capture. Use $dex:learn, $dex:pattern, or $dex:research to start.
```

## Step 3.5: Offer Migration (If Mismatch Detected)

If discovery flagged a migration mismatch (instructions file resolved to AGENTS.md but knowledge exists in `.claude/docs/`, not `.ai/docs/`):

Use the host's user-input mechanism:

**Question:** "Knowledge is in `.claude/docs/` but project uses AGENTS.md. Migrate to `.ai/docs/`?"

Show in the description:
> This moves `.claude/docs/` → `.ai/docs/` for consistency with the AGENTS.md setup. All existing knowledge documents are preserved.

**Options:**
- **Yes, migrate** - creates `.ai/` if needed, moves `.claude/docs/` to `.ai/docs/` via `mv`
- **Keep as-is** - continues using `.claude/docs/`

If "Yes", run `mkdir -p <root>/.ai && mv <root>/.claude/docs <root>/.ai/docs`. Report what was moved. Update the active `knowledge_dir` to `.ai/docs/` for the rest of this command.

If no mismatch was detected, skip this step silently.

## Step 4: Suggest Capture Directive (Conditional)

If a CLAUDE.md file was found in Step 1, check whether it already contains a `$dex:grok` capture directive (search for `$dex:grok` in the file). If a directive is already present, skip this step silently.

If no directive exists, use the host's user-input mechanism:

**Question:** "Add a knowledge capture reminder to CLAUDE.md?"

Show in the description:
> This adds a one-liner that reminds future agents to suggest `$dex:grok` after significant debugging, decisions, or pattern discovery.
>
> **Directive:** `- After significant debugging sessions, architectural decisions, or discovering non-obvious behavior, suggest using $dex:grok to capture the knowledge.`

**Options:**
- **Yes, add it** - appends the directive to the most relevant section in CLAUDE.md
- **Skip** - no changes to CLAUDE.md

If "Yes", follow the **Auto-Placement** logic from the `knowledge-capture` skill to place the one-liner in the most relevant section.

Stop here. This command only scaffolds - it does not capture knowledge.
