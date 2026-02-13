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
- **Yes, create `.claude/docs/`** — scaffolds `learnings/`, `patterns/`, `decisions/`
- **Not now** — abort capture

If "Not now", stop. If "Yes", create directories with `mkdir -p` and continue.

## Step 2: Extract Pattern from Conversation

Scan the recent conversation for the relevant pattern. If `$ARGUMENTS` contains a focus hint, use it to narrow extraction.

Following the **Knowledge Extraction from Conversation** guidance in the `knowledge-capture` skill:

1. Identify the reusable approach — what's the pattern an agent should follow?
2. Draft a **title** as a short pattern name
3. Draft the **Pattern** section — what it is and how to apply it
4. Draft **When to apply** and **When NOT to apply** sections
5. Identify a **Reference implementation** if one exists in the codebase
6. Identify 3-5 **tags** from the technical domain
7. Determine the filename: `YYYY-MM-DD-slug.md`

## Step 3: Confirm with User

Use AskUserQuestion:

**Question:** "Capture this pattern?"

Show the drafted title and pattern in the question description:
> **Title:** [drafted title]
> **Pattern:** [1-2 sentence pattern description]
> **File:** `.claude/docs/patterns/YYYY-MM-DD-slug.md`

**Options:**
- **Accept** — write the document immediately
- **Edit** — let user provide corrections via free text
- **Skip** — abort capture

If "Edit", apply the user's corrections and confirm again. If "Skip", stop.

## Step 4: Write the Document

Write the pattern to `.claude/docs/patterns/YYYY-MM-DD-slug.md` using the **Pattern Format** from the `knowledge-capture` skill.

Report success:
```
Written to .claude/docs/patterns/YYYY-MM-DD-slug.md
```

## Step 5: Suggest Promotion (Conditional)

Evaluate whether the pattern looks rule-worthy using the criteria from the `knowledge-capture` skill (corrects a common mistake, applies project-wide, codifies a convention).

**If rule-worthy**, use AskUserQuestion:

**Question:** "This looks like a project rule. Add a one-liner to CLAUDE.md?"
**Options:**
- **Yes, add to CLAUDE.md** — promote using the promotion flow from the `knowledge-capture` skill
- **No, just keep the doc** — done

**If NOT rule-worthy**, skip this step entirely. Do not ask.

## Step 6: Promote (If Selected)

Follow the **CLAUDE.md Promotion** flow from the `knowledge-capture` skill:

1. Count CLAUDE.md lines and check budget
2. Draft a one-liner + link
3. Auto-place in the most relevant section
4. Report success with new line count
