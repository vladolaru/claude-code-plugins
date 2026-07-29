---
name: learn
description: "Capture a learning from the current conversation - discoveries, fixes, gotchas, debugging insights"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/learn.md -->

## Codex Host Adapter

This skill is generated from the canonical Claude Code command named above. To execute it in Codex:

1. Treat the text supplied after the skill mention as the invocation arguments. Substitute that exact text for `${CODEX_SKILL_ARGUMENTS}` before executing shell commands.
2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the plugin root is two directories above the directory containing this `SKILL.md`.
3. Assign both variables explicitly in any shell call that uses them. Codex does not export these instruction variables automatically.
4. Use Codex's available user-input and subagent tools when the workflow requests them.
5. Follow the canonical workflow below without skipping its gates or artifact checks.

## Canonical Workflow


# $dex:learn

Capture a learning from the current conversation. Self-contained - extracts from chat history, no arguments needed. Optional focus hint narrows what to extract.

## Step 1: Discover Project Infrastructure

Follow the **Project Discovery** steps from the `knowledge-capture` skill.

If `.claude/docs/` does not exist, use the host's user-input mechanism:

**Question:** "No knowledge directory found. Create it?"
**Options:**
- **Yes, create `.claude/docs/`** - scaffolds `learnings/`, `patterns/`, `decisions/`, `research/`
- **Not now** - abort capture

If "Not now", stop here. If "Yes", create directories with `mkdir -p` and continue.

## Step 2: Extract Learning from Conversation

Run the `<pre_extraction_analysis>` from the `knowledge-capture` skill on the relevant conversation exchange. If `${CODEX_SKILL_ARGUMENTS}` contains a focus hint, narrow extraction to that topic.

If the conversation contains nothing extractable as a learning (no discovery, fix, or gotcha), say so briefly and stop. Do not fabricate knowledge.

Following the **Knowledge Extraction from Conversation** guidance in the `knowledge-capture` skill:

1. Identify the core insight - what's the one thing an agent should know next time?
2. Draft a **title** as a short directive statement (e.g., "Always pass --user=1 for WP-CLI REST calls")
3. Draft the **Rule** section - a specific, actionable directive: what to do and why, in 1-3 sentences
4. Draft brief **Context** (why this matters, root cause) and **Examples** (correct vs. incorrect approaches)
5. Identify 3-5 **tags** from the technical domain
6. Determine the filename: `YYYY-MM-DD-slug.md`

Verify the draft passes the `<extraction_quality_checklist>` from the `knowledge-capture` skill before presenting to the user.

Focus on the behavioral change: what should an agent do differently next time?

## Step 3: Confirm with User

Use the host's user-input mechanism:

**Question:** "Capture this learning?"

Show exactly these fields in the question description:
> **Title:** [drafted title]
> **Rule:** [1-2 sentence rule]
> **File:** `.claude/docs/learnings/YYYY-MM-DD-slug.md`

**Options:**
- **Accept** - write the document immediately
- **Edit** - let user provide corrections via free text
- **Skip** - abort capture

If "Edit", apply the user's corrections and confirm again. If "Skip", stop here.

## Step 4: Write the Document

Write the learning to `.claude/docs/learnings/YYYY-MM-DD-slug.md` using the **Learning Format** from the `knowledge-capture` skill.

Report:
```
Captured: .claude/docs/learnings/YYYY-MM-DD-slug.md
```

## Step 5: Suggest Promotion (Conditional)

Evaluate whether the learning looks rule-worthy (per the **When to Suggest Promotion** criteria in the `knowledge-capture` skill): does it contain a do/don't directive, correct a common mistake, or apply project-wide?

**If rule-worthy**, use the host's user-input mechanism:

**Question:** "This looks like a project rule. Add a one-liner to CLAUDE.md?"
**Options:**
- **Yes, add to CLAUDE.md** - promote using the promotion flow from the `knowledge-capture` skill
- **No, just keep the doc** - done

**If NOT rule-worthy**, skip this step silently. Proceed to completion.

## Step 6: Promote (If Selected)

Follow the **CLAUDE.md Promotion** flow from the `knowledge-capture` skill:

1. Count CLAUDE.md lines and check budget
2. Draft a one-liner + link
3. Auto-place in the most relevant section
4. Report success with new line count

After promotion (or skipping it), stop. This command captures one learning per invocation.
