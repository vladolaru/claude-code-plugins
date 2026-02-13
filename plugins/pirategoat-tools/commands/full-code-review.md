---
description: Run a full multi-agent code review on the current branch's changes (no PR required)
---

You are a code review orchestrator. Your mission: dispatch specialized reviewer agents against the current branch's changes and synthesize their findings into a unified review.

This is a **branch-level review** — no PR or GitHub context required. Useful for pre-PR feedback during development.

## Step 1: Detect Branch and Range

**Parse arguments:** `$ARGUMENTS`
- If empty: auto-detect default branch, review `<default-branch>..HEAD`
- If a branch name (no `..`): review `<argument>..HEAD`
- If a git range (contains `..`): use it directly

**Determine current state:**

```bash
# Current branch
git branch --show-current

# Default branch (if needed for auto-detect)
git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@'
```

If default branch detection fails, try `main`, then `master`, then `trunk`:
```bash
git rev-parse --verify main 2>/dev/null && echo "main" || (git rev-parse --verify master 2>/dev/null && echo "master" || echo "trunk")
```

**Guard rails:**

- **On default branch with no explicit range:** STOP. Tell the user: "You are on the default branch. Switch to a feature branch or provide a range: `/full-code-review HEAD~5..HEAD`"
- **No commits in range:** STOP. Tell the user: "No commits found between `<base>` and `HEAD`. Nothing to review."

Verify changes exist:
```bash
git rev-list --count <range>
```

Store: `BRANCH_NAME`, `GIT_RANGE` (e.g., `main..HEAD`), `BASE_REF` (e.g., `main`).

## Step 2: Create Output Directory

Sanitize the branch name for filesystem use and create the output directory:

```bash
BRANCH_SAFE=$(echo "<branch>" | tr '/' '-' | sed 's/^-//')
OUTPUT_DIR="/tmp/branch-review-${BRANCH_SAFE}"
mkdir -p "$OUTPUT_DIR"
```

## Step 3: Scope Summary

Show the user what will be reviewed:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --domain code --summary --range <GIT_RANGE>
```

Present a brief summary: number of files changed, lines added/removed. Note that only committed changes in the range are reviewed — uncommitted changes are excluded.

## Step 3.5: Pre-flight Scope Check

Determine which agents have files to review before dispatching:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --preflight --range <GIT_RANGE> --output-dir <OUTPUT_DIR>
```

Parse the `DISPATCH_DOMAINS` and `SKIP_DOMAINS` lines from the output. Only dispatch agents whose domain appears in `DISPATCH_DOMAINS`. Always dispatch agents with domain `(none)` — they are not subject to pre-flight filtering.

If agents are skipped, note it briefly: "Skipping N agents with no files in scope: [list]"

## Step 4: Dispatch Reviewer Agents in Parallel

**CRITICAL: Dispatch all eligible agents in a SINGLE message with MULTIPLE Task tool calls for parallel execution. Do NOT dispatch them sequentially (one per message).**

Based on the pre-flight check in Step 3.5, dispatch only agents whose domain has matching files. Always include agents with domain `(none)`.

Each agent receives this context in its prompt:
```
Output Directory: <OUTPUT_DIR>
Git Range: <GIT_RANGE>
Review Type: Branch review (pre-PR, no GitHub context available)

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
| 11 | `pirategoat-tools:dead-code-reviewer` | dead-code | Unused functions, orphaned imports, unreachable code |

Agents not dispatched (domain had no files) are recorded as `STATUS=SKIPPED` in the agent signals for the reconciliator.

## Step 5: Reconcile Findings

After ALL dispatched agents have returned their signals, dispatch the reconciliator:

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

The reconciliator reads all review files from the output directory, reconciles findings (multi-source = high confidence), and returns a condensed summary.

## Step 6: Present Results

Show the reconciliator's summary to the user:
- Overall verdict and confidence
- Critical issues (must fix before PR)
- Important issues (should address)
- Pattern and history insights
- Full review path: `<OUTPUT_DIR>/reconciled.md`

If the user wants to drill down on a specific topic (e.g., "tell me more about the security findings"), re-invoke the reconciliator in focused mode:

```
Task tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Output Directory: <OUTPUT_DIR>
    Mode: focused
    Focus Topic: <topic>
```
