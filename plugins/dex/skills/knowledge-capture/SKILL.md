---
name: knowledge-capture
description: >
  Core logic for dex knowledge capture: project discovery, document formats,
  CLAUDE.md budget management, and promotion flow. Referenced by all dex commands.
---

# Knowledge Capture Skill

Shared logic for all `/dex:*` commands. Follow these procedures exactly — do what the calling command asks, nothing more.

## Project Discovery

Discover the project's knowledge infrastructure fresh on every invocation. Scan the filesystem directly — no config files, no cached state.

### Discovery Steps

1. **Find the project root:** Run `git rev-parse --show-toplevel`
2. **Find CLAUDE.md:** Check in order, use the first found:
   - `<root>/CLAUDE.md`
   - `<root>/.claude/CLAUDE.md`
3. **Find knowledge directory:** Check for `<root>/.claude/docs/`
4. **List existing subdirectories:** Check for `learnings/`, `patterns/`, `decisions/` within `.claude/docs/`
5. **Count CLAUDE.md lines:** Run `wc -l` on the found CLAUDE.md

If a CLAUDE.md or `.claude/docs/` does not exist, proceed with what you have. Missing infrastructure is a normal state — handle it per the calling command's instructions.

### Discovery Output

Build this mental model before proceeding:

```
project_root:     /path/to/project
claude_md:        /path/to/project/CLAUDE.md  (387 lines)
knowledge_dir:    /path/to/project/.claude/docs/
  learnings:      exists (7 files)
  patterns:       exists (3 files)
  decisions:      exists (2 files)
```

If `.claude/docs/` doesn't exist, commands should offer scaffolding via AskUserQuestion before proceeding (except `/dex:status` which reports the absence).

### Scaffolding

When scaffolding is needed, create these directories:
- `.claude/docs/learnings/`
- `.claude/docs/patterns/`
- `.claude/docs/decisions/`

Create empty directories only. No README files, no templates, no boilerplate.

## Document Formats

All documents are **agent-first**: the first section contains the actionable directive — an AI agent reading only the Rule/Pattern/Decision section gets enough to act. Context and examples follow for depth.

### Learning Format

Use for: discoveries, fixes, gotchas, debugging insights, non-obvious behaviors.

```markdown
# Short directive title

**Date:** YYYY-MM-DD
**Tags:** tag1, tag2, tag3

## Rule

The actionable directive — what to do or not do. An agent reading
only this section should know enough to apply the knowledge.

## Context

Why this matters. What went wrong or what was non-obvious.
Technical explanation of the root cause.

## Examples

Code examples showing correct and incorrect approaches.
Use CORRECT / WRONG labels for clarity.
```

<example type="CORRECT">
# Always pass --user=1 for WP-CLI REST calls with auth

**Date:** 2026-02-13
**Tags:** wp-cli, rest-api, authentication

## Rule

Always pass `--user=1` when making WP-CLI REST API calls that have
permission callbacks. Without it, the call runs as unauthenticated.
</example>

<example type="INCORRECT">
# WP-CLI REST API Issue

Today I discovered that WP-CLI REST API calls need authentication.
This was really confusing and took a while to debug. The error was a 403...
</example>

The incorrect example buries the actionable rule in narrative. Agent-first means Rule section leads.

### Pattern Format

Use for: reusable approaches, conventions, anti-patterns, recurring solutions.

```markdown
# Short pattern name

**Date:** YYYY-MM-DD
**Tags:** tag1, tag2, tag3

## Pattern

The reusable approach — what it is and how to apply it.

## When to apply

- Condition 1
- Condition 2

## When NOT to apply

- Exception 1
- Exception 2

## Reference implementation

File path and line range, or inline code example.
```

### Decision Format

Use for: architectural choices, trade-off decisions, technology selections.

```markdown
# Short decision statement

**Date:** YYYY-MM-DD
**Tags:** tag1, tag2, tag3

## Decision

What was chosen and the one-line rationale.

## Alternatives considered

- **Option A**: Description — why rejected
- **Option B** (chosen): Description — why chosen
- **Option C**: Description — why rejected

## Why this choice

Detailed reasoning, trade-offs, and constraints that led to this choice.
```

### Filename Convention

All files follow: `YYYY-MM-DD-slug.md`

Slug rules:
- Lowercase
- Replace spaces with hyphens
- Remove special characters except hyphens
- Truncate to keep full path under 100 characters

## CLAUDE.md Promotion

### When to Suggest Promotion

After capturing a learning or pattern, evaluate whether it looks rule-worthy. Suggest promotion only when the knowledge meets **at least one** of these criteria:
- Contains a do/don't directive that corrects a common mistake
- Addresses a recurring issue mentioned multiple times in conversation
- Is a project-wide constraint that applies broadly, not to one file

Skip promotion silently (without asking) for:
- Informational learnings ("here's how X works internally")
- One-off debugging insights unlikely to recur
- Decisions (they are reference material, not rules)

### Promoted Rule Format

One-liner + link to the source document:

```markdown
- Always pass `--user=1` for WP-CLI REST calls with auth. See [details](.claude/docs/learnings/2026-02-13-wp-cli-rest-auth.md).
```

### Auto-Placement

When promoting a rule to CLAUDE.md:

1. Read CLAUDE.md and identify all `##` section headings
2. Analyze the rule's tags and content to find the most relevant section
3. Append the one-liner at the end of that section (before the next `##` heading)
4. If no section is a clear match, append under the last section

### Budget Enforcement

Count lines in CLAUDE.md before promoting:

| Line count | Behavior |
|---|---|
| **< 500** | Promote freely — add the one-liner, confirm success |
| **500–550** | Warn via AskUserQuestion: **"CLAUDE.md is at X/500 lines."** Options: "Add anyway" / "Extract a section first" |
| **550+** | **STOP. Hard block.** Tell the user: "CLAUDE.md is over budget (X lines). Extract a section before adding new rules." Show sections ranked by line count, offer to extract the largest. Proceed with promotion only after extraction brings the count below 550 |

### Extraction Flow

When extracting a section from CLAUDE.md:

1. List all `##` sections with their line counts
2. AskUserQuestion: "Which section to extract?" — show sections ranked by size
3. Move the section content to `.claude/docs/` as a standalone doc
4. Replace the section in CLAUDE.md with a 1-2 line summary + link:
   ```markdown
   ## Section Name
   See [full details](.claude/docs/section-name.md).
   ```
5. Report the new line count

## Knowledge Extraction from Conversation

### How to Extract

Before drafting, re-read the relevant conversation exchange to identify the core insight. Then:

1. **Identify the core insight** — what's the one thing an agent should know?
2. **Draft the title** as a short, directive statement (imperative or declarative)
3. **Draft the key section** (Rule for learnings, Pattern for patterns, Decision for decisions)
4. **Identify tags** from the technical domain (3-5 lowercase, hyphen-separated)
5. **Present via AskUserQuestion** for one-click confirmation

Focus on what an agent needs to act differently next time, not on narrating what happened during debugging.

### Extraction Quality

**IMPORTANT:** Every extracted document must pass all four of these checks:
1. **Self-contained** — an agent reading it in isolation understands the rule
2. **Actionable** — tells the agent what to DO, not just what happened
3. **Specific** — includes concrete examples, file paths, or code when relevant
4. **Concise** — under 50 lines for learnings, under 80 for patterns/decisions
