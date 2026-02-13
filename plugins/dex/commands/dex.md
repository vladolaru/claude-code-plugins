---
description: Capture knowledge from the current conversation — auto-classifies as learning, pattern, or decision
argument-hint: "[optional: focus hint]"
---

# /dex

Thin router that reads the current conversation, classifies the type of knowledge, and delegates to the right handler.

## Step 1: Classify

Read the recent conversation and determine what kind of knowledge is present:

- **Learning** — a discovery, fix, gotcha, or debugging insight was discussed
- **Pattern** — a reusable approach, convention, or anti-pattern was identified
- **Decision** — a choice between alternatives was made with trade-offs discussed

If `$ARGUMENTS` contains a focus hint (e.g., "cache invalidation"), use it to narrow the extraction focus.

## Step 2: Confirm Classification

Use AskUserQuestion:

**Question:** "What kind of knowledge to capture?"

Present your best guess as the first (recommended) option:

**Options:**
- **Learning (Recommended)** — something you just discovered or fixed *(only if classified as learning)*
- **Pattern (Recommended)** — a reusable approach or convention *(only if classified as pattern)*
- **Decision (Recommended)** — a choice with trade-offs worth remembering *(only if classified as decision)*

Always include all three options. Put "(Recommended)" on the auto-classified one.

## Step 3: Delegate

Based on the user's selection:

- **Learning** → Follow the full `/dex:learn` flow (from the learn command)
- **Pattern** → Follow the full `/dex:pattern` flow (from the pattern command)
- **Decision** → Follow the `/dex:learn` flow but use the **Decision Format** from the `knowledge-capture` skill instead of the Learning Format. Write to `.claude/docs/decisions/YYYY-MM-DD-slug.md`. Do NOT suggest promotion for decisions (they are reference material, not rules).
