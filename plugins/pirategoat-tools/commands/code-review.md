---
description: Incremental code review of new commits on the current branch since the last review
---

You are a code review orchestrator. Your mission: dispatch specialized reviewer agents against **only the new commits** on the current branch since the last review, then synthesize findings.

This is an **incremental branch review** — it tracks what was previously reviewed and only covers new work. On first run, it reviews all changes (same as `/full-code-review`). Use `/full-code-review` when you want a comprehensive review of all branch changes regardless of prior reviews.

## Step 1: Parse Arguments and Detect Branch

**Parse arguments:** `$ARGUMENTS`
- If empty: incremental mode (auto-detect from state)
- If `full` or `reset`: force a full review from base branch, delete any existing state
- If a branch name (no `..`): review `<argument>..HEAD`
- If a git range (contains `..`): use it directly

**Determine current state:**

```bash
# Current branch and HEAD SHA
git branch --show-current
CURRENT_HEAD=$(git rev-parse HEAD)

# Default branch (if needed)
git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@'
```

If default branch detection fails, try `main`, then `master`, then `trunk`:
```bash
git rev-parse --verify main 2>/dev/null && echo "main" || (git rev-parse --verify master 2>/dev/null && echo "master" || echo "trunk")
```

**Guard rails:**

- **On default branch with no explicit range:** STOP. Tell the user: "You are on the default branch. Switch to a feature branch or provide a range: `/code-review HEAD~5..HEAD`"

**Create output directory:**

```bash
BRANCH_SAFE=$(echo "<branch>" | tr '/' '-' | sed 's/^-//')
OUTPUT_DIR="/tmp/branch-review-${BRANCH_SAFE}"
mkdir -p "$OUTPUT_DIR"
```

Store: `BRANCH_NAME`, `BASE_REF`, `CURRENT_HEAD`, `OUTPUT_DIR`.

## Step 2: Determine Review Range

This is the iterative core. The range depends on whether prior review state exists.

**If `full`, `reset`, or explicit range was given in Step 1:**
- If `full` or `reset`: delete `${OUTPUT_DIR}/.review-state.json` if it exists. Set `GIT_RANGE` to `<BASE_REF>..HEAD`.
- If explicit range or branch name: use it directly as `GIT_RANGE`.

**If no arguments (incremental mode):**

Check for state file:
```bash
cat "${OUTPUT_DIR}/.review-state.json" 2>/dev/null
```

**Case A — No state file (first run):**
Set `GIT_RANGE` to `<BASE_REF>..HEAD`. Tell the user: "First review on this branch. Reviewing all commits from `<BASE_REF>`."

**Case B — State file exists:**
1. Read `last_reviewed_sha` from the JSON.
2. Validate the SHA is still an ancestor of HEAD (handles rebases/force-pushes):
   ```bash
   git merge-base --is-ancestor <last_reviewed_sha> HEAD
   ```
3. **If validation fails** (exit code non-zero): The branch was rebased. Tell the user: "Branch history has changed since last review (rebase or force-push). Falling back to full review." Delete the state file. Set `GIT_RANGE` to `<BASE_REF>..HEAD`.
4. **If `CURRENT_HEAD` equals `last_reviewed_sha`:** STOP. Tell the user: "No new commits since last review at `<sha_short>` (`<last_reviewed_at>`). Nothing to review."
5. **Otherwise:** Set `GIT_RANGE` to `<last_reviewed_sha>..HEAD`. Tell the user: "Previous review covered commits up to `<sha_short>` (`<last_reviewed_at>`). Reviewing `<N>` new commits since then."

**Verify changes exist:**
```bash
git rev-list --count <GIT_RANGE>
```
If 0 commits: STOP. "No commits found in range. Nothing to review."

## Step 3: Scope Summary

Show the user what will be reviewed:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --domain code --summary --range <GIT_RANGE>
```

Present a brief summary: number of files changed, lines added/removed in the incremental range.

## Step 3.5: Pre-flight Scope Check

Determine which agents have files to review before dispatching:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --preflight --range <GIT_RANGE> --output-dir <OUTPUT_DIR>
```

Parse the `DISPATCH_DOMAINS` and `SKIP_DOMAINS` lines from the output. Only dispatch agents whose domain appears in `DISPATCH_DOMAINS`. Always dispatch agents with domain `(none)` — they are not subject to pre-flight filtering.

If agents are skipped, note it briefly: "Skipping N agents with no files in scope: [list]"

**Stale branch check (conditional):** Parse the `BRANCH_FRESHNESS:` section from the preflight output. Only act on staleness if the `history-insights-reviewer` is in the `DISPATCH_DOMAINS` list (domain: `code`). If the history-insights-reviewer is being skipped, the stale branch warning adds no value — skip this check.

If acting on staleness:
- If `IS_STALE: true` and `RANGE_REBASED: true`: Tell the user: "Branch is N commits behind base. Review scope adjusted to merge-base to exclude unrelated trunk files. Consider rebasing before your next review iteration."
- If `IS_STALE: false`: proceed normally, no message needed.

## Step 4: Dispatch Reviewer Agents in Parallel

**CRITICAL: Dispatch all eligible agents in a SINGLE message with MULTIPLE Task tool calls for parallel execution. Do NOT dispatch them sequentially (one per message).**

Based on the pre-flight check in Step 3.5, dispatch only agents whose domain has matching files. Always include agents with domain `(none)`.

Each agent receives this context in its prompt:
```
Output Directory: <OUTPUT_DIR>
Git Range: <GIT_RANGE>
Review Type: Branch review (incremental, reviewing only new commits since last review)

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
| 12 | `pirategoat-tools:dead-code-reviewer` | dead-code | Unused functions, orphaned imports, unreachable code |

Agents not dispatched (domain had no files) are recorded as `STATUS=SKIPPED` in the agent signals for the reconciliator.

## Step 5: Save Review State

After all agents have returned their signals, save the review state **before** running the reconciliator:

```bash
cat > "${OUTPUT_DIR}/.review-state.json" << 'STATEEOF'
{
  "last_reviewed_sha": "<CURRENT_HEAD>",
  "last_reviewed_at": "<current ISO timestamp>",
  "review_count": <previous count + 1, or 1 if first run>,
  "base_ref": "<BASE_REF>",
  "git_range_used": "<GIT_RANGE>"
}
STATEEOF
```

This ensures state is persisted even if reconciliation encounters an issue.

## Step 6: Reconcile and Present Results

Dispatch the reconciliator:

```
Task tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Output Directory: <OUTPUT_DIR>
    Mode: summary

    Agent Signals:
    <list all agent signals from Step 4>
    <for each skipped agent: "<agent>: STATUS=SKIPPED (no files in <domain> domain)">
```

Present the reconciliator's summary to the user:
- Overall verdict and confidence
- Critical issues (must fix)
- Important issues (should address)
- Pattern and history insights
- Full review path: `<OUTPUT_DIR>/reconciled.md`
- "Review state saved. Next `/code-review` will only review new commits."

If the user wants to drill down on a specific topic, re-invoke the reconciliator in focused mode:

```
Task tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Output Directory: <OUTPUT_DIR>
    Mode: focused
    Focus Topic: <topic>
```
