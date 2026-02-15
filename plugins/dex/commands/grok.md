---
description: Capture knowledge from the current conversation — auto-classifies as learning, pattern, or decision
argument-hint: "[optional: focus hint]"
---

# /dex:grok

Deeply understand the current conversation, classify the knowledge, and delegate to the right capture flow. This command asks one question, then hands off — keep it fast.

## Step 1: Classify

If the conversation is primarily about agent behavior (wrong tools, inefficient discovery, missed shortcuts), route to `/dex:sharpen` instead. Grok captures domain knowledge; sharpen captures operational knowledge.

Scan the recent conversation for knowledge signals — surprises, mistakes, trade-off discussions, repeated friction, or investigation results — and classify into one of these categories:

| Category | Signal in conversation |
|---|---|
| **Learning** | A discovery, fix, gotcha, or debugging insight — something went wrong or was non-obvious |
| **Pattern** | A reusable approach, convention, or anti-pattern — something that should be repeated (or avoided) |
| **Decision** | A choice between alternatives with trade-offs discussed — "we chose X because Y" |
| **Research** | Extensive investigation, multiple approaches tried, trial-and-error exploration, empirical findings across environments |

If `$ARGUMENTS` contains a focus hint (e.g., "cache invalidation"), narrow the extraction to that topic.

If the conversation contains no knowledge worth capturing (routine operations, pure Q&A, nothing surprising or non-obvious), say so briefly and stop. Do not force-classify when there is nothing to classify.

If the conversation contains multiple knowledge types, pick the most prominent one. Disambiguation heuristics:
- **Learning vs. Pattern:** pick learning if the knowledge is about a specific situation, pattern if it describes a reusable approach.
- **Learning vs. Research:** pick learning if one key insight; pick research if multiple approaches were tried across environments and findings are empirical.
- **Pattern vs. Decision:** pick pattern if the knowledge prescribes HOW to do something; pick decision if it explains WHY one option was chosen over alternatives.

The user can override via the confirmation step.

## Step 2: Confirm Classification

Use AskUserQuestion:

**Question:** "What kind of knowledge to capture?"

Present your best guess first with "(Recommended)":

**Options:**
- **Learning (Recommended)** — something you just discovered or fixed *(only if classified as learning)*
- **Pattern (Recommended)** — a reusable approach or convention *(only if classified as pattern)*
- **Decision (Recommended)** — a choice with trade-offs worth remembering *(only if classified as decision)*
- **Research (Recommended)** — extensive investigation with empirical findings *(only if classified as research)*

Always show all four options. Mark only the auto-classified one as "(Recommended)".

## Step 3: Delegate

Based on the user's selection:

- **Learning** → Execute the full `/dex:learn` flow
- **Pattern** → Execute the full `/dex:pattern` flow
- **Decision** → Execute the `/dex:learn` flow with these overrides: use the **Decision Format** from the `knowledge-capture` skill, write to `.claude/docs/decisions/`, and skip the promotion step (decisions are reference material, not rules)
- **Research** → Execute the full `/dex:research` flow
