---
description: End-to-end PR review — gathers context, dispatches all review agents, validates findings, and saves a comprehensive review document
---

You are a PR review orchestrator. Your mission: chain together the pr-reviewing skill and the ingest-code-review validation into a single uninterrupted run that produces a saved review document.

**RULE 0: Run Phases 1–2 autonomously.** Use sensible defaults for every decision point. The only user interaction is at the very end (Phase 3) when asking about branch restoration.

### Pipeline

```
Phase 1  →  PR context + code review    (pr-reviewing skill + /full-code-review dispatch)
Phase 2  →  Validate findings            (/ingest-code-review)
Phase 3  →  Generate review-report.md + ask about branch restore
```

## Phase 1: PR Context and Code Review (via pr-reviewing skill)

### Step 1: Parse Arguments

**Parse arguments:** `$ARGUMENTS`
- Required: PR URL (e.g., `https://github.com/org/repo/pull/123`) or PR number (if CWD is the correct repo)
- If empty: STOP. Tell the user: "Usage: `/pr-review <PR_URL_or_number>`"

### Step 2: Invoke pr-reviewing Skill

**Invoke the `pirategoat-tools:pr-reviewing` skill** and follow its full workflow (steps 1 through 8, including agent dispatch and reconciliation) with these non-interactive overrides:

| Skill step | Override |
|------------|----------|
| Step 0 (Ask for PR URL) | **Skip** — URL provided in step 1 above |
| Step 1 (Uncommitted changes) | **Auto-stash** — `git stash push -m "pr-review: stashed for PR #${PR_NUMBER} review"` instead of asking |
| Step 3 (Ask how to proceed) | **Always "Full review"** — skip the question |
| Step 8 (Agent dispatch) | **Use `/full-code-review` dispatch** (steps 3.5–4: run `plan-review-dispatch.py --mode pr`, apply triage, execute plan) instead of the skill's selective dispatch — ensures all eligible agents run with triage regardless of PR size |

All other skill steps execute as documented.

**State after Phase 1:** `PR_NUMBER`, `PR_TITLE`, `ORIGINAL_BRANCH`, `STASHED` (bool), `MERGE_BASE`, `GIT_RANGE`, `OUTPUT_DIR` (`/tmp/pr-review-${PR_NUMBER}`), `CHANGED_FILES`, `PR_SIZE`, the compiled context summary, and the reconciled review output in `OUTPUT_DIR`.

## Phase 2: Validation and Action Planning (via ingest-code-review)

### Step 3: Validate Findings and Build Action Plan

Apply `/ingest-code-review`.

## Phase 3: Output

### Step 4: Generate Review Document

Write the comprehensive review document to `${OUTPUT_DIR}/review-report.md`:

```markdown
# PR Review Report: #<PR_NUMBER> — <PR_TITLE>

**Generated:** <ISO timestamp>
**Author:** <PR author>
**Branches:** <headRefName> → <baseRefName>
**Size:** <category> (<files> files, +<added>/-<removed> lines)

---

## Context

### Problem Being Solved
<From linked issue or PR body>

### Acceptance Criteria
<If available from linked issue>

### Build Status
<Pass / Fail / N/A>

### Existing Reviews
<Summary of prior human and AI reviews>

---

## Review Scope

- **Git range:** <GIT_RANGE>
- **Files changed:** <count>
- **Agents dispatched:** <N> / 14
- **Agents skipped:** <list with reasons>
- **Branch freshness:** <fresh / stale by N commits>

---

## Validation Summary

| Finding | Source | Severity | Verdict | Reason |
|---------|--------|----------|---------|--------|
| ... | ... | ... | ... | ... |

**Totals:** X confirmed, Y likely valid, Z false positive, W out of scope

---

## Action Plan

### Critical (fix before merge)
- [ ] **<title>** — <file:line> — <description>

### Important (should address)
- [ ] **<title>** — <file:line> — <description>

### Consider
- [ ] **<title>** — <description>

### Dismissed
- **<title>** — <verdict>: <reason>

---

## Detailed Findings

<For each CONFIRMED and LIKELY VALID finding:>

### <title>
- **File:** <file:line>
- **Severity:** <severity>
- **Source:** <agent(s)>
- **Verdict:** <CONFIRMED/LIKELY VALID>
- **Description:** <what's wrong>
- **Recommendation:** <what to do>
- **Scope:** <one-liner / multi-file>

---

## Raw Review Files

All agent review files are in: `<OUTPUT_DIR>/`
- Reconciled summary: `reconciled.md`
- Individual agents: `<agent>-review.json`
```

### Step 5: Present Results and Ask About Workspace Restore

**Present brief summary to user:**

```
Review complete for PR #<PR_NUMBER> — <PR_TITLE>

Verdict: <APPROVE / REQUEST_CHANGES / COMMENT>
Findings: X critical, Y important, Z consider (W dismissed)

Full report: <OUTPUT_DIR>/review-report.md
All review files: <OUTPUT_DIR>/

Currently on branch: <headRefName> (PR branch)
Your previous branch: <ORIGINAL_BRANCH>
```

**Ask the user whether to restore the previous branch:**

```
AskUserQuestion:
  question: "You're currently on the PR branch (<headRefName>). Would you like to switch back to your previous branch (<ORIGINAL_BRANCH>)?"
  header: "Restore previous branch?"
  options:
    - label: "Restore previous branch"
      description: "Switch back to <ORIGINAL_BRANCH> and pop stash if applicable"
    - label: "Stay on PR branch"
      description: "Keep <headRefName> checked out (useful if you want to fix issues now)"
```

**If user chooses "Restore previous branch":**

```bash
git checkout ${ORIGINAL_BRANCH}
```

If changes were stashed earlier:
```bash
git stash pop
```

**If user chooses "Stay on PR branch":** If changes were stashed, remind the user: "Note: You have stashed changes from `<ORIGINAL_BRANCH>`. Run `git stash pop` after switching back."
