---
description: Run a full multi-agent code review on the current branch's changes (no PR required)
---

You are a code review orchestrator. Your mission: dispatch specialized reviewer agents against the current branch's changes and synthesize their findings into a unified review.

This is a **branch-level review** — no PR or GitHub context required. Useful for pre-PR feedback during development.

## Step 1: Gather Context

**Parse arguments:** `$ARGUMENTS`
- If empty: auto-detect default branch
- If a branch name (no `..`): review `<argument>..HEAD`
- If a git range (contains `..`): use directly

**Construct output directory** (sanitize all fragments for filesystem safety):

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
SAFE_REPO_PATH=$(echo "$REPO_ROOT" | tr '/' '-' | sed 's/^-//')
BRANCH=$(git branch --show-current)
SAFE_BRANCH=$(echo "$BRANCH" | tr -c 'a-zA-Z0-9._-' '-' | sed 's/^-//;s/-$//')
OUTPUT_DIR="/tmp/branch-review-${SAFE_REPO_PATH}-${SAFE_BRANCH}"
mkdir -p "$OUTPUT_DIR"
```

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gather-review-context.py \
  --branch \
  --git-range "<explicit range if provided>" \
  --output-dir "$OUTPUT_DIR"
```

Read `review-context.json` for `git_range`, `merge_base`, `github_cli_command`, changed files.

Guard rails: on default branch → stop; no commits in range → stop.

Store: Read `BRANCH_NAME`, `GIT_RANGE`, `BASE_REF`, `OUTPUT_DIR` from `review-context.json`.

## Step 2: Create Output Directory

Output directory was already created in Step 1. Proceed to ground truth collection.

## Step 3: Ground Truth Collection (optional)

### 3a — Extract tool configuration

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

### 3b — Run ground truth collection

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

## Step 4: Scope Summary

Show the user what will be reviewed:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/review-scope.py --domain code --summary --range <GIT_RANGE>
```

Present a brief summary: number of files changed, lines added/removed. Note that only committed changes in the range are reviewed — uncommitted changes are excluded.

## Step 5: Generate Dispatch Plan

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

Use `agent_signals` when you want to inspect or summarize individual entries. Do not rebuild this text yourself and do not expand it unquoted in the shell.

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

## Step 6: Adaptive Agent Triage

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

## Step 7: Execute Dispatch Plan

**CRITICAL: Dispatch all eligible agents in a SINGLE message with MULTIPLE Task tool calls for parallel execution. Do NOT dispatch them sequentially (one per message).**

For each agent with status "DISPATCH" in the plan (after triage adjustments in Step 6), dispatch using the Agent tool.

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
- Triage skip: `<agent>: STATUS=SKIPPED_TRIAGE (<one-line reason from Step 6>)`

## Step 8: Reconcile + Verify Findings

After ALL dispatched agents have returned their signals, dispatch the reconciliator to deduplicate, verify, and produce the review findings.

Build the list of completed agent review files from the dispatch results:
- For each agent that returned `STATUS=FINISHED`, add `<OUTPUT_DIR>/<agent>-review.json` to the list
- Skip agents with `STATUS=SKIPPED`, `STATUS=SKIPPED_TRIAGE`, or `STATUS=FAILED`

```
Agent tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Review Files:
    - <OUTPUT_DIR>/pr-review.json
    - <OUTPUT_DIR>/security-review.json
    ... (one per completed agent)

    Output Directory: <OUTPUT_DIR>
    Git Range: <GIT_RANGE>
    PR Context: Branch review in <REPO>
    Change Purpose: <1-3 sentence summary of what this branch does, derived from commit messages in GIT_RANGE>
    Changed Files: <CHANGED_FILES_CSV>
    Ground Truth: <GROUND_TRUTH_PATH>  (if available)
```

The reconciliator reads all agent findings, groups by underlying concern, verifies each against actual code, and writes:
- `review-findings.json` — structured output (ReviewOutputBuilder format)
- `review-findings.md` — narrative review summary

## Step 9: Decision Critic

Stress-test the review's conclusions by dispatching the decision-reviewer agent:

```
Agent tool:
  subagent_type: pirategoat-tools:decision-reviewer
  prompt: |
    Document Path: ${OUTPUT_DIR}/review-findings.md
    Output Directory: ${OUTPUT_DIR}
```

The agent produces `decision-critic-findings.md` in OUTPUT_DIR and returns a verdict. Extract the verdict using this priority chain:

1. **Return message:** Parse the agent's return message for the `Verdict:` line (expected format: `Verdict: STAND|REVISE|ESCALATE`).
2. **Findings file fallback:** If the return message doesn't contain a parseable verdict, read `${OUTPUT_DIR}/decision-critic-findings.md` and extract the `**Verdict:**` value from its header.
3. **Critic unavailable:** If both sources fail (no file, no parseable verdict), note "Decision critic unavailable" in the final presentation and present the review as-is.

Once you have a verdict, act on it:

- **STAND:** The review conclusions are sound. No report updates needed.
- **REVISE:** Read `decision-critic-findings.md` for the recommended adjustments. **Before applying revisions, spot-check the critic's factual claims:** extract 2-3 claims that contain specific numbers, file paths, line references, or git metadata, and verify each with a single command. If any claim fails spot-check, strip it from the adjustments. Apply remaining valid adjustments to `review-findings.md` — upgrade/downgrade severities, recategorize findings, add or remove items. **Recalculate the review verdict** from the updated findings (any critical → `REQUEST_CHANGES`, any high/medium → `COMMENT`, all clear → `APPROVE`).
- **ESCALATE:** Read `decision-critic-findings.md` for the validity concerns. **Spot-check any factual claims as described above for REVISE.** Update `review-findings.md` — flag prominently that the review's validity has significant concerns requiring human judgment before acting on findings. **Override the review verdict to `COMMENT`** regardless of original verdict.

## Step 10: Present Final Summary

Present the final review results to the user, incorporating the decision-critic's assessment:

- Overall verdict and confidence
- Decision-critic verdict (STAND / REVISE / ESCALATE) with key insight
- Critical issues (must fix)
- Important issues (should address)
- Any adjustments the decision-critic recommended (if REVISE)
- Full review files: `<OUTPUT_DIR>/`

If the user wants to drill down on a specific topic, re-invoke the reconciliator in focused mode:

```
Agent tool:
  subagent_type: pirategoat-tools:review-reconciliator
  prompt: |
    Output Directory: <OUTPUT_DIR>
    Mode: focused
    Focus Topic: <topic>
```
