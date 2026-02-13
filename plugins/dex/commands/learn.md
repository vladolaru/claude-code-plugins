---
description: Capture a learning from the current conversation — discoveries, fixes, gotchas, debugging insights
argument-hint: "[optional: focus hint]"
---

# /dex:learn

Capture a learning from the current conversation. Self-contained — extracts from chat history, no arguments needed. Optional focus hint narrows what to extract.

## Step 1: Discover Project Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill.

If `.claude/docs/` does not exist, use AskUserQuestion:

**Question:** "No knowledge directory found. Create it?"
**Options:**
- **Yes, create `.claude/docs/`** — scaffolds `learnings/`, `patterns/`, `decisions/`
- **Not now** — abort capture

If "Not now", stop. If "Yes", create directories with `mkdir -p` and continue.

## Step 2: Extract Learning from Conversation

Scan the recent conversation for the relevant exchange. If `$ARGUMENTS` contains a focus hint, use it to narrow extraction.

Following the **Knowledge Extraction from Conversation** guidance in the `knowledge-capture` skill:

1. Identify the core insight — what's the one thing an agent should know?
2. Draft a **title** as a short directive statement
3. Draft the **Rule** section — the actionable directive
4. Draft brief **Context** and **Examples** sections
5. Identify 3-5 **tags** from the technical domain
6. Determine the filename: `YYYY-MM-DD-slug.md`

## Step 3: Confirm with User

Use AskUserQuestion:

**Question:** "Capture this learning?"

Show the drafted title and rule in the question description:
> **Title:** [drafted title]
> **Rule:** [1-2 sentence rule]
> **File:** `.claude/docs/learnings/YYYY-MM-DD-slug.md`

**Options:**
- **Accept** — write the document immediately
- **Edit** — let user provide corrections via free text
- **Skip** — abort capture

If "Edit", apply the user's corrections and confirm again. If "Skip", stop.

## Step 4: Write the Document

Write the learning to `.claude/docs/learnings/YYYY-MM-DD-slug.md` using the **Learning Format** from the `knowledge-capture` skill.

Report success:
```
Written to .claude/docs/learnings/YYYY-MM-DD-slug.md
```

## Step 5: Suggest Promotion (Conditional)

Evaluate whether the learning looks rule-worthy using the criteria from the `knowledge-capture` skill (contains do/don't directive, corrects a common mistake, applies project-wide).

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
