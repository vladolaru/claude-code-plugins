---
description: Capture a reusable pattern from the current conversation — approaches, conventions, anti-patterns
argument-hint: "[optional: focus hint]"
---

# /dex:pattern

Capture a reusable pattern from the current conversation. Self-contained — extracts from chat history. Optional focus hint narrows what to extract.

## Step 1: Discover Project Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill.

If `.claude/docs/` does not exist, use AskUserQuestion:

**Question:** "No knowledge directory found. Create it?"
**Options:**
- **Yes, create `.claude/docs/`** — scaffolds `learnings/`, `patterns/`, `decisions/`, `research/`
- **Not now** — abort capture

If "Not now", stop here. If "Yes", create directories with `mkdir -p` and continue.

## Step 2: Extract Pattern from Conversation

Run the `<pre_extraction_analysis>` from the `knowledge-capture` skill on the relevant conversation exchange. If `$ARGUMENTS` contains a focus hint, narrow extraction to that topic.

If the conversation contains nothing extractable as a pattern (no reusable approach, convention, or anti-pattern), say so briefly and stop. Do not fabricate knowledge.

Following the **Knowledge Extraction from Conversation** guidance in the `knowledge-capture` skill:

1. Identify the reusable approach — what's the pattern an agent should follow?
2. Draft a **title** as a short pattern name (e.g., "Use factory pattern for test fixtures")
3. Draft the **Pattern** section — the reusable approach in concrete terms: what to do, when, and how, in 2-4 sentences
4. Draft **When to apply** — observable signals that indicate this pattern is needed (what you'd see in code, errors, or task requirements)
5. Draft **When NOT to apply** — exceptions where this pattern causes harm (simpler alternatives exist, wrong scale, or conflicting constraints)
6. Identify a **Reference implementation** if one exists in the codebase — direct `file:line` reference (e.g., `src/gateway.php:45-60`)
7. Identify 3-5 **tags** from the technical domain
8. Determine the filename: `YYYY-MM-DD-slug.md`

Verify the draft passes the `<extraction_quality_checklist>` from the `knowledge-capture` skill before presenting to the user.

Include "When NOT to apply" — a pattern without boundaries will be misapplied.

## Step 3: Confirm with User

Use AskUserQuestion:

**Question:** "Capture this pattern?"

Show exactly these fields in the question description:
> **Title:** [drafted title]
> **Pattern:** [1-2 sentence pattern description]
> **Applies when:** [1-sentence trigger summary]
> **File:** `.claude/docs/patterns/YYYY-MM-DD-slug.md`

**Options:**
- **Accept** — write the document immediately
- **Edit** — let user provide corrections via free text
- **Skip** — abort capture

If "Edit", apply the user's corrections and confirm again. If "Skip", stop here.

## Step 4: Write the Document

Write the pattern to `.claude/docs/patterns/YYYY-MM-DD-slug.md` using the **Pattern Format** from the `knowledge-capture` skill.

Report:
```
Captured: .claude/docs/patterns/YYYY-MM-DD-slug.md
```

## Step 5: Suggest Promotion (Conditional)

Evaluate whether the pattern looks rule-worthy (per the **When to Suggest Promotion** criteria in the `knowledge-capture` skill): does it correct a common mistake, apply project-wide, or codify a convention?

**If rule-worthy**, use AskUserQuestion:

**Question:** "This looks like a project convention. Add a one-liner to CLAUDE.md?"
**Options:**
- **Yes, add to CLAUDE.md** — promote using the promotion flow from the `knowledge-capture` skill
- **No, just keep the doc** — done

**If NOT rule-worthy**, skip this step silently. Proceed to completion.

## Step 6: Promote (If Selected)

Follow the **CLAUDE.md Promotion** flow from the `knowledge-capture` skill:

1. Count CLAUDE.md lines and check budget
2. Draft a one-liner + link
3. Auto-place in the most relevant section
4. Report success with new line count

After promotion (or skipping it), stop. This command captures one pattern per invocation.
