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

## Step 2.5: Ground Truth Collection (optional)

### 2.5a — Extract tool configuration

Read the project's CLAUDE.md and AGENTS.md (if present). Extract the commands this project uses to run linters, test suites, security scanners, and coverage. Write a `tool-config.json` in OUTPUT_DIR:

```bash
cat > "${OUTPUT_DIR}/tool-config.json" << 'TOOLCFG'
{
  "<tool_name>": { "cmd": "<command template>" }
}
TOOLCFG
```

**Supported tools and their expected output files:**

| Tool name | Purpose | Expected output |
|-----------|---------|----------------|
| `eslint` | JS/TS linting | `eslint-results.json` (ESLint JSON format) |
| `phpcs` | PHP linting | `phpcs-results.json` (PHPCS JSON format) |
| `semgrep` | Security scanning | `semgrep-results.json` (Semgrep JSON format) |
| `jest` | JS/TS test results | `jest-results.json` (Jest JSON format) |
| `jest_coverage` | JS/TS coverage | `jest-coverage-summary.json` (Jest coverage-summary) |
| `phpunit` | PHP test results | `phpunit-results.json` (PHPUnit JSON/JUnit) |
| `phpunit_coverage` | PHP coverage | `phpunit-coverage.xml` (Clover XML) |

**Placeholders:** Use `{output_file}` for the tool's output path, `{output_dir}` for the output directory, `{files}` for changed files (shell-quoted by the script).

**Rules:**
- Only include tools the project actually uses and has instructions for
- If you can't determine the exact command, omit the tool
- If the project has no tool instructions at all, write an empty config `{}`

### 2.5b — Run ground truth collection

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/run-ground-truth.py \
  --output-dir "${OUTPUT_DIR}" \
  --changed-files "${CHANGED_FILES_CSV}" \
  --tool-config "${OUTPUT_DIR}/tool-config.json"
```

Where `CHANGED_FILES_CSV` is a comma-separated list of changed files from `git diff --name-only <GIT_RANGE>`.

- If the script exits non-zero or produces no findings, continue without ground truth — it is additive, not required.
- If it succeeds, `OUTPUT_DIR/ground-truth-summary.json` is available for the bootstrap dispatch phase.
- Store: `GROUND_TRUTH_PATH` = `OUTPUT_DIR/ground-truth-summary.json` (or empty if unavailable).

## Step 3: Scope Summary

Show the user what will be reviewed:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --domain code --summary --range <GIT_RANGE>
```

Present a brief summary: number of files changed, lines added/removed in the incremental range.

## Step 3.5: Generate Dispatch Plan

Run the dispatch planner to determine which agents to dispatch:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan-review-dispatch.py \
  --mode incremental \
  --git-range "<GIT_RANGE>" \
  --output-dir "<OUTPUT_DIR>"
```

Parse the JSON output. Display the dispatch summary to the user showing which agents will be dispatched and which are skipped (with reasons).

Persist two representations from the planner output:
- `agent_signals` — the JSON array of per-agent status lines
- `agent_signals_text` — the planner-provided newline-joined text block

Use `agent_signals` when you want to inspect or summarize individual entries. Use `agent_signals_text` whenever you invoke `reconcile-reviews.py` or paste the signals into the reconciliator prompt. Do not rebuild this text yourself and do not expand it unquoted in the shell.

If agents are skipped, note it briefly: "Skipping N agents with no files in scope: [list]"

**Stale branch check (conditional):** Only act on staleness if the `history-insights-reviewer` has status "DISPATCH" in the plan (domain: `code`). If the history-insights-reviewer is being skipped, the stale branch warning adds no value — skip this check.

If acting on staleness, check branch freshness:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --preflight --range <GIT_RANGE> --output-dir <OUTPUT_DIR>
```
Parse the `BRANCH_FRESHNESS:` section from the preflight output.
- If `IS_STALE: true` and `RANGE_REBASED: true`: Tell the user: "Branch is N commits behind base. Review scope adjusted to merge-base to exclude unrelated trunk files. Consider rebasing before your next review iteration."
- If `IS_STALE: false`: proceed normally, no message needed.

## Step 3.6: Adaptive Agent Triage

The dispatch planner handles domain-level filtering and deterministic triage (keyword matching, test-file detection, diffstat checks). **Its DISPATCH decisions are preliminary — they confirm the agent has matching files and basic signal, not that it should definitely run.** Your judgment here is the quality gate.

For **conditional agents** that the planner marked as DISPATCH, apply your own triage using the full context (diffstat, commit messages, file list, and the actual nature of the changes):

1. Check its **triage_criteria** (from the agent registry) against the diffstat and commit messages
2. Decide: keep **DISPATCH** or downgrade to **SKIP**
3. Log your reasoning (required for every decision)

**DEFAULT: When in doubt, DISPATCH.** Only skip when you are confident none of the criteria apply.

### Triage Output

For each conditional agent, log:

```
TRIAGE: <agent-name>: <DISPATCH|SKIP> — <one-line reasoning>
```

Example:
```
TRIAGE: security-reviewer: DISPATCH — PR adds new REST endpoint in src/api/users.ts
TRIAGE: dead-code-reviewer: SKIP — no files deleted, no refactoring commits, net +120 lines
TRIAGE: architecture-reviewer: SKIP — single component file changed, no structural reorganization
TRIAGE: wp-architecture-reviewer: DISPATCH — PHP files modify WooCommerce payment gateway hooks
TRIAGE: performance-reviewer: DISPATCH — new useQuery hook in data-fetching layer
TRIAGE: a11y-reviewer: DISPATCH — new modal component with form inputs
TRIAGE: reliability-reviewer: DISPATCH — new external API client without timeout/retry
```

Agents skipped by triage are recorded as `STATUS=SKIPPED_TRIAGE` in the agent signals for the reconciliator.

## Step 4: Execute Dispatch Plan

**CRITICAL: Dispatch all eligible agents in a SINGLE message with MULTIPLE Task tool calls for parallel execution. Do NOT dispatch them sequentially (one per message).**

For each agent with status "DISPATCH" in the plan (after triage adjustments in Step 3.6), dispatch using the Agent tool.

Each agent receives this context in its prompt:
```
Output Directory: <OUTPUT_DIR>
Git Range: <GIT_RANGE>
Review Type: Branch review (incremental, reviewing only new commits since last review)

When running bootstrap, include these flags:
python3 $PLUGIN_ROOT/scripts/bootstrap-reviewer.py --agent <agent-name> --range <GIT_RANGE> --output-dir <OUTPUT_DIR> --ground-truth <GROUND_TRUTH_PATH>
```

<!-- Agent dispatch reference table (sourced from agent-registry.json) -->

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
| 13 | `pirategoat-tools:a11y-reviewer` | a11y | ARIA correctness, keyboard access, focus management, WCAG 2.2 AA |
| 14 | `pirategoat-tools:reliability-reviewer` | reliability | Logging, error handling, rollback safety, feature flags, failure-mode resilience |

Agents not dispatched are recorded in agent signals for the reconciliator:
- Domain skip: `<agent>: STATUS=SKIPPED (no files in <domain> domain)`
- Triage skip: `<agent>: STATUS=SKIPPED_TRIAGE (<one-line reason from Step 3.6>)`

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

### Step 6a: Run Deterministic Reconciliation

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/reconcile-reviews.py \
  --output-dir <OUTPUT_DIR> \
  --agent-signals "$AGENT_SIGNALS_TEXT" \
  --changed-files "$(echo "$CHANGED_FILES_CSV")"
```

Where:
- `AGENT_SIGNALS_TEXT` is the exact `agent_signals_text` value from Step 3.5. It must remain a single quoted shell argument, even if it spans multiple lines. Never splat the list directly into the command, and never use an unquoted expansion such as `--agent-signals $AGENT_SIGNALS_TEXT`.
- `CHANGED_FILES_CSV` is the `changed_files` list from the Step 3.5 dispatch plan output, joined with commas. This enables test-gap detection advisories.

This script reads all `*-review.json` files, deduplicates findings across agents, and writes `reconciled-structured.json` to the output directory. It handles:
- Exact and near deduplication (same file + overlapping lines + similar title)
- Severity conflict resolution (highest wins)
- Source agent aggregation per cluster
- Schema validation (gracefully skips invalid outputs)
- Test-gap advisory (production code changed without tests, when `--changed-files` is provided)

### Step 6b: Dispatch Reconciliator for Narrative Summary

```
Task tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Output Directory: <OUTPUT_DIR>
    Mode: summary

    Agent Signals:
    <paste the exact agent_signals_text block from Step 4 verbatim, one signal per line>
```

The reconciliator reads the pre-processed `reconciled-structured.json` and agent review files, then produces a narrative executive summary (`reconciled.md` and `reconciled.json`).

Present the reconciliator's summary to the user:
- Overall verdict and confidence
- Critical issues (must fix)
- Important issues (should address)
- Pattern and history insights
- Full review path: `<OUTPUT_DIR>/reconciled.md`
- "Validating findings and running decision critic..."

## Step 7: Ingest Review Findings

After presenting the reconciled summary, automatically invoke the ingest skill to validate findings, filter false positives, and produce an action plan:

```
Skill tool:
  skill: pirategoat-tools:ingest-code-review
  args: <OUTPUT_DIR>
```

Do not wait for user input between Step 6 and Step 7 — run them back-to-back.

Do not present ingest results to the user separately — the pipeline continues to the post-ingest update and decision-critic steps.

## Step 7.5: Update Reconciled Summary with Validation Results

Update `reconciled.md` using the ingest validation results from Step 7 to reflect the validated state of findings:

1. **Annotate findings** with their validation verdict (CONFIRMED, LIKELY VALID, FALSE POSITIVE, OUT OF SCOPE, STYLE/PREFERENCE)
2. **Move dismissed findings** (FALSE POSITIVE, OUT OF SCOPE, STYLE/PREFERENCE) from the Critical/Important sections into a "Dismissed by Validation" section at the bottom
3. **Recalculate the verdict** from remaining confirmed + likely-valid findings: any critical → `REQUEST_CHANGES`, any high/medium → `COMMENT`, all clear → `APPROVE`
4. **Add a validation summary line** after the verdict: "Validation: X confirmed, Y likely valid, Z dismissed"

This ensures the decision critic in Step 8 reviews the post-validation state, not the raw reconciliation output.

## Step 7b: Write Ingestion Verification Artifact

Write the accumulated verification state to `${OUTPUT_DIR}/ingest-verification.json` using the same schema as `/pr-review` Step 3b. Build the JSON from your verification results (finding IDs, status, files_read, evidence_summary, questions, answers). This artifact is passed to the decision-reviewer to avoid redundant re-verification of already-confirmed claims.

## Step 8: Decision Critic

Stress-test the review's conclusions by dispatching the decision-reviewer agent:

```
Agent tool:
  subagent_type: pirategoat-tools:decision-reviewer
  prompt: |
    Document Path: ${OUTPUT_DIR}/reconciled.md
    Output Directory: ${OUTPUT_DIR}
    Ingestion Verification: ${OUTPUT_DIR}/ingest-verification.json
```

The agent produces `decision-critic-findings.md` in OUTPUT_DIR and returns a verdict. Extract the verdict using this priority chain:

1. **Return message:** Parse the agent's return message for the `Verdict:` line (expected format: `Verdict: STAND|REVISE|ESCALATE`).
2. **Findings file fallback:** If the return message doesn't contain a parseable verdict, read `${OUTPUT_DIR}/decision-critic-findings.md` and extract the `**Verdict:**` value from its header.
3. **Critic unavailable:** If both sources fail (no file, no parseable verdict), note "Decision critic unavailable" in the final presentation and present the review as-is.

Once you have a verdict, act on it:

- **STAND:** The review conclusions are sound. No report updates needed.
- **REVISE:** Read `decision-critic-findings.md` for the recommended adjustments. **Before applying revisions, spot-check the critic's factual claims:** extract 2-3 claims that contain specific numbers, file paths, line references, or git metadata, and verify each with a single command (e.g., `git rev-list --count`, `grep -n`, `wc -l`). If any claim fails spot-check, strip it from the adjustments and note: "Critic claim X was not reproducible — excluded." Apply the remaining valid adjustments individually to `reconciled.md` — upgrade/downgrade severities, recategorize findings, add or remove items. **Recalculate the review verdict** from the updated findings (any critical → `REQUEST_CHANGES`, any high/medium → `COMMENT`, all clear → `APPROVE`).
- **ESCALATE:** Read `decision-critic-findings.md` for the validity concerns. **Spot-check any factual claims as described above for REVISE.** Update `reconciled.md` — flag prominently that the review's validity has significant concerns requiring human judgment before acting on findings. **Override the review verdict to `COMMENT`** regardless of original verdict — the findings need human judgment before acting on any approve or request-changes signal.

## Step 9: Present Final Results

Present the final validated review to the user, building on the quick summary from Step 6:

- Updated verdict (if changed by validation or critic)
- Decision-critic verdict (STAND / REVISE / ESCALATE) with key insight
- Validation summary: X confirmed, Y likely valid, Z dismissed
- Any adjustments the decision-critic recommended (if REVISE)
- Any validity concerns (if ESCALATE)
- Full review files: `<OUTPUT_DIR>/`
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
