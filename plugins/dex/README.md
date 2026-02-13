# dex — Knowledge Capture Plugin

Make compounding knowledge a one-click habit.

## The Problem

Most codebases get harder to work with over time. Each feature adds complexity, each debugging session reveals gotchas that live in someone's head, and each architectural decision's rationale fades from memory. AI agents start every session from scratch, rediscovering the same pitfalls.

## The Idea: Compound Engineering

[Compound engineering](https://every.to/guides/compound-engineering) inverts this — each unit of work should make the next one *easier*, not harder. The concept, developed by [Every, Inc.](https://every.to/source-code/compound-engineering-the-definitive-guide), follows a four-phase loop: **Plan → Work → Review → Compound**.

The "Compound" step is the novel piece. As [Will Larson observed](https://lethain.com/everyinc-compound-engineering/), the other three phases are well-known practices — it's the systematic capture of knowledge that creates the compounding effect.

**dex** focuses entirely on that Compound step. It makes knowledge capture so fast and frictionless that you'll actually do it.

## How It Works

After any engineering work — fixing a bug, discovering a gotcha, establishing a convention, making an architectural choice — fire a command. dex reads the conversation, extracts the insight, and asks for one-click confirmation. That's it.

Knowledge is stored as agent-first documents in `.claude/docs/` — structured so AI agents get the actionable rule in the first three lines without reading the whole file. Optionally, critical rules get promoted to a one-liner in CLAUDE.md with a link back to the full doc.

## Commands

| Command | Purpose |
|---|---|
| `/dex` | Auto-classifies the knowledge (learning, pattern, or decision) and routes to the right handler |
| `/dex:learn` | Captures a learning — discovery, fix, gotcha, debugging insight |
| `/dex:pattern` | Captures a reusable pattern — approach, convention, anti-pattern |
| `/dex:init` | Scaffolds `.claude/docs/` knowledge infrastructure for a new project |
| `/dex:status` | Shows knowledge health report — CLAUDE.md budget, doc counts, latest entries |

## What Gets Captured

### Learnings

Discoveries from debugging, fixes, and non-obvious behaviors. Structured as: **Rule → Context → Examples**.

```markdown
# WP-CLI REST API calls require explicit user context

## Rule
Always pass `--user=1` when making WP-CLI REST API calls that have
permission callbacks. Without it, the call runs as unauthenticated.

## Context
WP-CLI defaults to no authenticated user. `current_user_can()` checks
in permission callbacks fail silently, returning a generic 403.

## Examples
# CORRECT
pnpm wp --user=1 eval '...'

# WRONG - will 403 silently
pnpm wp eval '...'
```

### Patterns

Reusable approaches and conventions. Structured as: **Pattern → When to apply → When NOT to apply → Reference implementation**.

### Decisions

Architectural choices with trade-offs. Structured as: **Decision → Alternatives considered → Why this choice**.

## CLAUDE.md Management

CLAUDE.md should be a lean routing table — concise rules with links to depth, not a knowledge dump. dex enforces this:

- **Under 500 lines**: promotes freely
- **500–550 lines**: warns and lets you choose to add or extract first
- **550+ lines**: blocks until you extract a section into a linked doc

Promoted rules are one-liners with links:

```markdown
- Always pass `--user=1` for WP-CLI REST calls with auth. See [details](.claude/docs/learnings/2026-02-13-wp-cli-rest-auth.md).
```

## Installation

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install dex@vladolaru-claude-code-plugins
```

## Further Reading

- [Compound Engineering: The Definitive Guide](https://every.to/source-code/compound-engineering-the-definitive-guide) — Every's comprehensive methodology
- [Compound Engineering Guide](https://every.to/guides/compound-engineering) — Adoption ladder and practical workflow
- [How Every Codes With Agents](https://every.to/chain-of-thought/compound-engineering-how-every-codes-with-agents) — The original introduction
- [Learning from Every's Compound Engineering](https://lethain.com/everyinc-compound-engineering/) — Will Larson's analysis and critique
- [Compound Engineering - Vinci Rufus](https://www.vincirufus.com/posts/compound-engineering/) — Productivity equation and feedback loop perspective

## License

MIT
