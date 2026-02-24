---
description: End-to-end PR review — gathers context, dispatches all review agents, validates findings, and saves a comprehensive review document
---

You are a PR review orchestrator. Your mission: gather full PR context, dispatch all specialized reviewer agents, validate their findings, and produce a comprehensive review document — all in one non-interactive pipeline.

This command combines three workflows into a single uninterrupted run:
1. **Context gathering** (from the pr-reviewing skill)
2. **Multi-agent code review** (from full-code-review)
3. **Finding validation and action planning** (from ingest-code-review)

**Design principle: ZERO interruptions.** This runs end-to-end without stopping for user input. All decisions use sensible defaults. The result is a saved review document.

## Phase 1: Context Gathering

### Step 1: Parse Arguments

**Parse arguments:** `$ARGUMENTS`
- Required: PR URL (e.g., `https://github.com/org/repo/pull/123`) or PR number (if CWD is the correct repo)
- If empty: STOP. Tell the user: "Usage: `/pr-review <PR_URL_or_number>`"

### Step 2: Get PR Details and Verify Repo

```bash
gh pr view <PR_URL_or_number> --json state,isDraft,author,title,body,labels,url,number,headRepository,headRepositoryOwner,headRefName,baseRefName
```

Extract: `PR_NUMBER`, `PR_TITLE`, `headRefName`, `baseRefName`, `author`, `body`, `state`, `isDraft`.

**Verify CWD matches PR repo:**

```bash
git remote get-url origin
```

Compare against `headRepositoryOwner.login/headRepository.name`. If repos don't match → STOP. Tell the user: "PR is for `<owner>/<repo>` but CWD is a different repo."

**Check PR state:**

| State | Action |
|-------|--------|
| `isDraft: true` | STOP — "PR #X is a draft. Mark as ready for review first." |
| `state: MERGED` | STOP — "PR #X is already merged." |
| `state: CLOSED` | STOP — "PR #X is closed." |
| Otherwise | Continue |

### Step 3: Prepare Workspace

**Save current branch:**

```bash
ORIGINAL_BRANCH=$(git branch --show-current)
```

**Auto-stash uncommitted changes (no user prompt):**

```bash
# Check for uncommitted changes
git status --porcelain
```

If output is non-empty:
```bash
git stash push -m "pr-review: stashed for PR #${PR_NUMBER} review"
# Remember: STASHED=true
```

**Fetch and update branches:**

```bash
git fetch origin

# Update target branch
git checkout <baseRefName>
git pull origin <baseRefName>

# Checkout PR branch
git checkout <headRefName> 2>/dev/null || git checkout -b <headRefName> origin/<headRefName>
git pull origin <headRefName>
```

**Compute merge-base (authoritative anchor for all diffs):**

```bash
MERGE_BASE=$(git merge-base origin/<baseRefName> <headRefName>)
```

Store: `PR_NUMBER`, `ORIGINAL_BRANCH`, `STASHED`, `MERGE_BASE`, `GIT_RANGE` = `${MERGE_BASE}..<headRefName>`.

### Step 4: Build Check

Look for AI instructions in the repo (`CLAUDE.md`, `.claude/`, etc.). If build instructions found, run the build on the PR branch. Note result (pass/fail) but **continue regardless** — a failing build is review feedback, not a reason to stop.

### Step 5: Gather Review State and Issue Context

**Review state (informational, not a decision point):**

```bash
gh api user --jq .login
gh pr view <PR_URL> --json reviews,reviewRequests,comments
```

Summarize: number of human reviews, AI reviews, pending reviewers, unresolved conversations. This is context for the report.

**Extract linked issue references from PR body:**
- Linear: `WOOPRD-1234`, `WOOPLUG-5678`, `WOOPMNT-999`
- GitHub: `Closes #123`, `Fixes #456`, `Refs #789`

**Fetch issue details (if linked):**

Use context-a8c or gh CLI to fetch issue with comments. Extract: problem being solved, acceptance criteria, related context.

If tool unavailable or issue not found: note it and continue. Not a stopping point.

### Step 6: Create Output Directory and Compile Context

```bash
OUTPUT_DIR="/tmp/pr-review-${PR_NUMBER}"
mkdir -p "$OUTPUT_DIR"
```

Compile the context summary (used in agent prompts and the final report):

```markdown
## PR Review Context

**PR:** #<number> - <title>
**Author:** <author>
**Branches:** <headRefName> → <baseRefName>
**Merge Base:** <MERGE_BASE>

### Problem Being Solved
<From linked issue or PR body>

### Build Status
<Pass / Fail (with errors) / No build instructions>

### Existing Review State
<N human reviews, M AI reviews, P pending>

### Key Verification Points
- [ ] Acceptance criteria met?
- [ ] Pending change requests addressed?
- [ ] Tests added/updated?
```

## Phase 2: Multi-Agent Code Review

### Step 7: Assess PR Size

```bash
# Full diff stats from merge-base
git diff --stat ${MERGE_BASE}..HEAD

# Code-only stats (exclude docs)
git diff --stat ${MERGE_BASE}..HEAD -- . ':!*.md' ':!*.txt' ':!*.rst' ':!docs/' ':!documentation/' ':!README*' ':!CHANGELOG*' ':!LICENSE*'

# Authoritative file list
git diff --name-only ${MERGE_BASE}..HEAD
```

| Category | Files | Lines | Note |
|----------|-------|-------|------|
| Small | 1-5 | < 200 | Standard review |
| Medium | 6-15 | 200-500 | Standard review |
| Large | 16-30 | 500-1000 | Note in report |
| Very Large | 30+ | 1000+ | Warn in report, proceed anyway |

Save `PR_SIZE` category and `CHANGED_FILES` list.

### Step 8: Pre-flight Scope Check

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --preflight --range <GIT_RANGE> --output-dir <OUTPUT_DIR>
```

Parse `DISPATCH_DOMAINS` and `SKIP_DOMAINS`. Only dispatch agents whose domain has matching files. Always dispatch agents with domain `(none)`.

If agents skipped: note in report — "Skipping N agents with no files in scope: [list]"

**Stale branch check:** Parse `BRANCH_FRESHNESS:` section. If stale, note in report and fetch latest:
```bash
git fetch origin <baseRefName>
```

### Step 9: Dispatch All Review Agents in Parallel

**CRITICAL: Dispatch all eligible agents in a SINGLE message with MULTIPLE Task tool calls for parallel execution. Do NOT dispatch them sequentially (one per message).**

Each agent receives this context in its prompt:
```
PR ID: <PR_NUMBER>
Output Directory: <OUTPUT_DIR>
Git Range: <GIT_RANGE>
Review Type: PR review (full context available)
PR Goal: <from context summary>
Changed Files (authoritative): <file list from step 7>

CONSTRAINT: Only review files on the changed files list. Files not listed are NOT part of this PR.

When running bootstrap, include these flags:
python3 $PLUGIN_ROOT/scripts/bootstrap-reviewer.py --agent <agent-name> --range <GIT_RANGE> --output-dir <OUTPUT_DIR>
```

| # | Agent | Domain | Focus |
|---|-------|--------|-------|
| 1 | `pirategoat-tools:pr-reviewer` | code | Goal alignment, bugs, code quality |
| 2 | `pirategoat-tools:security-reviewer` | security | XSS, SQL injection, CSRF, sanitization |
| 3 | `pirategoat-tools:performance-reviewer` | performance | N+1 queries, caching, optimization |
| 4 | `pirategoat-tools:architecture-reviewer` | architecture | SOLID, design patterns, coupling |
| 5 | `pirategoat-tools:wp-architecture-reviewer` | wp-architecture | Hooks, WPCS, backwards compatibility |
| 6 | `pirategoat-tools:patterns-reviewer` | patterns | Existing patterns, consolidation |
| 7 | `pirategoat-tools:history-insights-reviewer` | code | Git history precedents, lessons learned |
| 8 | `pirategoat-tools:php-tests-reviewer` | php-tests | PHPUnit test quality |
| 9 | `pirategoat-tools:js-tests-reviewer` | js-tests | Jest/Vitest test quality |
| 10 | `pirategoat-tools:e2e-tests-reviewer` | e2e-tests | Playwright E2E test quality |
| 11 | `pirategoat-tools:go-tests-reviewer` | go-tests | Go test quality |
| 12 | `pirategoat-tools:dead-code-reviewer` | dead-code | Unused functions, orphaned imports |

Agents not dispatched (domain had no files) are recorded as `STATUS=SKIPPED` in agent signals.

### Step 10: Reconcile Findings

After ALL agents return signals, dispatch the reconciliator:

```
Task tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Output Directory: <OUTPUT_DIR>
    Mode: summary

    Agent Signals:
    <list all agent signals from Step 9>
    <for each skipped agent: "<agent>: STATUS=SKIPPED (no files in <domain> domain)">
```

## Phase 3: Validation and Action Planning

### Step 11: Validate Every Finding

**RULE 0: Trust nothing. Verify everything against actual code.**

Read the reconciled output:

```bash
cat "${OUTPUT_DIR}/reconciled.json"
```

If not present, fall back to `reconciled.md`, then individual agent files.

**For EACH finding, perform these checks:**

1. **File in scope?** — Compare `file` against `CHANGED_FILES`. Not changed → OUT OF SCOPE.
2. **About changed code?** — Check if finding's line falls in diff hunks (`git diff <GIT_RANGE> -- <file>`). Pre-existing unchanged code → OUT OF SCOPE (unless the change directly interacts with it).
3. **Accurate?** — Read the actual code at the referenced location. Does it do what the finding claims?
4. **Confidence?** — Multi-agent (2+) = higher trust. Single-agent low-confidence (<0.6) = skeptical.

### Step 12: Categorize and Plan

**Categorize each finding:**

| Category | Criteria |
|----------|----------|
| **CONFIRMED** | Verified against actual code, in scope, accurate |
| **LIKELY VALID** | In scope, plausible, not fully verified |
| **FALSE POSITIVE** | Inaccurate or based on misunderstanding |
| **OUT OF SCOPE** | About code not changed in this PR |
| **STYLE/PREFERENCE** | Subjective, not a defect |

**Build action plan from CONFIRMED and LIKELY VALID findings only:**

1. **Critical / Must Fix** — Security vulnerabilities, data loss, crashes
2. **Important / Should Fix** — Bugs, performance, significant quality issues
3. **Consider** — LIKELY VALID but uncertain
4. **Dismissed** — FALSE POSITIVE and OUT OF SCOPE with explanations

## Phase 4: Output

### Step 13: Generate Review Document

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
- **Agents dispatched:** <N> / 12
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

### Step 14: Present Results and Ask About Workspace Restore

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

**If user chooses "Stay on PR branch":** Do nothing. If changes were stashed, remind the user: "Note: You have stashed changes from `<ORIGINAL_BRANCH>`. Run `git stash pop` after switching back."
