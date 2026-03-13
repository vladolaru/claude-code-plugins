---
description: End-to-end PR review — gathers context, dispatches all review agents, validates findings, and saves a comprehensive review document
---

You are a PR review orchestrator. Your mission: chain together the pr-reviewing skill and the ingest-code-review validation into a single uninterrupted run that produces a saved review document.

**RULE 0: Run all phases autonomously.** Use sensible defaults for every decision point. The only user interaction is at the very end when asking about branch restoration.

**Phase failures are recoverable — but recovery depends on how far you got.**

| Failure point | What's available | Recovery action |
|---------------|------------------|-----------------|
| Phase 1 — before PR details fetched (no URL, wrong repo, stash/checkout failure) | Nothing usable | **STOP.** Tell the user what failed and why. Restore branch/stash if any were touched. No report possible. |
| Phase 1 — after PR details but before agent dispatch (build failure, branch issues) | `PR_NUMBER`, `PR_TITLE`, `ORIGINAL_BRANCH`, `STASHED`, `OUTPUT_DIR` | Create `OUTPUT_DIR`, write a partial report (context sections only, no findings), skip to Step 6. |
| Phase 1 — during agent dispatch (some agents fail) | Full state, partial agent output | Continue with whatever agents succeeded. Reconcile available output, note failed agents in the report. |
| Phase 2 failure (no findings to ingest) | Full state, agent output | Write a report noting "No findings from review agents" and proceed to Step 6. |
| Phase 3 failure (decision-critic error) | Full state, complete report | Skip the critic step, note "Decision critic unavailable" in the report, present as-is. |

Adapt and continue — partial results are more valuable than no results. But don't fabricate a report when the required state doesn't exist.

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
| Step 1 (Uncommitted changes) | **Auto-stash** — `git stash push -u -m "pr-review: stashed for PR #${PR_NUMBER} review"` instead of asking. The `-u` flag includes untracked files (prevents checkout conflicts). After stashing, record the stash ref: `STASH_REF=$(git stash list --max-count=1 --format="%H")`. |
| Step 3 (Ask how to proceed) | **Always "Full review"** — skip the question |
| Step 7 (Before agent dispatch) | **Ground truth collection (optional).** First, read the project's CLAUDE.md/AGENTS.md and extract tool commands into `${OUTPUT_DIR}/tool-config.json` (same format as `/full-code-review` Step 2.5a — only include tools the project actually uses, write `{}` if none found). Then run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/run-ground-truth.py --output-dir "${OUTPUT_DIR}" --changed-files "${CHANGED_FILES_CSV}" --tool-config "${OUTPUT_DIR}/tool-config.json"`. If it succeeds, store `GROUND_TRUTH_PATH=OUTPUT_DIR/ground-truth-summary.json`. If it fails or produces no findings, continue without it. |
| Step 8 (Agent dispatch) | **Replace the skill's selective dispatch with the full-code-review dispatch pipeline.** Run three sub-steps in order: **(a)** Generate dispatch plan: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan-review-dispatch.py --mode pr --git-range "${GIT_RANGE}" --output-dir "${OUTPUT_DIR}"`. **(b)** Apply triage: for each conditional agent the planner marked DISPATCH, check its `triage_criteria` against the diffstat and commit messages; keep or downgrade to SKIP (when in doubt, DISPATCH). **(c)** Execute plan: dispatch ALL eligible agents in a SINGLE message with MULTIPLE Agent tool calls for parallel execution. Pass `--ground-truth <GROUND_TRUTH_PATH>` to each agent's bootstrap if available. After all agents return, run `reconcile-reviews.py` with `--changed-files` (comma-joined `changed_files` from the dispatch plan) and dispatch the `review-reconciliator`. This ensures all eligible agents run with triage regardless of PR size. |

All other skill steps execute as documented.

**State after Phase 1:** `PR_NUMBER`, `PR_TITLE`, `ORIGINAL_BRANCH`, `STASHED` (bool), `STASH_REF` (commit hash if stashed), `MERGE_BASE`, `GIT_RANGE`, `OUTPUT_DIR` (`/tmp/pr-review-${PR_NUMBER}`), `CHANGED_FILES`, `PR_SIZE`, the compiled context summary, and the reconciled review output in `OUTPUT_DIR`.

## Phase 2: Validation and Action Planning (via ingest-code-review)

### Step 3: Validate Findings and Build Action Plan

Invoke `/ingest-code-review` with `OUTPUT_DIR` as the argument:

```
Skill tool:
  skill: pirategoat-tools:ingest-code-review
  args: ${OUTPUT_DIR}
```

Follow the ingest command's full workflow (preprocessing + 3-step verification).

### Step 3b: Write Ingestion Verification Artifact

After the ingest workflow completes (Step 3 of the 3-step verification produces the categorized action plan), write the accumulated verification state to `${OUTPUT_DIR}/ingest-verification.json`. This artifact is passed to the decision-reviewer to avoid redundant re-verification.

Build the JSON from your accumulated `--thoughts` state:

```json
{
  "findings": [
    {
      "id": "F1",
      "title": "<finding title>",
      "status": "VERIFIED | FAILED | UNCERTAIN | OUT_OF_SCOPE",
      "file": "<file path if verified>",
      "line": <line number if verified>,
      "files_read": ["<path1:line>", "<path2:line>"],
      "evidence_summary": "<1-2 sentences: what the code actually showed>",
      "questions_asked": ["<verification question 1>"],
      "answers": ["<factual answer from code>"]
    }
  ],
  "summary": {
    "total": <N>,
    "verified": <N>,
    "failed": <N>,
    "uncertain": <N>,
    "out_of_scope": <N>
  }
}
```

Write this using the Write tool to `${OUTPUT_DIR}/ingest-verification.json`. Include ONLY findings that went through the verification pipeline (skip pre-classified OUT_OF_SCOPE findings that were never verified — list them with `status: "OUT_OF_SCOPE"` and empty `files_read`/`evidence_summary`).

Proceed to Phase 3 — present results to the user only in Step 6.

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

Stress-test the review's conclusions by dispatching the decision-reviewer agent:

```
Agent tool:
  subagent_type: pirategoat-tools:decision-reviewer
  prompt: |
    Document Path: ${OUTPUT_DIR}/review-report.md
    Output Directory: ${OUTPUT_DIR}
    Ingestion Verification: ${OUTPUT_DIR}/ingest-verification.json
```

The agent produces `decision-critic-findings.md` in OUTPUT_DIR and returns a verdict. Extract the verdict using this priority chain:

1. **Return message:** Parse the agent's return message for the `Verdict:` line (expected format: `Verdict: STAND|REVISE|ESCALATE`).
2. **Findings file fallback:** If the return message doesn't contain a parseable verdict, read `${OUTPUT_DIR}/decision-critic-findings.md` and extract the `**Verdict:**` value from its header.
3. **Critic unavailable:** If both sources fail (no file, no parseable verdict), treat as Phase 3 failure — note "Decision critic unavailable" in the report and present as-is.

Once you have a verdict, act on it:

- **STAND:** No report changes. Present findings as-is in Step 6.
- **REVISE:** Read `decision-critic-findings.md` for the recommended adjustments. **Before applying revisions, spot-check the critic's factual claims:** extract 2-3 claims that contain specific numbers, file paths, line references, or git metadata, and verify each with a single command (e.g., `git rev-list --count`, `grep -n`, `wc -l`). If any claim fails spot-check, strip it from the adjustments and note: "Critic claim X was not reproducible — excluded." Apply the remaining valid adjustments individually to `review-report.md` — upgrade/downgrade severities, recategorize findings, add or remove items. **Recalculate the review verdict** from the updated findings (any critical → `REQUEST_CHANGES`, any high/medium → `COMMENT`, all clear → `APPROVE`). In Step 6, include a brief note of what changed and why.
- **ESCALATE:** Read `decision-critic-findings.md` for the validity concerns. **Spot-check any factual claims as described above for REVISE.** Update `review-report.md` — add a prominent warning that findings have significant validity concerns. **Override the review verdict to `COMMENT`** regardless of original verdict — the findings need human judgment before acting on any approve or request-changes signal. In Step 6, flag prominently that findings need human review before acting.

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

If changes were stashed earlier (`STASHED` is true), find and pop the correct stash entry by matching the saved `STASH_REF`:
```bash
# Find the stash index that matches our saved ref
STASH_INDEX=$(git stash list --format="%gd %H" | grep "${STASH_REF}" | head -1 | cut -d' ' -f1)
git stash pop "${STASH_INDEX}"
```
If `STASH_REF` is not found in the stash list (e.g., it was already popped), skip the pop and warn the user: "The stash created for this review was not found — it may have been popped or dropped already."

**If user chooses "Stay on PR branch":** If changes were stashed, remind the user: "Note: You have stashed changes from `<ORIGINAL_BRANCH>`. To restore them later, find the stash entry matching message `pr-review: stashed for PR #${PR_NUMBER} review` and run `git stash pop <index>`."
