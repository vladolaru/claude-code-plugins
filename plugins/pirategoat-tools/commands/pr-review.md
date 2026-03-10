---
description: End-to-end PR review — gathers context, dispatches all review agents, validates findings, and saves a comprehensive review document
---

You are a PR review orchestrator. Your mission: chain together the pr-reviewing skill and the ingest-code-review validation into a single uninterrupted run that produces a saved review document.

**RULE 0: Run all phases autonomously.** Use sensible defaults for every decision point. The only user interaction is at the very end when asking about branch restoration.

**Phase failures are recoverable.** If a phase encounters errors:
- **Phase 1 failure** (pr-reviewing skill): Note what failed, skip to Step 6 with a partial report explaining what context is missing
- **Phase 2 failure** (no findings to ingest): Write a report noting "No findings from review agents" and proceed to Step 6
- **Phase 3 failure** (decision-critic error): Skip the critic step, note "Decision critic unavailable" in the report, present as-is

Adapt and continue — partial results are more valuable than no results.

### Pipeline

```
Phase 1  →  PR context + code review    (pr-reviewing skill, with dispatch override
                                          for full agent triage — see Step 2 overrides)
Phase 2  →  Validate findings            (/ingest-code-review on OUTPUT_DIR)
Phase 3  →  Generate report + stress-test + present
             (review-report.md → decision critic → user summary)
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
| Step 8 (Agent dispatch) | **Replace the skill's selective dispatch with the full-code-review dispatch pipeline.** Run three sub-steps in order: **(a)** Generate dispatch plan: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan-review-dispatch.py --mode pr --git-range "${GIT_RANGE}" --output-dir "${OUTPUT_DIR}"`. **(b)** Apply triage: for each conditional agent the planner marked DISPATCH, check its `triage_criteria` against the diffstat and commit messages; keep or downgrade to SKIP (when in doubt, DISPATCH). **(c)** Execute plan: dispatch ALL eligible agents in a SINGLE message with MULTIPLE Agent tool calls for parallel execution. After all agents return, run `reconcile-reviews.py` and dispatch the `review-reconciliator`. This ensures all eligible agents run with triage regardless of PR size. |

All other skill steps execute as documented.

**State after Phase 1:** `PR_NUMBER`, `PR_TITLE`, `ORIGINAL_BRANCH`, `STASHED` (bool), `MERGE_BASE`, `GIT_RANGE`, `OUTPUT_DIR` (`/tmp/pr-review-${PR_NUMBER}`), `CHANGED_FILES`, `PR_SIZE`, the compiled context summary, and the reconciled review output in `OUTPUT_DIR`.

## Phase 2: Validation and Action Planning (via ingest-code-review)

### Step 3: Validate Findings and Build Action Plan

Invoke `/ingest-code-review` with `OUTPUT_DIR` as the argument:

```
Skill tool:
  skill: pirategoat-tools:ingest-code-review
  args: ${OUTPUT_DIR}
```

Follow the ingest command's full workflow (preprocessing + 3-step verification). Proceed directly to Phase 3 after completion — present results to the user only in Step 6.

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

### Step 5: Decision Critic

Stress-test the review's conclusions before presenting them.

```
Skill tool:
  skill: pirategoat-tools:decision-critic
  args: <OUTPUT_DIR>/review-report.md
```

Follow the skill's full 7-step workflow. After the SYNTHESIS step produces a verdict, update the report and note how to present it:

- **STAND:** No report changes. Present findings as-is in Step 6.
- **REVISE:** Update `review-report.md` — adjust the action plan (upgrade/downgrade severities, recategorize findings, add or remove items). In Step 6, include a brief note of what the critic changed and why.
- **ESCALATE:** Update `review-report.md` — add a prominent warning that findings have significant validity concerns. In Step 6, flag prominently that findings need human review before acting.

### Step 6: Present Results and Ask About Workspace Restore

**Present brief summary to user based on the updated report:**

```
Review complete for PR #<PR_NUMBER> — <PR_TITLE>

Verdict: <APPROVE / REQUEST_CHANGES / COMMENT>
Decision Critic: <STAND / REVISE / ESCALATE> — <one-line key insight>
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
