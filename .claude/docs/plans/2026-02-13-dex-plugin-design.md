# dex — Knowledge Capture Plugin Design

**Date:** 2026-02-13
**Status:** Draft
**Plugin:** `dex` (standalone, new plugin in this marketplace)

## Purpose

Make compounding knowledge a one-click habit. After any engineering work — fixing a bug, discovering a gotcha, establishing a pattern, making an architectural choice — the developer captures the insight in under 10 seconds so the project gets smarter for every future AI agent session.

## Core Principles

1. **Conversation is the context** — commands extract from chat history, no arguments needed
2. **One-click confirmation** — AskUserQuestion for all interactions, not text input
3. **Agent-first documents** — lead with the rule/directive, context and examples follow
4. **CLAUDE.md stays lean** — 500-line budget with promoted rules as one-liners linking to detail docs
5. **Zero config** — scan the project fresh each invocation, scaffold on first run if needed

## Commands

### `/dex:init` — Scaffold Knowledge Infrastructure

Scans the project, reports what exists, and offers to create missing `.claude/docs/` directories. Convenience command for proactive setup — same scaffolding is offered inline by other commands on first run.

### `/dex` — Thin Router

Reads recent conversation, classifies the knowledge type, and asks:

**AskUserQuestion:** "What kind of knowledge to capture?"
- **Learning** — something you just discovered/fixed → delegates to `/dex:learn`
- **Pattern** — a reusable approach or convention → delegates to `/dex:pattern`
- **Decision** — a choice with trade-offs worth remembering → uses decision format from shared skill

One click → routes to the right handler.

### `/dex:learn` — Capture a Learning

**When:** Post-fix, post-debug, post-discovery. Self-contained — extracts from conversation.

**Flow:**

1. Scan project for knowledge infrastructure (CLAUDE.md location, `.claude/docs/`)
2. If first run and no `.claude/docs/`, offer to scaffold via AskUserQuestion
3. Extract learning from conversation: title, rule, context, examples
4. **AskUserQuestion 1:** "Capture this learning?" — Accept / Edit / Skip
5. Write to `.claude/docs/learnings/YYYY-MM-DD-slug.md`
6. If learning looks rule-worthy (contains do/don't directive, corrects a mistake):
   **AskUserQuestion 2:** "This looks like a project rule. Add one-liner to CLAUDE.md?" — Yes / No

### `/dex:pattern` — Capture a Pattern

**When:** Post-review, recurring approach noticed, convention to codify. Self-contained or with optional focus hint.

**Flow:** Same as learn, but uses pattern document format. Writes to `.claude/docs/patterns/YYYY-MM-DD-slug.md`.

### `/dex:init` — Scaffold Knowledge Infrastructure

**When:** Setting up a new project for knowledge capture, or onboarding a teammate.

**Flow:**

1. Scan project for existing knowledge infrastructure
2. Report what exists and what's missing
3. **AskUserQuestion:** "Create missing directories?" — listing what would be created
   - `.claude/docs/learnings/`
   - `.claude/docs/patterns/`
   - `.claude/docs/decisions/`
4. Create selected directories
5. Confirm what was created

If everything already exists, reports "Knowledge infrastructure is already set up" with a summary.

This is a convenience shortcut — the same scaffolding is offered inline when running `/dex:learn` or `/dex:pattern` on a project with no `.claude/docs/`. `/dex:init` lets you set up proactively without needing something to capture first.

### `/dex:status` — Knowledge Health Report

**When:** Anytime. Fully self-contained, no input.

**Output:**
```
Project Knowledge Health

CLAUDE.md:        387/500 lines (root)
                  + 3 nested CLAUDE.md files

Learnings:        12 docs  (.claude/docs/learnings/)
  Latest:         2026-02-13 — WP-CLI REST auth
  Oldest:         2026-01-15 — Docker volume permissions

Patterns:         4 docs   (.claude/docs/patterns/)
  Latest:         2026-02-11 — Transient cache invalidation

Decisions:        2 docs   (.claude/docs/decisions/)
  Latest:         2026-02-09 — Flat array for PM promotions
```

## Document Formats

All formats are agent-first: lead with the actionable directive, follow with context.

### Learning Format

```markdown
# Short directive title

**Date:** YYYY-MM-DD
**Tags:** tag1, tag2, tag3

## Rule

The actionable directive — what to do or not do. An agent reading
only this section should know enough to apply the knowledge.

## Context

Why this matters. What went wrong or what was non-obvious.

## Examples

Code examples showing correct and incorrect approaches.
```

### Pattern Format

```markdown
# Short pattern name

**Date:** YYYY-MM-DD
**Tags:** tag1, tag2, tag3

## Pattern

The reusable approach — when and how to apply it.

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

## Knowledge Topology

### Discovery

Every command invocation scans the project fresh:
1. Find CLAUDE.md (check root, then `.claude/CLAUDE.md`)
2. Find `.claude/docs/` and list subdirectories
3. Build in-memory map of what exists

No config files, no cached state.

### First-Run Scaffolding

When no `.claude/docs/` exists, AskUserQuestion:

**"No knowledge directory found. Create it?"**
- **Yes, create `.claude/docs/`** — scaffolds `learnings/`, `patterns/`, `decisions/`
- **Not now** — skips capture

Creates empty directories only. No README files, no templates. The first captured doc is the template.

### Storage Paths

```
.claude/docs/
├── learnings/       YYYY-MM-DD-slug.md
├── patterns/        YYYY-MM-DD-slug.md
└── decisions/       YYYY-MM-DD-slug.md
```

## CLAUDE.md Management

### Promotion

Promotion is folded into the capture flow — no standalone command. After a learning or pattern is captured, if it looks rule-worthy, the command offers to promote via AskUserQuestion.

**Promoted rule format in CLAUDE.md:**
```markdown
- Always pass `--user=1` for WP-CLI REST calls with auth. See [details](.claude/docs/learnings/2026-02-13-wp-cli-rest-auth.md).
```

One-liner + link. The detail doc has the full context.

### Auto-Placement

When promoting, the command:
1. Scans CLAUDE.md for existing `##` section headings
2. Matches the rule's tags/content to the most relevant section
3. Places the one-liner under that section

### Budget Enforcement

| CLAUDE.md lines | Behavior |
|---|---|
| **< 500** | Promote freely |
| **500–550** | Warn via AskUserQuestion: "Add anyway" / "Extract a section first" |
| **550+** | Hard block — must extract a section to `.claude/docs/` before adding |

Extraction flow: show CLAUDE.md sections ranked by line count, offer to move the largest one to a linked doc.

## Plugin Structure

```
plugins/dex/
├── CHANGELOG.md
├── commands/
│   ├── dex.md              # Thin router — classify and delegate
│   ├── init.md             # Scaffold knowledge infrastructure
│   ├── learn.md            # Capture a learning
│   ├── pattern.md          # Capture a pattern
│   └── status.md           # Knowledge health report
└── skills/
    └── knowledge-capture/
        └── SKILL.md         # Core logic: discovery, formats,
                             # extraction, CLAUDE.md budget mgmt
```

- **Commands** are user-facing entry points (thin orchestration)
- **Shared skill** holds common logic all commands reference
- **No agents** — commands run in main conversation (need conversation history access)
- **No scripts, no config files, no schema validation**

## Integration (Future)

Not in v1, but planned:
- **pirategoat-tools review pipeline** — after `/full-code-review`, suggest capturing repeated findings as patterns
- **Cross-project knowledge** — learnings from one project applicable to others
- **Batch promotion** — `/dex:status` surfaces unpromoted rule-worthy docs

## Research Sources

- [Compound Engineering: The Definitive Guide (Every)](https://every.to/source-code/compound-engineering-the-definitive-guide)
- [Compound Engineering Guide (Every)](https://every.to/guides/compound-engineering)
- [Every's Official Plugin](https://github.com/EveryInc/compound-engineering-plugin)
- [Learning from Every's Compound Engineering (Will Larson)](https://lethain.com/everyinc-compound-engineering/)
- [Compound Engineering - Vinci Rufus](https://www.vincirufus.com/posts/compound-engineering/)
- [Agentic Patterns: Compounding Engineering](https://agentic-patterns.com/patterns/compounding-engineering-pattern/)
