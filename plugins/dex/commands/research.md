---
description: Capture research findings from the current conversation — investigations, debugging sessions, trial-and-error explorations
argument-hint: "[optional: focus hint]"
---

# /dex:research

Capture research findings from the current conversation. Self-contained — extracts from chat history, no arguments needed. Optional focus hint narrows what to extract.

## Step 1: Discover Project Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill.

If `.claude/docs/` does not exist, use AskUserQuestion:

**Question:** "No knowledge directory found. Create it?"
**Options:**
- **Yes, create `.claude/docs/`** — scaffolds `learnings/`, `patterns/`, `decisions/`, `research/`
- **Not now** — abort capture

If "Not now", stop here. If "Yes", create directories with `mkdir -p` and continue.

## Step 2: Extract Research from Conversation

Run the `<pre_extraction_analysis>` from the `knowledge-capture` skill on the relevant conversation exchange. If `$ARGUMENTS` contains a focus hint, narrow extraction to that topic.

If the conversation contains nothing extractable as research (no investigation, no empirical findings), say so briefly and stop. Do not fabricate knowledge.

Following the **Knowledge Extraction from Conversation** guidance in the `knowledge-capture` skill:

1. Identify the **topic** — what was being investigated or debugged?
2. Draft a **title** as a short descriptive statement (e.g., "PHP 8.3 readonly property behavior with WooCommerce hooks")
3. Draft a **Summary** — 2-3 sentence overview of key findings
4. Identify the **Environment** — specific versions and configs (e.g., "PHP 8.3.4, WooCommerce 9.6.0") that determine whether findings still apply
5. Draft **What Works** — proven approaches with evidence (commands, configs, or code that succeeded)
6. Draft **What Doesn't Work** — failed approaches and WHY they failed
7. Draft **Key Findings** — detailed empirical observations, numbered for reference
8. Draft **Reproduction Steps** — how to verify or reproduce the findings
9. Draft **Open Questions** — unresolved issues or areas needing further investigation
10. Identify 3-5 **tags** from the technical domain
11. Determine the filename: `YYYY-MM-DD-slug.md`

Omit any section that has no content — empty sections waste reader attention.

Verify the draft passes the `<extraction_quality_checklist>` from the `knowledge-capture` skill before presenting to the user.

Focus on structured empirical findings: what was tried, what the evidence showed, and what remains unknown.

## Step 3: Confirm with User

Use AskUserQuestion:

**Question:** "Capture this research?"

Show exactly these fields in the question description:
> **Title:** [drafted title]
> **Summary:** [1-2 sentence summary]
> **Environment:** [key versions/configs]
> **File:** `.claude/docs/research/YYYY-MM-DD-slug.md`

**Options:**
- **Accept** — write the document immediately
- **Edit** — let user provide corrections via free text
- **Skip** — abort capture

If "Edit", apply the user's corrections and confirm again. If "Skip", stop here.

## Step 4: Write the Document

Write the research to `.claude/docs/research/YYYY-MM-DD-slug.md` using the **Research Format** from the `knowledge-capture` skill. Set `Status: current`.

Report:
```
Captured: .claude/docs/research/YYYY-MM-DD-slug.md
```

After writing, stop. Research documents are reference material — no CLAUDE.md promotion step. This command captures one research document per invocation.
