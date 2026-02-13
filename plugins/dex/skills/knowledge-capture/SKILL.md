---
name: knowledge-capture
description: >
  Core logic for dex knowledge capture: project discovery, document formats,
  CLAUDE.md budget management, and promotion flow. Referenced by all dex commands.
---

# Knowledge Capture Skill

This skill provides the shared logic that all `/dex:*` commands reference. It is not invoked directly by users.

## Project Discovery

Every command invocation must discover the project's knowledge infrastructure by scanning the filesystem. No config files, no cached state.

### Discovery Steps

1. **Find the project root:** Use `git rev-parse --show-toplevel` to find the repository root
2. **Find CLAUDE.md:** Check these locations in order, use the first found:
   - `<root>/CLAUDE.md`
   - `<root>/.claude/CLAUDE.md`
3. **Find knowledge directory:** Check for `<root>/.claude/docs/`
4. **List existing subdirectories:** Check for `learnings/`, `patterns/`, `decisions/` within `.claude/docs/`
5. **Count CLAUDE.md lines:** `wc -l` on the found CLAUDE.md

### Discovery Output

Build a mental model of what exists:

```
project_root:     /path/to/project
claude_md:        /path/to/project/CLAUDE.md  (387 lines)
knowledge_dir:    /path/to/project/.claude/docs/
  learnings:      exists (7 files)
  patterns:       exists (3 files)
  decisions:      exists (2 files)
```

If `.claude/docs/` doesn't exist, commands should offer scaffolding via AskUserQuestion before proceeding (except `/dex:status` which just reports the absence).

### Scaffolding

When scaffolding is needed, create these directories:
- `.claude/docs/learnings/`
- `.claude/docs/patterns/`
- `.claude/docs/decisions/`

Create empty directories only. No README files, no templates, no boilerplate.

## Document Formats

All documents are **agent-first**: lead with the actionable directive so an AI agent reading only the first section gets enough to act. Context and examples follow for depth.

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

After capturing a learning or pattern, evaluate whether it looks rule-worthy. Suggest promotion when the captured knowledge:
- Contains a do/don't directive (corrects a common mistake)
- Addresses a recurring issue (mentioned multiple times in conversation)
- Is a project-wide constraint (applies broadly, not to one file)

Do NOT suggest promotion for:
- Informational learnings ("here's how X works internally")
- One-off debugging insights unlikely to recur
- Decisions (they're reference material, not rules)

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
| **550+** | Hard block — **"CLAUDE.md is over budget (X lines). Must extract a section before adding."** Show sections ranked by line count, offer to extract the largest |

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

When a command needs to extract knowledge from the current conversation:

1. **Scan recent messages** for the relevant exchange (fix, discovery, decision, pattern discussion)
2. **Identify the core insight** — what's the one thing an agent should know?
3. **Draft the title** as a short, directive statement (imperative or declarative)
4. **Draft the key section** (Rule for learnings, Pattern for patterns, Decision for decisions)
5. **Identify tags** from the technical domain (3-5 lowercase, hyphen-separated)
6. **Present via AskUserQuestion** for one-click confirmation

### Extraction Quality

The extracted document should be:
- **Self-contained** — an agent reading it in isolation understands the rule
- **Actionable** — tells the agent what to DO, not just what happened
- **Specific** — includes concrete examples, file paths, or code when relevant
- **Concise** — under 50 lines for learnings, under 80 for patterns/decisions
