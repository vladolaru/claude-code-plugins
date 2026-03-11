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

Present a brief summary: number of files changed, lines added/removed. Note that only committed changes in the range are reviewed — uncommitted changes are excluded.

## Step 3.5: Generate Dispatch Plan

Run the dispatch planner to determine which agents to dispatch:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/plan-review-dispatch.py \
  --mode full \
  --git-range "<GIT_RANGE>" \
  --output-dir "<OUTPUT_DIR>"
```

Parse the JSON output. Display the dispatch summary to the user showing which agents will be dispatched and which are skipped (with reasons).

Persist two representations from the planner output:
- `agent_signals` — the JSON array of per-agent status lines
- `agent_signals_text` — the planner-provided newline-joined text block

Use `agent_signals` when you want to inspect or summarize individual entries. Use `agent_signals_text` whenever you invoke `reconcile-reviews.py` or paste the signals into the reconciliator prompt. Do not rebuild this text yourself and do not expand it unquoted in the shell.

If agents are skipped, note it briefly: "Skipping N agents with no files in scope: [list]"

**Stale branch check:** Before dispatching, check branch freshness:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --preflight --range <GIT_RANGE> --output-dir <OUTPUT_DIR>
```
Parse the `BRANCH_FRESHNESS:` section from the preflight output.
- If `IS_STALE: true` and `RANGE_REBASED: true`: the scope has been automatically adjusted to use the merge-base (common ancestor) to exclude unrelated trunk changes.
  - Tell the user: "Branch is N commits behind base. Review scope adjusted to merge-base to exclude unrelated trunk files."
  - **Offer to freshen the base branch before suggesting rebase:** The local base ref may itself be out of date. Run `git fetch origin <base_branch>` and check if new commits arrived. If so, tell the user: "Local base branch was also outdated — fetched M new commits from origin. Consider rebasing: `git rebase origin/<base_branch>`". If already up to date, just suggest: "Consider rebasing before opening a PR: `git rebase origin/<base_branch>`"
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
Review Type: Branch review (pre-PR, no GitHub context available)

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

## Step 5: Reconcile Findings

After ALL dispatched agents have returned their signals, run the deterministic reconciliation script, then dispatch the reconciliator for narrative synthesis.

### Step 5a: Run Deterministic Reconciliation

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

### Step 5b: Dispatch Reconciliator for Narrative Summary

```
Task tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Output Directory: <OUTPUT_DIR>
    Mode: summary

    Agent Signals:
    <paste the exact agent_signals_text block from Step 3.5/Step 4 verbatim, one signal per line>
```

The reconciliator reads the pre-processed `reconciled-structured.json` and agent review files, then produces a narrative executive summary (`reconciled.md` and `reconciled.json`).

## Step 6: Ingest Review Findings

Invoke the ingest skill to validate findings, filter false positives, and produce an action plan:

```
Skill tool:
  skill: pirategoat-tools:ingest-code-review
  args: <OUTPUT_DIR>
```

Do not present results to the user yet — the pipeline continues to the decision-critic step.

## Step 6.5: Update Reconciled Summary with Validation Results

Update `reconciled.md` using the ingest validation results from Step 6 to reflect the validated state of findings:

1. **Annotate findings** with their validation verdict (CONFIRMED, LIKELY VALID, FALSE POSITIVE, OUT OF SCOPE, STYLE/PREFERENCE)
2. **Move dismissed findings** (FALSE POSITIVE, OUT OF SCOPE, STYLE/PREFERENCE) from the Critical/Important sections into a "Dismissed by Validation" section at the bottom
3. **Recalculate the verdict** from remaining confirmed + likely-valid findings: any critical → `REQUEST_CHANGES`, any high/medium → `COMMENT`, all clear → `APPROVE`
4. **Add a validation summary line** after the verdict: "Validation: X confirmed, Y likely valid, Z dismissed"

This ensures the decision critic in Step 7 reviews the post-validation state, not the raw reconciliation output.

## Step 7: Decision Critic

Stress-test the review's conclusions by dispatching the decision-reviewer agent:

```
Agent tool:
  subagent_type: pirategoat-tools:decision-reviewer
  prompt: |
    Document Path: ${OUTPUT_DIR}/reconciled.md
    Output Directory: ${OUTPUT_DIR}
```

The agent produces `decision-critic-findings.md` in OUTPUT_DIR and returns a verdict. Extract the verdict using this priority chain:

1. **Return message:** Parse the agent's return message for the `Verdict:` line (expected format: `Verdict: STAND|REVISE|ESCALATE`).
2. **Findings file fallback:** If the return message doesn't contain a parseable verdict, read `${OUTPUT_DIR}/decision-critic-findings.md` and extract the `**Verdict:**` value from its header.
3. **Critic unavailable:** If both sources fail (no file, no parseable verdict), note "Decision critic unavailable" in the final presentation and present the review as-is.

Once you have a verdict, act on it:

- **STAND:** The review conclusions are sound. No report updates needed.
- **REVISE:** Read `decision-critic-findings.md` for the recommended adjustments. Update `reconciled.md` — adjust the action plan (upgrade/downgrade severities, recategorize findings, add or remove items). **Recalculate the review verdict** from the updated findings (any critical → `REQUEST_CHANGES`, any high/medium → `COMMENT`, all clear → `APPROVE`). In Step 8, include a brief note of what changed and why.
- **ESCALATE:** Read `decision-critic-findings.md` for the validity concerns. Update `reconciled.md` — flag prominently that the review's validity has significant concerns requiring human judgment before acting on findings. **Override the review verdict to `COMMENT`** regardless of original verdict — the findings need human judgment before acting on any approve or request-changes signal. In Step 8, flag prominently that findings need human review before acting.

Do not present results between Step 6 and Step 7 — run them back-to-back.

## Step 8: Present Final Summary

Present the final review results to the user, incorporating the decision-critic's assessment:

- Overall verdict and confidence
- Decision-critic verdict (STAND / REVISE / ESCALATE) with key insight
- Critical issues (must fix)
- Important issues (should address)
- Any adjustments the decision-critic recommended (if REVISE)
- Full review files: `<OUTPUT_DIR>/`

If the user wants to drill down on a specific topic, re-invoke the reconciliator in focused mode:

```
Task tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Output Directory: <OUTPUT_DIR>
    Mode: focused
    Focus Topic: <topic>
```
