---
description: Capture knowledge from the current conversation — auto-classifies as learning, pattern, or decision
argument-hint: "[optional: focus hint]"
---

# /dex

Classify the knowledge in the current conversation and delegate to the right capture flow. This command asks one question, then hands off — keep it fast.

## Step 1: Classify

Re-read the recent conversation and classify the knowledge into one of these categories:

| Category | Signal in conversation |
|---|---|
| **Learning** | A discovery, fix, gotcha, or debugging insight — something went wrong or was non-obvious |
| **Pattern** | A reusable approach, convention, or anti-pattern — something that should be repeated (or avoided) |
| **Decision** | A choice between alternatives with trade-offs discussed — "we chose X because Y" |

If `$ARGUMENTS` contains a focus hint (e.g., "cache invalidation"), narrow the extraction to that topic.

If the conversation contains multiple types, pick the most prominent one. The user can override via the confirmation step.

## Step 2: Confirm Classification

Use AskUserQuestion:

**Question:** "What kind of knowledge to capture?"

Present your best guess first with "(Recommended)":

**Options:**
- **Learning (Recommended)** — something you just discovered or fixed *(only if classified as learning)*
- **Pattern (Recommended)** — a reusable approach or convention *(only if classified as pattern)*
- **Decision (Recommended)** — a choice with trade-offs worth remembering *(only if classified as decision)*

Always show all three options. Mark only the auto-classified one as "(Recommended)".

## Step 3: Delegate

Based on the user's selection:

- **Learning** → Execute the full `/dex:learn` flow
- **Pattern** → Execute the full `/dex:pattern` flow
- **Decision** → Execute the `/dex:learn` flow but use the **Decision Format** from the `knowledge-capture` skill instead of the Learning Format. Write to `.claude/docs/decisions/YYYY-MM-DD-slug.md`. Skip the promotion step entirely — decisions are reference material, not rules.
