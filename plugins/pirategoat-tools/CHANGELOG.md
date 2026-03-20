# Changelog

All notable changes to the pirategoat-tools plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.77.1] - 2026-03-20

### Changed

- **`codex-reviewer` agent** — rewrite invocation to use `codex exec review` (non-interactive) with `developer_instructions` for additive prompt injection, `-o` for output capture, `--ephemeral` for throwaway sessions. Bump timeout from 180s to 30 minutes. Remove Gemini comparison section. Prompt-optimized with research-backed patterns: numbered rule priority (RULE 0-4), contrastive examples for signal block output, affirmative directives, redundancy elimination (202→148 lines), error normalization at top for primacy effect.
- **Prompt-optimized `code-clarity-reviewer` and `docs-drift-reviewer`** — applied 6 prompt engineering techniques: passive exclusion lists → active False Positive Gate checklists (Anticipatory Reflection + CoVe), recency reinforcement before output (sentence template forces proof articulation), structured reasoning templates in verification steps, compressed confidence scoring with hard cutoff, removed redundant bash examples (~210 token savings per agent), strengthened "move on" signals to prevent sunk-cost investigation of non-findings.
- **Prompt-optimized `api-contract-reviewer`, `data-flow-privacy-reviewer`, `concurrency-reviewer`, and `reliability-reviewer`** — same 6 techniques applied: active False Positive Gate checklists (domain-specific questions), STOP signals in RULE 0 requiring concrete scenario articulation, structured reasoning templates with early-exit paths, compressed confidence scoring with hard cutoffs, and "Final Check Before Writing Output" sentence templates tailored to each domain.
- **Step 5 orchestrator briefing rewritten to encourage active pruning** — replaced "Trust the planner" / high override bar with guidance to actively skip low-signal dispatches. Explicitly calls out the weak "no triage signal to skip" reason as a default, not evidence. Inverts the default posture to "lean toward skipping over dispatching." Makes over-dispatch cost concrete (tokens, time, reconciliation noise). Force-skip listed before force-dispatch to signal it's the more common action.

## [1.77.0] - 2026-03-20

### Added

- **`docs-drift-reviewer` agent** — detects when code changes cause external documentation to become stale. Checks README, CLAUDE.md, AGENTS.md, API docs, guides, and AI-facing conventions for claims invalidated by the PR's code changes. Two verification tiers: shallow (symbol matching for renames/removals) and deep (behavioral comparison for logic changes). Four categories: stale symbol references, behavioral drift, incomplete API enumeration, and stale examples. Only flags docs made stale by the current change — not pre-existing staleness or missing docs. Adds `docs-drift` domain to review-scope.py.

## [1.76.0] - 2026-03-20

### Added

- **`code-clarity-reviewer` agent** — reviews naming accuracy, documentation correctness, and intent communication. Flags names that lie (get_ that mutates, validate_ that transforms), docblocks that contradict code (@param/@return mismatches, stale claims), and semantic inconsistency (same concept with multiple names in one file). Language-agnostic, behavioral-proof-required verification standard. Conditional dispatch with broad triggers on any diff containing new/modified functions, classes, docblocks, or signatures. Adds `clarity` domain to review-scope.py.

## [1.75.1] - 2026-03-20

### Fixed

- **`sed` error in output directory construction on macOS** — all three review commands (`/pr-review`, `/full-code-review`, `/code-review`) used `sed 's/^-//'` to strip a leading dash from the sanitized repo path. When the LLM joined the code block lines into a single shell command, the `sed` pattern got corrupted, producing `unescaped newline inside substitute pattern`. Replaced with `${REPO_ROOT#/}` parameter expansion before `tr`, eliminating `sed` entirely.
- **`sed` in reviewer-protocol fallback** — same class of issue in the Scope Discovery fallback code block (`sed 's@^refs/remotes/origin/@@'`). Replaced with parameter expansion. This path is skipped by bootstrap in normal operation but fixed for consistency.

## [1.75.0] - 2026-03-20

### Added

- **`create-github-pr` skill** — structured PR creation workflow with pre-flight checks (branch guard, existing PR detection), context gathering, content generation with testing steps synthesis from related merged PRs, user approval gate, draft creation, and post-creation assignee/milestone prompts. Moved from global `~/.claude/skills/` into the plugin.

## [1.74.0] - 2026-03-20

### Changed

- **Decision critic uses review-specific 4-phase prompt injection** (`review-critic.py`) instead of the generic 7-step `decision-critic.py` — tailored for severity calibration, false positive detection, and code-grounded verification. Migrates epistemic boundary, tool citation, state accumulation, and academic grounding from the generic script.
- **Decision critic dropped from Opus to Sonnet + effort high** — the structured pipeline compensates.
- **Step 10 dispatch prompt now includes output directory path** for the critic.
- **Review budget uses domain-scoped metrics** — budget is now computed from each agent's domain-filtered diff size (TOTAL_DIFF_LINES from review-scope.py), not the PR total. Agents with small scopes get proportionally smaller budgets. Reverts the 1.73.1 decision to use PR-level metrics after session analysis showed every agent getting budget=80 regardless of scope.
- **Budget text enforces hard ceiling** — replaced "calibration hint" framing and "genuine lead" escape hatch with a hard ceiling at 1.5× target and "MUST stop" instruction. Agents can no longer self-justify indefinite budget overruns.
- **History-insights budget aligned** — agent definition now references the bootstrap budget instead of stating a conflicting number (~40 vs ~80).
- **History-insights dispatch class changed from `always` to `conditional`** — the enhanced `focus` field signals to the step 5 orchestrator when to skip (net-new code with no history to mine). The conservative triage default still dispatches when no signal skips it, but the `"conditional (domain has files, no triage signal to skip)"` reason is now visible to the orchestrator (vs suppressed for `always` agents).
- **History-insights agent definition includes parallel batching hint** — instructs the agent to issue all Tier 1 searches for all scenarios in a single turn since they have no data dependencies on each other.
- **Agent registry focus fields enhanced with skip signals** — patterns-reviewer, dead-code-reviewer, api-contract-reviewer, and history-insights-reviewer now include "high value when / low value for" guidance in their focus fields, giving the step 5 orchestrator explicit context for override decisions.
- **Triage keywords narrowed to reduce false positive dispatch reasons** — replaced overly broad substring-matched keywords that mislead the step 5 orchestrator: `transaction` → `begin_transaction`/`db_transaction` (concurrency), `focus` → `focus trap`/`focus management`/`focusable` (a11y), `user`/`customer`/`log`/`payment`/`card` → more specific compound forms (data-flow-privacy), `api` → `rest api`/`api endpoint` (api-contract), `query` → `db query`/`sql query` (performance). Conservative default still dispatches regardless — narrower keywords just prevent false positive reason strings from discouraging orchestrator overrides.
- **Architecture-reviewer triage criteria cleaned** — removed "Deployment or infrastructure architecture changes (Dockerfiles, Terraform, CI pipelines, Helm charts)" which was misaligned with the agent's code-architecture focus (SOLID, coupling, cohesion). Infrastructure concerns belong to security-reviewer (CI/CD configs) and reliability-reviewer (deployment, rollback).

### Added

- **`budget_override` field in agent-registry.json** — fixed tool-call budget for agents whose workload doesn't correlate with diff size. History-insights-reviewer uses override=45 (aligned with its ~40 git commands guidance).
- **`budget_target` in telemetry `agent_start` events** — enables cost analysis without parsing raw session JSONL files.

### Fixed

- **Duplicated `=== REVIEW SCOPE ===` header** — `build_output()` prepended the header, but `review-scope.py` output already includes it. Also stripped the header from exploration scope output (patterns-reviewer).

## [1.73.2] - 2026-03-19

### Fixed

- **Step 2 tests silently stashed uncommitted edits** — `TestStep2Orchestration` ran `setup-workspace.py` via subprocess without `cwd` isolation. The script detected dirty working tree state in the real repo, ran `git stash push -u`, and silently stashed uncommitted changes. Now tests initialize a temp git repo and pass `cwd=tmp_path`.
- **File history test hardcoded single agent** — `TestFileHistory` excluded only `history-insights-reviewer` but `api-contract-reviewer` also has `file_history: true`. Now reads the registry dynamically.

## [1.73.1] - 2026-03-19

### Fixed

- **Scope extraction stopped at first FILES block** — `extract_scope_files()` and `extract_scope_line_count()` broke out of the loop after the first `=== FILES ===` block, silently ignoring secondary domain scopes (e.g., config-ops appended for security/architecture/reliability reviewers). Budget, telemetry, and file history now accumulate across all scope blocks.
- **Critic template referenced nonexistent findings JSON in degraded flow** — the step 10 decision critic dispatch template unconditionally included `review-findings.json`, which doesn't exist when reconciliation failed. Now conditionally included only when reconciliation succeeded.
- **Review budget derived from structured PR metrics** — budget computation now reads `pr_size` from `review-context.json` (PR-level lines/files/category) instead of parsing `=== FILES ===` sections from scope output. More accurate (reflects overall PR complexity, not domain-filtered subset) and avoids prose parsing. Falls back to scope extraction when context unavailable.

## [1.73.0] - 2026-03-19

### Fixed

- **Stale dispatch_plan_summary after overrides** — summary was computed at step 5 from the initial plan, never updated after SKIPPED_OVERRIDE edits. Now recomputed at step 6 from the final dispatch-plan.json on disk.
- **Pipeline state verdict: null** — `pipeline-state.json` had `verdict: null` despite the review completing successfully. Step 11 now writes the verdict from `review-verdict.json` into state.
- **Agent outputs with pr_id: 0** — bootstrap now reads PR number from `review-context.json` as fallback when scope discovery doesn't provide it.

### Added

- **Scope-proportionate budget hints** — bootstrap injects a calibrated tool call budget (15 + changed_lines/10, capped at 80) with reinforcement on why staying on budget matters: critical path bottleneck, diminishing returns, and depth matching complexity.
- **Decision critic evidence anchors** — step 10 briefing now provides an exact dispatch template including `review-findings.json` path, giving the critic targeted verification anchors instead of re-discovering evidence independently.

### Removed

- **`agent_signals_text` from dispatch plan** — redundant `"\n".join(agent_signals)` field. The `agent_signals` list remains.
- **`dispatch_plan_output` from pipeline state** — full plan JSON was triple-stored (dispatch-plan.json, escaped string, parsed array). Removed the escaped string copy (~9KB per run).

## [1.72.2] - 2026-03-19

### Fixed

- **Telemetry summary override counting** — `_build_summary()` counted `DISPATCH_OVERRIDE` agents as skipped instead of dispatched. Switched from exact key match (`k != "DISPATCH"`) to prefix matching (`k.startswith("DISPATCH")`) so both `DISPATCH` and `DISPATCH_OVERRIDE` are counted as dispatched.
- **Step 10 REVISE verdict too vague** — the REVISE instruction said "Apply recommended adjustments" without specifying what file to edit. In practice the LLM wrote verdict files but skipped editing `review-report.md`. Expanded all three verdict instructions with concrete per-verdict actions: REVISE now has a numbered checklist that explicitly names the report file, STAND clarifies to proceed directly, and ESCALATE spells out the COMMENT override.
- **Step 7 polling replaced with notification-based waiting** — step 7 told the LLM to poll in a `sleep` loop, blocking the session for 9+ minutes. Now instructs to wait for background agent notifications and use the status check only as a safety confirmation. Step 8's existing `TaskStop` handles any stragglers.

## [1.72.1] - 2026-03-19

### Fixed

- **Status check misclassified SKIPPED_OVERRIDE agents** — `check-reviewer-agent-status.py` used a hardcoded list of skip statuses (`SKIP`, `SKIPPED`, `SKIPPED_TRIAGE`) that missed `SKIPPED_OVERRIDE`. Agents overridden at step 5 fell through to the dispatch path and were reported as `NOT_DISPATCHED (never started)`, inflating the "never started" count. Switched to prefix matching (`status.startswith("SKIP")`) to handle all current and future skip variants.

## [1.72.0] - 2026-03-19

### Changed

- **Step 5 override briefing** — tightened the orchestrator's dispatch override guidance. Explains why the planner is trustworthy (5 signal sources + agent self-triage), sets a concrete override bar (structural gaps only), and frames force-dispatch as low-risk due to the quick relevance check backstop.

## [1.71.0] - 2026-03-19

### Added

- **Quick relevance check** — reviewer protocol now instructs agents to scan diff hunks for domain relevance before deep analysis. Agents that find no relevant changes exit immediately with APPROVE, saving minutes of wasted analysis on false-positive dispatches.

## [1.70.0] - 2026-03-19

### Added

- **Multi-source keyword triage** — dispatch planner now matches `triage_keywords` against commit messages, file paths, PR title, PR body, PR labels, branch name, and linked issue titles. Each keyword match reports which source triggered it (e.g., `keywords matched (files: payment; pr: security)`).

### Changed

- **Dispatch reason messages** — keyword match reasons now indicate the signal source (commits/files/pr) instead of always saying "commit keywords matched".

## [1.69.0] - 2026-03-19

### Added

- **Domain-specific scoping** — new `api-contract`, `data-flow`, and `concurrency` domains in DOMAIN_CATALOG so those reviewers only see relevant file types instead of the catch-all `code` domain.

### Changed

- **api-contract-reviewer** — domain narrowed from `code` to `api-contract` (php/js/ts/py/go/sql, excludes CSS/SCSS and test files).
- **data-flow-privacy-reviewer** — domain narrowed from `code` to `data-flow` (php/js/ts/py/rb/go/java/sql, excludes CSS/SCSS and test files).
- **concurrency-reviewer** — domain narrowed from `code` to `concurrency` (php/js/ts/py/go/java/sql, excludes CSS/SCSS and test files).
- **reliability-reviewer** — removed CSS/SCSS from domain (no operational resilience concerns in stylesheets).

## [1.68.0] - 2026-03-19

### Changed

- **Model pinning** — all agents now have explicit model assignments, removing `inherit` dependency on the orchestrator session. Three-tier model strategy: opus (pr-reviewer, a11y-reviewer, decision-reviewer, review-reconciliator), sonnet (all domain specialists), haiku (go-tests-reviewer).
- **history-insights-reviewer** — fixed registry/frontmatter mismatch: registry said `inherit` but `.md` said `sonnet`. Aligned both to `sonnet`.

## [1.67.0] - 2026-03-19

### Added

- **api-contract-reviewer** — new conditional agent that detects backwards-incompatible REST API changes, hook/filter argument breaks, response shape drift, and missing deprecation paths.
- **data-flow-privacy-reviewer** — new conditional agent that traces PII through code paths, flags sensitive data in logs and API responses, identifies missing GDPR erasure handlers, and reviews payment data handling.
- **concurrency-reviewer** — new conditional agent that identifies race conditions, TOCTOU patterns, missing database transactions, cache stampede, non-idempotent operations, and concurrent state corruption.

### Changed

- **wp-architecture-reviewer** — tightened backwards-compatibility scope to WordPress ecosystem contracts; REST API contract stability now deferred to api-contract-reviewer.
- **security-reviewer** — tightened Sensitive Data Exposure to security-exploitable exposure; PII lifecycle and GDPR concerns now deferred to data-flow-privacy-reviewer.
- **reliability-reviewer** — added explicit scope boundary: concurrency correctness now handled by concurrency-reviewer.

## [1.66.0] - 2026-03-18

### Added

- **Deterministic workspace setup** — new `scripts/setup-workspace.py` handles stash, branch recording, and PR checkout as a subprocess instead of 4 LLM tool calls. Outputs JSON for the pipeline to persist.

### Changed

- **Step 2 (Repo Setup) runs deterministically** — the pipeline's `_orchestrate_step` now calls `setup-workspace.py` before generating the briefing. On success, the briefing confirms what happened; on failure, it falls back to manual instructions.
- **gh/ghe auto-detection in workspace setup** — fixes a latent bug where step 2 always defaulted to `gh` even for GitHub Enterprise repos.

## [1.65.3] - 2026-03-18

### Fixed

- **`decision-reviewer` and `tests-mutation-reviewer` excluded from dispatch plan** — `plan-review-dispatch.py` now skips agents with `dispatch_class: "special"` or `"manual"` before building the plan, so they never appear in `dispatch-plan.json`, the agent status report, or the step 5 triage list. Previously they appeared as `SKIPPED (special only)` / `SKIPPED (manual only)`, adding noise and risking unintended LLM triage overrides.

## [1.65.2] - 2026-03-18

### Changed

- **Test suite cleanup** — removed 126 tests (1036 → 910) that tested prose keywords, trivial operations (constructors, setters, list.append), or internal logic already covered by integration tests. Deleted `test_commands_pr_update.py` and `test_commands_switch_to.py` (prose-keyword tests); trimmed bootstrap unit tests superseded by `test_bootstrap_integration.py`; consolidated `TestStart` in telemetry (12 → 4 tests); removed JSON type-checking from agent registry tests; removed phase-transition keyword tests from pipeline briefing tests; deduplicated command/grader tests across files.

## [1.65.1] - 2026-03-18

### Changed

- **Test file splitting** — split `test_review_pipeline.py` (1915 lines) into three focused files: infrastructure (routing, state, CLI), orchestration (subprocess, telemetry), and briefings (step guidance output); split `test_bootstrap_reviewer.py` (1043 lines) into unit and integration files; split `test_commands.py` (713 lines) by extracting per-command tests into own files with shared helpers module

## [1.65.0]

### Added

- **Pipeline mission statement** — step 1 briefing now anchors the orchestrator on its identity, quality goals, and artifact discipline
- **Phase-transition anchoring** — contextual reminders at phase boundaries (SETUP→EXECUTION, EXECUTION→SYNTHESIS, SYNTHESIS→VALIDATION, VALIDATION→OUTPUT) keep the orchestrator aligned as work shifts character
- **Structured data discipline** — file-producing steps (3, 4, 8, 9, 10) now have verification checkpoints and `handoff` gates; step 10 uses schema format instead of copyable placeholder values
- **Unified command mission** — all three review commands (pr-review, full-code-review, code-review) share identical mission language with mode-specific context

- **Change-purpose propagation to specialist reviewers** — `bootstrap-reviewer.py` now reads `change-purpose.md` (the main session's distilled PR synthesis) and injects it as a `=== REVIEW FOCUS (pipeline synthesis) ===` section after PR INTENT, giving specialist agents richer context than raw PR metadata alone
- **Hard readiness gate at step 8** — `review-pipeline.py` now runs `check-reviewer-agent-status.py` before generating the reconciliation briefing; if agents are still RUNNING, step 8 returns a "BLOCKED" briefing instead of proceeding with incomplete data
- **Human-readable step 5 dispatch summary** — step 5 now presents dispatched/skipped agents as a readable list with focus descriptions instead of injecting the raw JSON plan output; the override mechanism still references `dispatch-plan.json` by path
- **Agent focus descriptions in dispatch plan** — `plan-review-dispatch.py` now includes the `focus` field from the agent registry in each dispatch plan entry, giving the main session concrete knowledge of what each agent reviews for informed override decisions
- **Agent name/focus sync rule** — AGENTS.md now documents the requirement to keep registry `focus` and agent `.md` `description` aligned when updating agent specializations

### Changed

- **Agent registry focus values** — sharpened 11 of 16 agent focus descriptions to be specific enough for informed dispatch override decisions while remaining concise (e.g., "PHPUnit test quality" → "PHPUnit assertions, WP test utilities, WooCommerce patterns, Brain Monkey isolation")

- **`review-pipeline.py`** — unified pipeline script following the curated-context-pipeline pattern
- **`run-config.json`** — caller-provided config, immutable during run (replaces config fields in old flat `pipeline-state.json`)
- **`pipeline-result.json`** — machine-readable result for callers (written by script at step 11)
- **`review-verdict.json`** — LLM→script verdict handoff (written by LLM at step 10)
- **`.branch-review-baseline.json`** — replaces `.review-state.json`, written for ALL modes
- `interactive` flag in run-config.json for non-interactive/autonomous runs
- `output_instructions` in run-config.json — callers can fully override review output tone/style
- Author display name resolution in gather-review-context.py
- GitHub issue details fetched programmatically with full bodies
- Stale branch detection in gather-review-context.py (all modes)
- Script builds complete agent dispatch prompts (steps 6, 8, 10)
- Script runs plan-review-dispatch.py internally at step 5
- Precise degraded paths for steps 8-10 with canonical artifact priority chain
- **Pipeline orchestration** — `main()` now runs subprocesses at steps 3, 5, 6, 7, 8, 11: gathers context, plans dispatch, populates agents, writes baseline, reads change purpose, syncs findings verdict, writes `pipeline-result.json`
- `_orchestrate_step()` extracted for readability; `_run_subprocess()` helper for all subprocess calls
- Telemetry `finalize()` called at the last active step (emits `pipeline_end` event)
- Step 1 clears stale `review-context.json` for interactive runs only (preserves bot pre-written context)
- Step 8 briefing instructs stopping background agents before reconciliation
- **PR intent propagation to specialist reviewers** — `bootstrap-reviewer.py` now reads `review-context.json` and injects a `=== PR INTENT ===` section

### Changed

- **current-datetime skill** (formerly date-time-wrangling) — rewritten RULE 0 to trigger on any date/time dependency (writing timestamps, stating times, calculating staleness), not just temporal reasoning questions. Added "Most Common Command" section. Removed duplicate examples, workflow tutorials, and platform detection — same operational value in half the lines.
- Unified pr-review, full-code-review, and code-review into a single pipeline script
- All three commands now thin wrappers (~40 lines each) calling review-pipeline.py
- Review report synthesis now runs for all modes (previously PR-only)
- Review baseline (`.branch-review-baseline.json`) written for all modes (previously incremental-only)
- Triage authority model unified: dispatch planner decisions are authoritative across all modes
- Split state model: `run-config.json` (caller config) + `pipeline-state.json` (execution state)
- Pipeline is self-contained — no dependency on user's CLAUDE.md for review quality
- Non-interactive PR mode without pre-computed context is now a hard error at step 2
- Verdict chain: LLM writes review-verdict.json → script reads it → script updates review-findings.json → script writes pipeline-result.json

### Removed

- **date-time-wrangling skill** — renamed to **current-datetime** with rewritten description to trigger on any date/time dependency, not just temporal reasoning questions
- `pr-review-pipeline.py` — replaced by unified `review-pipeline.py`
- `review-scope.py --preflight` — stale branch detection moved to `gather-review-context.py`
- `.review-state.json` — replaced by `.branch-review-baseline.json` (with migration fallback)
- `source == "pirategoat-bot"` identity check — replaced by state-driven `merge_base` detection

### Fixed

- Branch review flows now pass dispatch plan to reconciliator
- Decision critic input unified to review-report.md for all modes
- Full review followed by incremental review now correctly starts from full review baseline
- Step 7 briefing instructs agent-completion check via `check-reviewer-agent-status.py` before proceeding to reconciliation — prevents starting reconciliation while agents are still running
- Step 8 surfaces completed review file paths in the reconciliator prompt — eliminates cognitive load on the main session to discover them
- Step 9 re-injects change purpose (or commit message fallback) into the situation block — re-anchors the model after parallel agent fan-out and reconciliation
- Critic verdict (STAND/REVISE/ESCALATE) persisted to `decision-critic-verdict.json` and read by step 11 — breaks reliance on conversational memory for a critical validation step

## [1.61.0] - 2026-03-16

### Removed

- **Ground truth collection step** — Removed linting, security scanning, test runners, and coverage parsers from the review pipeline. Real-world testing showed near-zero actionable signal: Semgrep free rules produced 100% noise on production PRs, and linting/tests duplicated what CI already surfaces. Agents now rely on their own analysis without pre-computed tool findings
- **Scripts removed:** `run-ground-truth.py`, `parse-linter-results.py`, `parse-security-results.py`, `parse-test-results.py`, `parse-coverage-results.py`, and 4 shell wrapper scripts
- **Ground truth injection** from `bootstrap-reviewer.py` (`--ground-truth` flag, `format_ground_truth_section()`)
- **Ground truth cross-referencing** from `reconcile-reviews.py` (`--ground-truth` flag, `_load_ground_truth()`, `_match_ground_truth()`)
- **Ground truth step** from `pr-review-pipeline.py` (Step 7 removed, pipeline reduced from 15 steps to 14), `code-review.md` (Step 3 removed), and `full-code-review.md` (Step 3 removed)
- **Ground truth references** from reviewer-protocol.md, dead-code-reviewer.md, review-reconciliator.md, and tests-reviewer-protocol.md
- **Dead ingest scripts** — `ingest-preprocess.py` and `ingest-code-review.py` had been marked "no longer called as a pipeline step" since their functionality was absorbed by the reconciliator agent. No active callers in the pipeline, commands, or pirategoat-bot
- **Dead reconcile script** — `reconcile-reviews.py` was replaced by the review-reconciliator agent. No active callers in the pipeline, commands, or pirategoat-bot

### Added

- **E2E stream-content assertions for review state** — `StreamMonitor` accumulates text per step and supports `stream_assertion` callbacks on checkpoints. PR 2 and PR 3 expectations verify that Step 3 (review state analysis) mentions `CHANGES_REQUESTED` and `APPROVED` respectively in the stream output

## [1.60.0] - 2026-03-16

### Changed

- **STATE_REQ contract** — Now includes PR purpose, key changes, and review focus areas alongside branch/stash/verdict fields. Matches what the pipeline actually requires
- **Step 8 triage** (was Step 10) — Script's dispatch decisions are now authoritative. Claude only overrides with strong evidence and explicit logging
- **Steps 3+4+5 collapsed** — Three steps (PR Review State, Decide Approach, Extract Linked Issue) merged into one "Review Context Summary" step. Reviews and linked issues are now pre-computed by `load_context_values()` instead of re-queried. Pipeline reduced from 17 steps (0-16) to 15 steps (0-14)
- **Step 10 reconciliator context** (was Step 12) — Now passes `dispatch-plan.json` path and file paths for ALL dispatched agents, enabling the reconciliator to track expected-but-missing agents
- **Ground truth two-phase pattern** — Step 7 (was Step 9) now uses the same tool discovery + `run-ground-truth.py` execution pattern as `/full-code-review` and `/code-review`
- **Tool slots merged** — `jest_coverage` merged into `jest`, `phpunit_coverage` merged into `phpunit`. Coverage flags go in the test command. Legacy names still accepted
- **Tool scoping guidance** — Linters/scanners scoped to changed files (`{files}`), test suites run full project, coverage scoped to changed files

### Improved

- **Ground truth parallel execution** — `collect_ground_truth()` now runs all configured tools concurrently via `ThreadPoolExecutor`. Wall time drops from sum(all tools) to max(single tool)
- **Ground truth parsing resilience** — Result parsing now checks for output files on disk rather than tool name in `tools_run`, supporting merged test+coverage runs

### Fixed

- **Stale ground truth files** — `collect_ground_truth()` now cleans all known tool output files before running tools. File-existence-based parsing could ingest leftover results from a previous run in reused output directories
- **Triage override agents dropped** — Step 10 now instructs to include review files from agents dispatched via triage override in Step 8. Overrides stored in `--thoughts` weren't reflected in `dispatch-plan.json`, so manually dispatched agents were excluded from reconciliation
- **Branch-name issue extraction** — `load_context_values()` now extracts issue IDs from `head_ref` (e.g., `fix/WOOPLUG-5988-desc` → `WOOPLUG-5988`), restoring the branch-name extraction lost when Steps 3+4+5 were collapsed

## [1.59.0] - 2026-03-16

### Added

- **Agent-level telemetry** — Reviewer agents now log `agent_start` (from bootstrap-reviewer.py) and `agent_complete` (from ReviewOutputBuilder.save()) events to the telemetry JSONL log. Captures per-agent timing, domain, scope size, verdict, and issue counts

### Changed

- **Remove headless mode** — The PR review pipeline always runs autonomously. Removed `--headless` flag, collapsed three-way branching (bot_mode / headless / interactive) to two-way (bot_mode / default), and removed dead interactive code paths. Eliminates the source of the step 16 cleanup regression
- **Split Present Results / Cleanup** — Step 15 now only presents review results. Step 16 handles workspace cleanup (branch restore, stash pop). Pipeline grows from 16 steps (0-15) to 17 steps (0-16)
- **Telemetry log directory** — Moved from `~/.claude/logs/pirategoat-tools/pr-reviews/` to `~/.pirategoat-tools/logs/pr-reviews/`

### Fixed

- **Workspace cleanup regression** — Step 16 cleanup incorrectly skipped workspace restore for non-bot runs. Since step 1 auto-stashes and checks out the PR branch, the user's workspace was left altered after every review. Root cause was the three-way branching — now eliminated
- **Agent name mismatch in completion telemetry** — `ReviewOutputBuilder.save()` passed the stripped reviewer name (e.g., `security`) to `log_agent_complete`, but bootstrap writes `.started` files and `agent_start` events using the full agent name (`security-reviewer`). Duration was always null and start/complete events couldn't be correlated
- **Scope metrics in agent_start telemetry** — `scope_files` and `scope_lines` were computed from raw scope text (total line count and character count), not from the structured `=== FILES ===` section. Extracted helper functions that parse the actual file entries and sum `(+N -M)` stats

## [1.58.0] - 2026-03-16

### Added

- **Review telemetry** — PR review pipeline now logs JSONL telemetry to `~/.claude/logs/pirategoat-tools/pr-reviews/`. Each step captures timing; the final step captures a full snapshot of output directory state (context, dispatch, agent results, findings) plus aggregate metrics. Re-reviews produce separate log files. Telemetry is best-effort and never breaks the pipeline

### Changed

- **CC-style output directory naming** — Review output directories now derive from the repo's absolute path (`git rev-parse --show-toplevel` with slashes replaced by dashes) instead of parsing `git remote` for owner/repo. Makes output dirs unique per clone/worktree, enabling parallel reviews of the same repo from different checkouts. Affects `/pr-review`, `/full-code-review`, and `/code-review`

## [1.57.2] - 2026-03-15

### Changed

- **Pass change purpose to reconciliator** — All three review commands now include a `Change Purpose` field in the reconciliator prompt (1-3 sentence summary of what the change does). Enables the reconciliator to calibrate finding severity by relevance to the change's goal, closing a gap where borderline findings could be dropped before the main session could recalibrate them with PR context

## [1.57.1] - 2026-03-15

### Fixed

- **Agent status mismatch in reconciliation** — `full-code-review.md`, `code-review.md`, and `pr-review-pipeline.py` referenced `STATUS=COMPLETE` when selecting agent outputs for reconciliation, but all agents return `STATUS=FINISHED`. Reconciliator received empty file lists on every run
- **Wrong data source for completed agents** — `pr-review-pipeline.py` step 12 read `dispatch-plan.json` (pre-dispatch decisions only) instead of `check-reviewer-agent-status.py` output to find completed agents
- **`/pr-review` step count mismatch** — `pr-review.md` invoked the pipeline with `--total-steps 14` but the script defines 16 steps (0-15). Step 14 returned `next = Step 15` which failed the range check, preventing pipeline completion. Also updated stale Phase Overview and Failure Recovery tables
- **`/pr-update` artifact lookup** — Step 4 still searched for `reconciled.md` instead of the new `review-findings.md` filenames, causing silent loss of review context

## [1.57.0] - 2026-03-15

### Changed

- **Reconciliator agent redesign** — The reconciliator now owns full post-agent processing: semantic deduplication, scope checking, fact verification, and clean output production in a single pass. Replaces the broken deterministic dedup (Jaccard title-similarity threshold failed to merge any cross-agent findings across 13 real sessions) and separate ingest verification phase
- **Decision critic independence** — The decision critic no longer receives ingestion verification artifacts. It operates fully independently, verifying claims against actual code
- **Pipeline step renumbering** — All review commands now use sequential integer step numbering (no more 2.5/3.5/3.6/5a/5b/6.5/6b). PR review pipeline is 16 steps (0-15) with dedicated REVIEW (13) and VALIDATION (14) phases
- **Clean output format** — Review output reads like one expert reviewer wrote it: no agent names, no cluster metadata, no source_agents fields. Multi-agent convergence is a confidence signal, not user-facing information

### Removed

- `/ingest-code-review` command — functionality absorbed by the reconciliator
- `reconcile-reviews.py` as a pipeline step (script remains for standalone utility use)
- Ingestion verification pass-through to the decision critic
- `reconciled.json`, `reconciled.md`, `reconciled-structured.json` output artifacts (replaced by `review-findings.json` and `review-findings.md`)

## [1.56.0] - 2026-03-15

### Added

- **E2E `verdict_in` assertion** — computes the verdict from reconciled cluster severities using the same threshold logic as `ReviewOutputBuilder` and checks it against the expected verdict list
- **E2E `must_skip_triage` assertion** — checks `dispatch-plan.json` agent statuses for `SKIPPED_TRIAGE`, matching `plan-review-dispatch.py`'s actual output shape

### Fixed

- **E2E severity assertion reads `canonical.severity`** — the severity assertion previously read `c.get('severity')` which doesn't exist on reconciled clusters; all findings silently fell back to `'medium'`, making critical and high findings invisible to E2E assertions
- **Reliability domain excludes `_test.go` and `_test.php` files** — the reliability exclude pattern was missing Go and PHP test file conventions, causing test-only diffs to be reviewed for operational resilience
- **Architecture domain no longer false-positives on `contest`** — the exclude pattern used a bare `test` match that caught words like `contest_handler.go`; now uses the shared `_TEST_EXCLUDE` constant

### Changed

- **Shared `_TEST_EXCLUDE` constant for production-code domains** — dead-code, architecture, and reliability domains now share a single exclude pattern, preventing future drift
- **Verdict threshold docs updated** — `AGENTS.md` now documents the full escalation thresholds (3+ highs → block, 5+ mediums → request_changes) instead of the simplified version

## [1.55.0] - 2026-03-15

### Changed

- **Scope budget sort order reversed to largest-first** — large files now get budget priority instead of being systematically excluded when the diff-line budget is tight

### Fixed

- **Semantic filter no longer strips suppression directives from diffs** — `eslint-disable`, `phpcs:ignore`, `@ts-ignore`, `noqa`, `nosec`, `@deprecated`, `TODO`, `FIXME`, and other intent-bearing comments are preserved
- **NOT_DISPATCHED agents no longer block pipeline completion** — status check proceeds to reconciliation instead of stalling with ACTION REQUIRED; reconciliation reports these agents as NOT_RUN instead of FAILED
- **Incremental review state validated with ancestry check** — `last_reviewed_sha` from `.review-state.json` is now verified as an ancestor of HEAD before use, preventing incorrect review ranges after rebases or force-pushes

## [1.54.1] - 2026-03-15

### Fixed

- **Emit absolute script paths in pipeline steps** — `pr-review-pipeline.py` now computes `SCRIPTS_DIR` from its own location and emits absolute paths instead of unresolved `$PLUGIN_ROOT/scripts/...` references
- **Fix `--git-range` → `--range` in bootstrap-reviewer invocation** — Step 11 emitted `--git-range` but `bootstrap-reviewer.py` expects `--range`, causing every agent bootstrap to fail with unrecognized arguments
- **Recompute context on reruns instead of reusing stale data** — Steps 1-2 now only skip repo setup and context discovery in bot mode (`source: "pirategoat-bot"`), not whenever `review-context.json` exists from a prior run. Non-bot reruns delete the stale context file and recompute fresh git context
- **Always recompute git context in `gather-review-context.py`** — `load_and_fill()` no longer skips `_fill_git_context()` when `merge_base` already exists in the context file. Only bot-pre-computed context (with no explicit overrides) is preserved. Fixes incremental branch reviews, explicit `--git-range` inputs, and reruns after new commits
- **Include untracked files in stash and use STASH_REF for restore** — `git stash push` now passes `-u` to capture untracked files, and cleanup uses `git stash apply/drop <STASH_REF>` instead of blind `git stash pop`
- **Act on decision critic verdict before presenting results** — Step 13 now instructs the agent to wait for the critic, read `decision-critic-findings.md`, spot-check factual claims, and act on verdicts: REVISE recalculates the verdict from updated findings, ESCALATE overrides the verdict to COMMENT and flags validity concerns in the report. Matches the behavior in `/code-review` and `/full-code-review`
- **E2E tests monitor the actual pipeline output directory** — `test_pipeline.py` now watches `/tmp/pr-review-<owner>-<repo>-<PR>` (where the real pipeline writes) instead of a pytest-managed temp path, and cleans stale artifacts before each run
- **Align review filename convention across status check and reconciliation** — `check-reviewer-agent-status.py` and `reconcile-reviews.py:discover_agent_signals()` now apply the same `-reviewer` suffix stripping as `bootstrap-reviewer.py`, matching the files agents actually write
- **Read `issues` key instead of `findings` in status check and signal discovery** — matches `ReviewOutputBuilder`'s output schema
- **Surface advisories in reconciliator narrative** — test-gap warnings and other advisories from `reconciled-structured.json` are now promoted to recommendations and processed by the reconciliator agent

### Changed

- **Unify triage language across all review entry points** — `/pr-review` Step 10 now describes bidirectional triage (upgrade or downgrade conditional agents) matching `/full-code-review` and `/code-review`

## [1.54.0] - 2026-03-15

### Added

- **`gather-review-context.py`** — unified Ring 1 context script for all review entry points. Gap-filling design: reads existing `review-context.json`, fills what's missing, writes the complete file. Supports `--pr-number`, `--branch`, and `--branch --incremental` modes
- **`pr-review-pipeline.py`** — 15-step mode-aware step-injection script that replaces both the `reviewing-pr` skill and the `/pr-review` override table. Bot mode detected from `review-context.json`, headless mode from `--headless` flag
- **`check-reviewer-agent-status.py`** — deterministic agent status check with four states: FINISHED, RUNNING, TIMED_OUT, NOT_DISPATCHED. Reads dispatch plan + `.started` markers + review files
- **`.started` markers in `bootstrap-reviewer.py`** — each agent writes a timestamped marker on entry for status tracking
- **E2E test harness (`tests/e2e/`)** — two-layer e2e test suite for the `/pr-review` pipeline against a permanent test repo (`vladolaru/pirategoat-pr-review-pipeline-test-repo`). Layer 1: script-level subprocess tests (fast, free). Layer 2: full pipeline via Claude CLI with real-time JSONL stream monitoring and step-level checkpoint assertions. Includes `StreamMonitor`, `PRExpectations` dataclass, assertion helpers, and checkpoint builders

### Changed

- **`/pr-review` command rewritten as step-injection pipeline** — the 265-line command with 7-entry override table is now ~80 lines delegating to `pr-review-pipeline.py`. No override table — mode switching is deterministic script logic
- **`/full-code-review` and `/code-review` use `gather-review-context.py`** — replaces ad-hoc branch detection, range computation, and output directory creation with the shared context script
- **Reconciliation uses `--dispatch-plan` for signal discovery** — `reconcile-reviews.py` now accepts `--dispatch-plan` to discover agent status from files instead of LLM-composed `--agent-signals` text
- **`plan-review-dispatch.py` schema normalized** — `"dispatch"` → `"agents"`, `"agent"` → `"name"` in the dispatch plan output. Also writes `dispatch-plan.json` to `--output-dir`
- **`ingest-code-review.py` and `decision-critic.py` output formatting aligned** — ═══ separators, phase in header, (MANDATORY) next pointers
- **`dig-into-linear-issue` PR handoff updated** — invokes `/pr-review` instead of the deleted `reviewing-pr` skill

### Removed

- **`reviewing-pr` skill deleted** — folded into `/pr-review` pipeline script

## [1.53.3] - 2026-03-14

### Added

- **Tool selection guidance in reviewer-protocol** — new "Tool Selection for Search" section instructs agents to use the Grep tool instead of Bash grep/rg for working-tree searches, with glob exclusion examples for node_modules/build filtering

### Fixed

- **Added Grep tool to decision-reviewer and review-reconciliator toolsets** — session analysis found the decision-reviewer used Bash grep for all 35 working-tree searches because Grep was not available to it
- **Use PR number instead of URL for `gh pr view`** — the reviewing-pr skill now extracts the PR number and uses `gh pr view <number>` which resolves against the CWD's git remote, avoiding failures when the URL points to a fork
- **Use Linear MCP server directly instead of context-a8c** — Linear is not a context-a8c provider; both reviewing-pr and dig-into-linear-issue now reference `mcp__linear-server__get_issue` directly, eliminating 2-3 wasted tool calls per session
- **Linear MCP is now a hard requirement for dig-into-linear-issue** — skill stops with a user prompt if the Linear MCP server is not available, instead of silently falling back
- **Pass GIT_RANGE to ingest from pr-review** — the ingest command now accepts `--git-range` so pr-review passes the already-computed range, skipping the `.review-state.json` lookup that always fails in the pr-review flow

## [1.53.2] - 2026-03-14

### Changed

- **reviewing-pr: prompt optimization pass with 5 research-backed techniques.** Applied targeted prompt engineering patterns to reduce redundancy and improve behavioral clarity (1038 → 958 lines, ~8% reduction):
  - Consolidated 3 overlapping guardrail sections (Common Mistakes, Red Flags, Correct/Incorrect) into a single Guardrails section with STOP metacognitive triggers and contrastive examples (STOP Escalation + Contrastive Examples)
  - Compressed 52-line parallel dispatch anti-pattern into a 12-line contrastive example, eliminating Everything-Is-Critical emphasis dilution (Emphasis Hierarchy)
  - Simplified review strategy table: stated defaults once (`pr-reviewer` + `patterns-reviewer`), table shows only additional specialists per PR type (Category-Based Generalization)
  - Converted 3 negative instructions ("Do NOT assume", "Do NOT just list", "no apology needed") to affirmative directives (Affirmative Directives)

### Removed

- **Verbose Reasoning Mode** (`VERBOSE=true`) removed from `reviewing-pr` skill and `reviewer-protocol.md` — never used in practice

### Fixed

- Fixed 2 pre-existing step-reference bugs: cleanup step referenced as "step 8" instead of "step 9"

## [1.53.1] - 2026-03-14

### Changed

- **Decision-reviewer now receives ingestion verification artifacts** (`ingest-verification.json`) to avoid redundant file re-reads — expected to eliminate ~53% of overlapping file reads based on 9-session analysis
- All three review commands (`pr-review`, `full-code-review`, `code-review`) write `ingest-verification.json` after ingestion and pass it to the decision-reviewer dispatch
- Renamed `pr-reviewing` skill to `reviewing-pr` for naming consistency
- Removed legacy `ai-memory` integration from `reviewing-pr` skill (Claude Code has its own memory system)

## [1.52.1] - 2026-03-13

### Changed

- **history-insights-reviewer: prompt optimization pass with 6 research-backed techniques.** Applied targeted prompt engineering patterns to reduce noise and improve behavioral compliance (302 → 257 lines, 15% reduction):
  - Removed redundant Review Checklists section that duplicated Phase 1-4 instructions (Reasoning Compression)
  - Converted `--all` and `-p -S` warnings from "NEVER/Do NOT" to STOP metacognitive checkpoints that explain consequences (STOP Escalation Pattern)
  - Rewrote negative directives as affirmative: "do NOT run git diff manually" → "Read diffs directly from REVIEW SCOPE" (Affirmative Directives)
  - Added Error Normalization for expected empty git log results and GitHub API failures
  - Compressed 19-line "What to Look For" bullet lists into a 5-line hint table (Hint-Based Guidance + Compression)
  - Removed 3 redundant constraints from "Important Constraints" already covered in phases, keeping 2 unique ones (Scope Limitation)

## [1.52.0] - 2026-03-13

### Changed

- **history-insights-reviewer: tiered search scoping to prevent exploration drift.** Replaced unscoped repo-wide `--grep` searches (returning noise from the entire commit history) with concentric-circle search strategy:
  - Tier 1: changed files — path-scoped, OR-mode keywords, `head -10`
  - Tier 2: sibling directories — same module, `head -15`
  - Tier 3: repo-wide — AND-mode (`--all-match`) required, `head -10`
- **history-insights-reviewer: diff-grounded keyword extraction.** Phase 1 now explicitly instructs the agent to extract concrete search keywords from the diff (function names, class names, domain terms) before searching. Removes canned broad terms like `--grep="fix"` that matched too many unrelated commits.
- **history-insights-reviewer: git-native result limiting.** Replaced all `| head -N` pipes with git's `-n N` flag. Added `-i` (case-insensitive) to all `--grep` and pickaxe searches.
- **history-insights-reviewer: keyword combining decision guide.** Inline rules for when to use single keywords, OR-mode (synonyms), or AND-mode (narrowing broad terms), tied to the tier strategy.

## [1.51.0] - 2026-03-12

### Changed

- **history-insights-reviewer: scenario-budgeted exploration with analysis document.** Replaced the dead-letter "~35 git commands" budget (routinely exceeded 2-3x, never internalized) with a three-part quality-aware exploration workflow:
  - Agent creates a running analysis document (`history-insights-analysis.md`) after scenario extraction, tracking planned scenarios, search results, and per-scenario status (INVESTIGATING → FOUND_LEAD / NO_LEADS → DONE).
  - Phase 4 renamed to "Ground and Write" — agent must review its analysis document before writing output, only reporting findings grounded in documented evidence. APPROVE is explicitly validated as a legitimate outcome.
  - Exploration budget ties to scenarios (~10-15 git commands each) with a soft ~40-call checkpoint rather than an arbitrary hard cap.
- **history-insights-reviewer: model tier upgraded to `inherit`.** Restores Opus-level reasoning when available. Empirical data showed a significant finding quality regression (3-4 findings/run → 1-2) after the Opus → Sonnet switch.
- **history-insights-reviewer: prompt optimization with research-backed patterns.** Applied 6 prompt engineering techniques: consolidated identity (5 scattered paragraphs → 2 focused), removed triple redundancy (RULE 0 protocol + Core Mission + Phases described the same 4 steps), promoted exploration budget from buried 7th constraint to pre-process "Before You Begin" section, added contrastive CORRECT/INCORRECT finding examples in Phase 4, reframed setup instruction from negative to affirmative.

## [1.50.4] - 2026-03-12

### Fixed

- **Large-diff chunked reading guidance:** When a scoped diff exceeds ~20,000 estimated tokens (which will hit the Read tool's 25,000 token limit), the bootstrap output now explicitly warns and instructs agents to read in chunks with interleaved source file reads. Previously, agents attempted a full-file read, hit the token limit error, and recovered with 4–5 extra chunked reads — causing a 53% efficiency drop in the worst case (session `bdd8fb62`, PR #3450 with a 27,515-token diff).

## [1.50.3] - 2026-03-12

### Fixed

- **Patch-line-number confusion in reviewer agents:** Agents reading `scoped-diff.patch` via the Read tool sometimes used the tool's display line numbers (position within the patch file) instead of source file line numbers in `add_issue(line=...)`. This caused valid findings to be classified OUT_OF_SCOPE by the ingestion pipeline. Root cause traced in session `7f5ee0a7` where patterns-reviewer reported `line=227` for a 116-line file (actual source line was 12). Three complementary fixes:
  - **reviewer-protocol.md:** Added CRITICAL rule in STOP CHECK explaining the difference between Read tool display line numbers and source file line numbers, with instructions on deriving correct lines from `@@ ... @@` hunk headers.
  - **bootstrap-reviewer.py:** Added warning header to `scoped-diff.patch` files that agents will see when reading the file. Added line number guidance in the OUTPUT INSTRUCTIONS section.
  - **review_output_simple.py:** Added defensive stderr warning when `line > 5000` to catch the most egregious cases at write time.

## [1.50.2] - 2026-03-12

### Fixed

- **Decision critic hallucination prevention:** After an incident where the decision critic fabricated a branch freshness claim (stated "41 behind" without running any verification command — actual count was 54), added multi-level safeguards:
  - **Evidence-citation requirement** in decision-reviewer agent: every claim in "Claims Verified" and "Claims Failed" now requires an `Evidence:` line citing the specific tool output. Claims without evidence go to a new "Unverified Claims" section that does not count toward the verdict.
  - **"Empty sections are fine" rule** in decision-reviewer agent: explicitly states that "Claims Failed: None" is valid, removing section-filling pressure.
  - **Tool-use mandate** in decision-critic skill Step 4 (Factored Verification): added "Do NOT claim to have verified a factual assertion without running a command" and "Do NOT state specific numbers without citing the tool output" to the epistemic boundary. Added `Tool used:` field to the output format.
  - **Orchestrator spot-checking** in code-review and pr-review commands: on REVISE/ESCALATE verdicts, the orchestrator now verifies 2-3 factual claims from the critic's findings with direct commands before applying revisions. Failed claims are stripped individually, preserving valid adjustments.
  - **Pipeline-wide cite norm** in shared reviewer-protocol: added verification rule #7 requiring agents to cite tool output for specific facts.

## [1.50.1] - 2026-03-11

### Fixed

- **Clean stale files from output directory:** `full-code-review` and `pr-reviewing` now remove and recreate the output directory before each run, preventing stale agent output files and reconciliation artifacts from contaminating new reviews. `code-review` (incremental) leaves the directory as-is.

## [1.50.0] - 2026-03-11

### Changed

- **Config-driven ground truth:** Replaced auto-detection (`shutil.which`, config file scanning, `package.json` parsing) with LLM-extracted tool configuration. Review commands now read the project's CLAUDE.md/AGENTS.md, extract tool commands into a `tool-config.json`, and pass it to `run-ground-truth.py` via `--tool-config`. The script becomes a dumb executor — no guessing, no PATH scanning.
- **Removed Bandit from security pipeline:** Our codebases are PHP/JS/TS; Bandit (Python-only) added no value. Semgrep with `--config=auto` covers Python if needed.
- **New output schema fields:** `tools_skipped`/`tools_unavailable` replaced with `tools_failed`/`tools_not_configured`. Coverage results now included when configured (`jest_coverage`, `phpunit_coverage`).

### Added

- **Tool config loader:** `load_tool_config()` validates a JSON config against 7 known tools (eslint, phpcs, semgrep, jest, jest_coverage, phpunit, phpunit_coverage). Unknown tools, missing `cmd` keys, and empty commands are skipped with warnings.
- **Config-driven tool runner:** `run_configured_tool()` executes command templates with `{output_file}`, `{output_dir}`, and `{files}` placeholder substitution. Files with spaces are shell-quoted.
- **Coverage wired up:** `parse_coverage_results()` was implemented but never called — now invoked when `jest_coverage` or `phpunit_coverage` tools are configured and run.

## [1.49.1] - 2026-03-11

### Fixed

- **Scope status vocabulary mismatch:** The ingest verification pipeline (`ingest-code-review.py`) still referenced `IN_SCOPE`/`OUT_OF_SCOPE` in all its LLM prompts, but the preprocessor outputs `IN_HUNK`/`INTERACTS_WITH_CHANGE`/`FILE_LEVEL`/`OUT_OF_SCOPE`. Updated all three preprocessed-mode step prompts and made the `state_requirement` string mode-aware.
- **`add_issue(line=None)` crashes agents:** Hard `ValueError` on missing line lost ALL agent findings, conflicting with "partial results > no results" philosophy. Now soft-redirects to `add_observation()`, preserving valid findings. Hard enforcement remains for `line=0`/negative.
- **Ground truth proximity match bypassed verification:** Findings near ground truth results (file + line ±3) were set to `pre_classification="verified"`, skipping LLM verification. Match only checks location, not category — coincidental proximity could fast-track false positives. Now stays `needs_verification` with a `ground_truth_corroborated` flag for higher confidence.

### Added

- **Per-agent semantic filter opt-out:** `no_semantic_filter` flag in agent registry causes bootstrap to pass `--no-semantic-filter` to `review-scope.py`. Enabled for `wp-architecture-reviewer` (preserves `@since`, `@deprecated`, `@hook` annotations) and `patterns-reviewer` (preserves pattern documentation in comments).

### Changed

- **Clarified deterministic triage interaction:** Step 3.6 in `full-code-review.md` and `code-review.md` now explicitly states that the dispatch planner's deterministic triage is preliminary — LLM triage in Step 3.6 is the quality gate.

## [1.49.0] - 2026-03-11

### Added

- **Ground truth pre-dispatch evidence phase:** New `run-ground-truth.py` orchestrator collects objective tool findings (ESLint, PHPCS, Semgrep, Jest, PHPUnit) before agent dispatch. File-type routing sends PHP files to PHPCS/PHPUnit, JS/TS to ESLint/Jest, all files to Semgrep. Tools are auto-detected; missing tools are gracefully skipped. Always exits 0 — ground truth is additive, never blocking.
- **Bootstrap ground truth injection:** `bootstrap-reviewer.py` accepts `--ground-truth` flag and injects tool findings (filtered to agent's domain files) into Section 2 of the bootstrap prompt. Each agent sees only findings relevant to its scope.
- **Reconcile ground truth cross-referencing:** `reconcile-reviews.py` matches canonical findings against ground truth using file + line ±3 tolerance. Matched findings are tagged with `ground_truth_match=True` and `ground_truth_tool`.
- **Ingest ground truth corroboration:** Findings matching ground truth are flagged with `ground_truth_corroborated=true` in `ingest-preprocess.py` for higher verification confidence. Summary includes `ground_truth_corroborated` count.
- **Step 2.5 in all review commands:** `full-code-review.md`, `code-review.md`, and `pr-review.md` now include an optional ground truth collection step before agent dispatch.
- **Comprehensive test coverage:** 46 tests for `run-ground-truth.py`, 10 tests for bootstrap integration, 11 tests for reconcile cross-referencing, 5 tests for ingest fast-track (72 new tests total).

## [1.48.0] - 2026-03-11

### Added

- **Deterministic dispatch triage:** Conditional agents now go through a 4-layer deterministic triage in `plan-review-dispatch.py` before LLM-based semantic triage: test-only filter → keyword matching → agent-specific checks (large_pr, file_deletions, net_removal) → conservative default (dispatch). New `SKIPPED_TRIAGE` status for agents filtered out deterministically.
- **Triage configuration in agent registry:** All 7 conditional agents now have `triage_keywords` arrays in `agent-registry.json`. Architecture-reviewer and dead-code-reviewer also have `triage_checks` for diffstat-based decisions.
- **Granular scope classification:** `ingest-preprocess.py` now classifies findings into 4 statuses: `IN_HUNK` (line in changed hunk), `INTERACTS_WITH_CHANGE` (within 5-line proximity), `FILE_LEVEL` (no line number), `OUT_OF_SCOPE`. Summary includes `by_scope_status` breakdown while preserving backward-compatible `in_scope`/`out_of_scope` aggregates.
- **`changed_files` in dispatch plan output:** `plan-review-dispatch.py` now includes the clean file list in its output, enabling downstream tools (reconcile, ingest) to receive it without re-parsing.

### Fixed

- **Recommendation field lost during reconciliation:** `reconcile-reviews.py` now preserves the `recommendation` field in canonical findings — picks the longest non-empty recommendation across all clustered findings.
- **`source_agents` field ignored in ingest:** `ingest-preprocess.py` now uses the structured `source_agents` field from reconciled data before falling back to title-based extraction.
- **Test-gap advisories not firing:** All three review commands (`pr-review`, `full-code-review`, `code-review`) now pass `--changed-files` to the reconcile step, enabling test-gap detection.

## [1.47.0] - 2026-03-11

### Added

- **Decision critic pipeline for `/code-review`:** The incremental review command now includes post-ingest validation update, decision-critic stress-testing, and final presentation steps (Steps 7.5, 8, 9) — matching the `/full-code-review` and `/pr-review` pipelines.

### Fixed

- **full-code-review decision critic reviewed wrong artifact:** The critic was sent `reconciled.md` before ingest had validated findings, so dismissed false positives were still present. Added Step 6.5 to update `reconciled.md` with ingest validation results before the critic sees it.
- **full-code-review verdict extraction was single-source:** Only parsed the agent's return message. Added 3-step fallback chain: return message → findings file → graceful "critic unavailable" degradation.
- **full-code-review verdict-coherence on REVISE/ESCALATE:** REVISE now recalculates the review verdict from updated findings. ESCALATE overrides verdict to COMMENT — prevents contradictory output.

## [1.46.2] - 2026-03-11

### Fixed

- **pr-review early failure recovery:** Replaced flat "skip to Step 6" instructions with a tiered recovery table. Early Phase 1 failures (no URL, wrong repo, stash/checkout) now correctly STOP instead of referencing undefined state variables. Mid-Phase 1 failures produce a partial report. Late failures continue with available output.
- **pr-review verdict coherence after decision critic:** REVISE now recalculates the review verdict from updated findings. ESCALATE overrides verdict to COMMENT — prevents contradictory output like "Verdict: APPROVE" alongside "Decision Critic: ESCALATE".
- **pr-review stash safety:** Added `-u` flag to `git stash push` (includes untracked files, prevents checkout conflicts). Stash pop now matches by saved commit ref instead of blindly popping the top entry. Warns if the stash was already consumed.
- **pr-review decision-reviewer handoff:** Added 3-step verdict extraction priority chain (return message → findings file header → critic unavailable fallback) so a correct findings file survives a drifted return message.

## [1.46.1] - 2026-03-11

### Improved

- **decision-reviewer agent prompt optimization:** Applied 9 research-backed prompt engineering techniques (Identity Establishment, Emotional Stimuli, Affirmative Directives, Emphasis Hierarchy, Numbered Rule Priority, Hint-Based Guidance, Category-Based Generalization, Pre-Work Context Analysis, Error Normalization). Adds adversarial mindset priming ("Think like a skeptic"), RULE 0 for independence from input framing, explicit verdict decision criteria table, expanded Step 2 with phase descriptions and sequential constraint, error normalization for degenerate inputs, and restructured context section with constraint-first framing.

## [1.46.0] - 2026-03-11

### Added

- New `decision-reviewer` agent — runs the 7-step decision-critic workflow in a subagent to preserve main session context. General-purpose: accepts a document path or inline text, produces its own findings document, returns verdict (STAND/REVISE/ESCALATE) + findings path. Pre-loads the decision-critic skill via `skills` frontmatter.

### Changed

- `pr-review` command now dispatches `decision-reviewer` agent instead of inlining the decision-critic skill (Step 5)
- `full-code-review` command now dispatches `decision-reviewer` agent instead of inlining the decision-critic skill (Step 7)
- Dispatch planner (`plan-review-dispatch.py`) now skips `special` class agents (same as `manual`)

### Improved

- **pr-review command prompt optimization:** Applied 5 research-backed prompt engineering techniques to improve agent execution reliability. Phase 2 now has explicit tool invocation with OUTPUT_DIR arg and "do not present yet" constraint (was a single terse sentence). Step 8 override is self-contained with 3 concrete sub-steps instead of cross-referencing full-code-review internals. Added error normalization for phase-level failures — partial results over stopping. Consolidated decision-critic outcome handling into Step 5 (was split across Steps 5 and 6). Clarified pipeline overview to prevent misreading "skill + dispatch" as two separate invocations.

## [1.45.0] - 2026-03-10

### Added

- **decision-critic pipeline integration:** Both `/pr-review` and `/full-code-review` now run the decision-critic skill after ingest to stress-test review conclusions — severity assignments, categorizations, and dismissals. On REVISE or ESCALATE, the report is updated with adjusted findings. Removes the intermediary "Present Reconciled Summary" step; only the final, stress-tested results are shown.

### Fixed

- **switch-to command:** Clarified `git rev-list --left-right --count` column interpretation — column 1 is behind, column 2 is ahead. Added inline comments, a CRITICAL interpretation block, and explicit column references in the summary template.

## [1.44.0] - 2026-03-10

### Added

- **switch-to command:** New `/switch-to` slash command for switching to a branch or PR. Accepts a branch name or GitHub PR URL. Handles dirty working tree (stash/commit/cancel), fork PR remotes, remote sync with pull options (rebase/merge/skip), base branch fetching for PRs, and post-switch context (recent commits, ahead/behind, PR metadata and checks). Includes early exit when already on target branch and error normalization for expected git/gh failures.

## [1.43.12] - 2026-03-06

### Fixed

- **decision-critic skill:** Fixed incorrect script path in SKILL.md — was resolving to plugin-level `scripts/` directory instead of the skill-local `scripts/` directory, causing `FileNotFoundError` on first invocation.

## [1.43.11] - 2026-03-06

### Fixed

- **a11y-reviewer triage criteria:** Added non-visual a11y features to triage criteria — `speak()` calls, `aria-live` regions, and focus management in hooks/utilities. Previously, the adaptive triage pass (Step 3.6) could skip the a11y reviewer on PRs that add screen reader announcements without new interactive UI components (e.g., `announceErrorMessage` wiring via `@wordpress/a11y`). Updated agent description and review process Step 1 to reinforce focus on non-visual a11y.

## [1.43.10] - 2026-03-04

### Fixed

- **review pipeline agent-signals contract:** `plan-review-dispatch.py` now emits `agent_signals_text`, a canonical newline-joined text block for downstream reconciliation steps. The full/incremental review commands, the `review-reconciliator` agent, and the `pr-reviewing` skill now state explicitly that this block must be passed as one quoted `--agent-signals` argument and pasted verbatim into the reconciliator prompt.
- **Regression coverage:** Added tests for the new `agent_signals_text` planner field, command docs that preserve the quoting contract, and `reconcile-reviews.py` CLI behavior for properly quoted vs split `--agent-signals` input.

## [1.43.9] - 2026-03-03

### Changed

- **copy-as command:** Added compressed Human-Facing Messages guidance to the PR review comments section — use the author's name, assume good faith, acknowledge effort, frame suggestions collaboratively, and write in active voice.

## [1.43.8] - 2026-03-03

### Fixed

- **review-reconciliator agent:** Agent now runs `reconcile-reviews.py` itself in Step 0 rather than assuming the caller already ran it. The pr-reviewing pipeline dispatches the reconciliator directly after agents finish without running the script first, causing the agent to fall back to manual reconciliation with wrong format. Step 0 uses the same semver-aware cache glob to locate the script — `CLAUDE_PLUGIN_ROOT` is a parse-time substitution not available as a runtime env var.

## [1.43.7] - 2026-03-03

### Fixed

- **review-reconciliator agent:** Replace broken `repo_root/lib/` path resolution for `review_output_simple.py` with semver-aware glob over the plugin cache. The previous code used the reviewed project's git root, which only has a `lib/` in the plugin dev repo — in all other projects it failed silently, causing the agent to fall back to an unordered `find` that could pick any cached version (in the incident: 1.34.1 instead of 1.43.5). The new approach always selects the latest installed version regardless of which project is being reviewed.

## [1.43.6] - 2026-03-03

### Fixed

- **ingest-preprocess.py:** Handle `reconciled-structured.json` in clusters-only format (old `reconcile-reviews.py` without the flat `issues` key added in step 8.5). When the structured file has `clusters` but no `issues`, the preprocessor now extracts canonicals from the clusters instead of silently returning zero findings. Added regression test `test_clusters_format_without_issues_key`.

## [1.43.5] - 2026-03-02

### Added

- **Figma helper scripts:** `figma-parse-nodes.py` (Phase 0 metadata parsing) and `figma-extract-specs.py` (Phase 1 design context extraction) referenced by the using-figma skill
- **Design spec template:** `references/design-spec-template.md` for the using-figma skill

### Removed

- **execute-plan command:** Unused, removed along with stale `quality-reviewer` reference
- **fix-github-issue command:** Unused
- **CURRENT-STATUS.md:** Stale since v1.10.0, actively misleading at v1.43.4

## [1.43.4] - 2026-03-02

### Added

- **analyzing-cc-sessions skill:** Ship skill for parsing CC session JSONL transcripts, analyzing subagent behavior, and extracting metrics — was registered in marketplace.json but never committed

### Fixed

- **Skills (analyzing-cc-sessions, decision-critic, using-figma):** Use skill base directory derivation for script path resolution instead of bare relative paths — scripts now resolve correctly when installed from marketplace cache
- **ingest-code-review command:** Use `${CLAUDE_PLUGIN_ROOT}` for all script paths instead of bare `scripts/` references

### Changed

- **Skills (analyzing-cc-sessions, woocommerce-browser-interaction, browser-interaction):** Remove local setup references and project-specific paths — keep examples generic for any user

## [1.43.3] - 2026-03-02

### Fixed

- All reviewer agents + shared protocol: validate plugin root cache path exists before use, and pick latest (not oldest) cached version in find fallback — prevents agents from running scripts from old cached plugin versions after upgrades

## [1.43.2] - 2026-03-02

### Changed

- **patterns-reviewer agent:** Add parallel tool call guidance — instructs the agent to issue independent `git grep` and `git show` calls simultaneously instead of sequentially. Addresses the #1 inefficiency (43.7% of all tool calls are sequential git grep, with zero parallelism across 302 observed turns). Expected ~40% wall-clock reduction on the search phase.

## [1.43.1] - 2026-03-02

### Added

- **test_extract_session_metrics.py:** 16 unit tests for `identify_agent_type()` — covers bootstrap detection (Strategy 1), reconciliator fingerprinting (Strategy 1.5), hardened keyword inference (Strategy 2), and edge cases (empty files, list content format, mixed signals)

### Fixed

- **extract-session-metrics.py:** Reconciliator agent sessions no longer misidentified as wp-architecture-reviewer. Added fingerprint detection (Strategy 1.5) and agent signal line stripping in keyword inference (Strategy 2) to prevent false matches from orchestrator context text like "wp-architecture-reviewer: STATUS=COMPLETED"

### Changed

- **history-insights-reviewer:** Cap diff output at 500 lines (`--max-lines 500`) and file history at 5 commits per file (down from 15) to reduce Sonnet context-processing time after the Opus→Sonnet demotion caused a 20% speed regression

## [1.43.0] - 2026-03-01

### Added

- **figma-copy-sync skill:** Self-contained skill for synchronizing text copy between Figma designs and implemented code. 4-phase workflow: Figma text extraction → surface matching (browser snapshots via browser-interaction skill) → copy comparison with i18n detection → approval-gated application. Handles multiple component states, auto-detects translation patterns (WordPress i18n, react-intl, i18next), and produces structured sync reports.

### Changed

- **figma-copy-sync skill:** Optimize with prompt engineering patterns — identity establishment, tiered Iron Rules (Safety-Critical RULE 0-2 vs Operational RULE 3-5), structured HITL approval gates with impact summaries, error normalization for expected uncertainty, compact workflow table replacing unrenderable dot graph, and affirmative directive framing

## [1.42.2] - 2026-03-01

### Added

- **test_review_output.py:** 40 unit tests for ReviewOutputBuilder — initialization, issue validation, verdict calculation, serialization (dict, JSON, markdown), and file output
- **test_review_api_contract.py:** 12 cross-component contract tests verifying producer→reconcile→ingest pipeline — catches interface mismatches between layers

### Fixed

- **reconcile-reviews.py:** Add `issues` key to reconcile output that flattens `clusters[].canonical` into a flat list. Previously `ingest-preprocess.py` read `reconciled.get("issues", [])` but reconcile only wrote `clusters`, silently dropping all findings. The `clusters` key is preserved for backward compatibility.

## [1.42.1] - 2026-03-01

### Changed

- **Bootstrap integration tests:** Run against temporary mock git repos (from `.diff` fixtures) instead of the real repository, eliminating state-dependent test results
- **conftest.py:** New shared test helper with `setup_temp_git_repo()` extracted from `test_domain_routing.py` — creates isolated git repos from diff fixtures for any test module
- **TESTING.md:** Documented mock repo pattern as design principle #8

## [1.42.0] - 2026-03-01

### Added

- **agent-registry.json:** Canonical JSON registry for all 15 review agents — single source of truth replacing hardcoded AGENT_CONFIG in bootstrap-reviewer.py. Fields: domain, secondary_domains, protocols, scope_flags, dispatch_class, triage_criteria, focus, model_tier
- **plan-review-dispatch.py:** Deterministic dispatch planner that reads agent registry and changed files to decide which agents to dispatch. Replaces duplicated triage/dispatch logic in command files with `--mode full|incremental|pr`
- **reconcile-reviews.py:** Deterministic reconciliation engine with Jaccard-based dedup clustering, severity resolution, and structured output. Pre-processes findings before the LLM reconciliator agent
- **ingest-preprocess.py:** Deterministic scope checker and pre-classifier for ingest pipeline. Reduces LLM ingest steps from 6 to 3 by handling file/hunk scope checks and stable ID assignment mechanically
- **reliability-reviewer agent:** New conditional agent for operational resilience review — logging, error handling, rollback safety, feature flags, circuit breakers, and failure-mode handling (sonnet tier)
- **config-ops domain:** New scope domain covering CI/CD configs, Docker, Terraform, Helm, Makefiles, and infrastructure files. Security-reviewer and architecture-reviewer gain secondary domain coverage with dedicated checklists
- **reliability domain:** New scope domain for production code operational resilience review
- **Quality metrics extraction:** `--quality-metrics` mode in analyze-reviewer-sessions.py for finding counts, survival rates, and cross-agent overlap detection
- **Test adequacy advisory:** Informational test-gap detection in reconcile-reviews.py — warns when production code changes without corresponding test modifications
- **180+ new tests:** test_agent_registry (27), test_dispatch_planner (41), test_reconcile_reviews (55), test_ingest_preprocess (30), test_quality_metrics (27), domain routing extensions

### Changed

- **bootstrap-reviewer.py:** Loads agent config from agent-registry.json instead of hardcoded dict; secondary_domains support for multi-domain scope discovery
- **review-reconciliator.md:** Simplified from mechanical dedup+narrative to narrative-only — reads pre-processed reconciled-structured.json, focuses on synthesis and executive summary
- **ingest-code-review.py:** Supports 3-step preprocessed mode (--total-steps 3) alongside legacy 6-step mode for backwards compatibility
- **full-code-review.md:** Dispatch via plan-review-dispatch.py; reconciliation split into deterministic preprocessing + LLM narrative
- **code-review.md:** Same dispatch and reconciliation refactoring as full-code-review.md
- **pr-review.md:** Updated agent count to /14; references dispatch planner directly

### Fixed

- **pr-review.md:** Corrected hardcoded agent count from /12 to /14
- **README.md:** Fixed 5 stale model tier entries; corrected tier counts (inherit 3, sonnet 12, haiku 4)

## [1.41.3] - 2026-03-01

### Changed

- **analyzing-cc-sessions:** Apply prompt engineering optimizations for behavioral clarity:
  - **"Before You Start" goal table** (Pre-Work Context Analysis) — maps analysis goals to concrete starting points, eliminating aimless exploration
  - **"Selecting Task-Relevant Agents"** (Affirmative Directives) — reframes "skip compaction agents" into "process only task agents" with reusable `is_task_agent()` helper
  - **Scripts table by use case** (Category-Based Generalization) — reorganizes from flat "Script | Purpose" to "When you need to... | Use" with fallback row for custom analysis
  - **Parsing error preamble** (Error Normalization) — sets defensive expectations for malformed data upfront, preventing parser crashes
  - **Efficiency analysis guidance** (Hint-Based Guidance) — directs focus to phase transition boundaries where waste clusters
- **review-reconciliator:** Change model from `sonnet` to `inherit` — the reconciliator performs judgment-heavy synthesis (conflict resolution, deduplication, 10:1 compression) so it should use the parent session's model rather than being pinned to Sonnet

## [1.41.2] - 2026-03-01

### Changed

- **using-figma:** Apply prompt engineering optimizations for high-impact behavioral improvements:
  - **Red Flags → Pre-Action Checkpoints** (STOP Escalation + Affirmative Directives) — converts passive "if you catch yourself" observation into active pre-action verification table with trigger/test/alternative columns
  - **Iron Rules → Category-Based Generalization** — groups 9 rules into 3 principle categories (data acquisition first, structural understanding first, tool usage discipline) enabling analogical reasoning for unlisted scenarios
  - **Asset Handling → Affirmative Directives** — reframes 3 prohibitions ("Do NOT") into 3 affirmative directives specifying correct behavior directly
  - **New "Handling Figma MCP Failures" section** (Error Normalization) — adds recovery table for truncated responses, empty results, connection errors, and unexpected formats to prevent apology spirals

## [1.41.1] - 2026-03-01

### Changed

- **patterns-reviewer:** Add tool discipline instruction — Bash only for git commands, use Read/Grep/Glob for everything else (addresses inefficiency #5 from deep analysis: 23 `cat`/`head`/`find` calls via Bash in worst dispatch)
- **patterns-reviewer:** Add search scoping guidance — always include extension filters and directory paths in `git grep`, never search unscoped common words (addresses inefficiency #6 remainder: broad searches like `git grep "error"` wasting 3-4 refinement calls)

## [1.41.0] - 2026-03-01

### Added

- **New `analyzing-cc-sessions` skill** — Reference guide for navigating and analyzing Claude Code raw session logs (JSONL transcripts). Codifies structural knowledge from 3 deep analysis sessions (figma-workflow, dead-code-reviewer efficiency, patterns-reviewer deep analysis). Covers:
  - Session data locations and project directory resolution
  - Main session JSONL structure (5 entry types, content block formats, tool_use/tool_result pairing)
  - Subagent JSONL structure (simpler format, dispatch prompt identification, agent type inference)
  - Tool results persistence (>30KB threshold, separate files)
  - Correlating main session dispatches with subagent execution via agentId
  - Parsing recipes (extract tool calls, categorize bash commands, sum token usage)
  - Links to existing analysis scripts (analyze-reviewer-sessions.py, extract-session-metrics.py, analyze-subagents.py)
  - Common waste patterns ranked by impact with detection guidance
  - Gotchas table for structural traps (content type variance, compaction agents, model field location)

## [1.40.0] - 2026-03-01

### Added

- **New `using-figma` skill** — Structured workflow for translating Figma designs into production code with high fidelity. Based on deep analysis of 4 real CIAB-admin sessions that identified 8 systematic anti-patterns causing design mismatches. Key features:
  - **5-phase workflow** (Survey → Specification → Component Tree → Implementation → Validation) that mandates building a structured mental model before coding
  - **Design Specification Documents** — Intermediary data state between raw Figma responses and code, persisting across context compressions
  - **9 iron rules** derived from observed failures: always call `get_variable_defs`, never use screenshots as sole implementation source, always use project tokens, never batch Figma with other tool providers, etc.
  - **Project-agnostic core** with `.claude/figma-config.json` configuration for project-specific token mappings (Figma → project design system)
  - **Cross-session caching** for token definitions, token mappings, and node hierarchies
  - **Bundled Python scripts** for parsing large Figma responses: `figma-parse-nodes.py` (metadata hierarchy) and `figma-extract-specs.py` (design context specifications)
  - **Design spec template** for consistent specification documents

## [1.39.1] - 2026-02-28

### Fixed

- **ReviewOutputBuilder API hallucination** — Bootstrap Section 3 now includes a complete usage example with all core methods (add_issue, add_positive, set_files_reviewed, set_confidence, save). Previously only showed the constructor, causing all 14 agents to hallucinate wrong method names on first write attempt (~3.2 wasted calls/agent).
- **Bootstrap output size cascade** — Scope output exceeding 15KB is now written to scoped-diff.patch and truncated inline with read instructions. Prevents the persistence cascade that wasted 2-3 calls per large PR session.
- **Post-write verification reads** — Bootstrap now instructs agents to trust save()'s return value, eliminating unnecessary Read calls per agent per session.
- **Dead-code Step 0 unconditional PHP check** — Bootstrap injects DYNAMIC_DISPATCH_RISK computed from file extensions; dead-code-reviewer skips the PHP hook grep when no PHP files are in scope (~1 wasted call in 50% of sessions).
- **Output filename mismatch** — ReviewOutputBuilder.save() now writes `{reviewer}-review.json/.md` matching the convention documented in bootstrap and shared protocol. Previously wrote `{reviewer}.json/.md`, causing filename mismatches.

## [1.39.0] - 2026-02-28

### Changed

- **history-insights-reviewer efficiency overhaul** — Based on deep analysis of 3 session transcripts showing 60-70 git commands per run. Key changes:
  - Bootstrap now provides merge-base-correct diffs (eliminates 8-11 redundant `git diff` commands per session)
  - All keyword/pickaxe searches use `--first-parent --since="12 months ago"` (10-100x faster on repos with many branches)
  - Pickaxe split into two phases: find SHAs first (no `-p`), then selective `git show` (major token reduction)
  - Fixed `-S`/`-G` confusion: `-S` for literal strings, `-G` for regex (eliminates ~half of 19% pickaxe failure rate)
  - Added `git blame` as supplementary discovery tool
  - Added explicit parallel branch detection as Phase 1.5 (elevates agent's most unique capability)
  - Added soft ~35 command budget and patterns-reviewer dedup
  - Pre-computed file history in bootstrap (last 15 commits per changed file)
  - Expected savings: ~35% token reduction (5.3M → ~3.5M avg), ~35% runtime reduction (5m35s → ~3m30s)

## [1.38.1] - 2026-02-28

### Changed

- **Model demotion: 4 agents from Opus (inherit) to Sonnet** — architecture-reviewer, wp-architecture-reviewer, patterns-reviewer, and history-insights-reviewer pinned to Sonnet instead of inheriting the parent session model (typically Opus). Cost-normalized analysis showed these agents consumed a disproportionate share of the token budget at Opus pricing (5x Haiku). pr-reviewer and a11y-reviewer stay on Opus (inherit). Expected savings: ~22% of cost-normalized budget.

## [1.38.0] - 2026-02-28

### Added

- **Adaptive agent dispatch (Step 3.6)** — LLM triage step between file-type preflight and agent dispatch. Six conditional agents (security, dead-code, architecture, wp-architecture, performance, a11y) are now evaluated against per-agent dispatch criteria using the diffstat and commit messages. Agents that don't match criteria are skipped with `STATUS=SKIPPED_TRIAGE` signal, reducing wasted token budget by ~20-30% without losing confirmed findings. Triage defaults to DISPATCH when in doubt to maintain safety.

### Fixed

- **Reconciliator missing dead-code and go-tests agents** — The reconciliator's `agent_names` list was missing `dead-code` and `go-tests`, causing their findings to be silently dropped from reconciled summaries.
- **pr-review.md stale agent count** — Updated Step 8 override from hard-coded "12 agents" to "all eligible agents with triage."

## [1.37.1] - 2026-02-28

### Changed

- **architecture-reviewer — Narrow scope to eliminate patterns-reviewer overlap** — Added explicit exclusions for code duplication, structural inconsistency, and consolidation opportunities (all handled by patterns-reviewer). Added -20 confidence reducer for findings that primarily recommend "extract shared code" or "align with existing implementation." Updated collaboration section to clarify the boundary. Based on overlap analysis showing 8 co-reported findings (all duplication/consistency) and architecture-reviewer's 50% unique contribution rate — the worst in the pipeline.

## [1.37.0] - 2026-02-28

### Added

- **reviewer-protocol — Three precision guardrails from ingest validation analysis (313 findings, 29 sessions)** — (1) "Bug or Preference?" self-check gate for LOW/MEDIUM findings to reduce STYLE/PREFERENCE noise (15.7% of output); (2) Factual-claim verification mandate requiring Read tool confirmation before reporting what code does/doesn't do (addresses 47% of false positives); (3) STOP escalation pattern before every `add_issue()` call requiring file+line scope verification (addresses 6.4% OUT OF SCOPE rate). All three changes are additive to the existing 4-point verification checklist.
- **wp-architecture-reviewer — Anti-FP checks for framework conventions** — Three rules addressing the agent's 13% FP rate: verify against type definitions before flagging APIs, developer-only strings don't need i18n, and clean removals are not dead code.
- **architecture-reviewer — WordPress context dampener** — Conditional -10 confidence for abstract SOLID opinions in WordPress code without concrete defects, addressing the precision drop from 80% (Go) to 53.6% (WordPress).
- **history-insights-reviewer — Relevance gate** — Insights must connect to code being changed in the PR; "good to know" findings from unrelated areas get INFO severity or are dropped.
- **ingest-code-review — Source inference rule** — Step 2 now requires inferring agent source from filename when no explicit field is present, eliminating the 25.6% UNKNOWN attribution gap.

## [1.36.0] - 2026-02-28

### Added

- **patterns-reviewer agent — Pattern relevance improvements** — Four changes to reduce false positives and improve finding quality: (1) RULE 1: 3+ independent usage gate — patterns need 3+ instances to be reported as "established," with exceptions for authoritative locations and small codebase adjustment; (2) Proximity confidence modifiers — same-module patterns get +15 confidence, distant patterns get -15, using the existing confidence system instead of a separate score; (3) Staleness check step — new Step 5 in the discovery process uses `git log -S` to detect actively-adopted vs declining patterns, with confidence reduction for dying patterns; (4) Contextual verdict qualifiers — verdicts now require usage counts, area context, and freshness indicators in descriptions.

## [1.35.3] - 2026-02-28

### Fixed

- **a11y-reviewer agent — Wired into all dispatch and documentation locations** — Added a11y-reviewer as agent #13 in both `/full-code-review` and `/code-review` dispatch tables, added `a11y` to review-reconciliator's agent_names list and file tree, added to pirategoat-tools and root README agent tables (17→18 agents), added to TestDeriveReviewerName parametrize list, updated test_commands dispatch count (12→13), and removed stale hardcoded agent counts from TESTING.md.

## [1.35.2] - 2026-02-28

### Changed

- **`a11y-reviewer` agent — Prompt engineering optimization** — Applied 10 research-backed patterns: Affirmative Directives (setup and confidence scoring), STOP Escalation (AP-01/AP-02 metacognitive checkpoints after RULE 0), Contrastive Examples (WRONG/RIGHT code for AP-01, AP-02, AP-07), UX-Justified Defaults (keyboard traps, tabindex, aria-hidden impact), Error Normalization ("Insufficient Context Is Normal" section), Conditional Sections (WordPress-only markers on P2 items and AP-14/AP-16), Completeness Checkpoint (keyboard protocol with named steps: Reach/Activate/Escape/Understand/Return), Numbered Rule Priority (assigned AP-17/18/19 to unnumbered entries, sorted table by severity), Hint-Based Guidance (attention primers before each sweep), Affirmative confidence scoring.

## [1.35.1] - 2026-02-28

### Changed

- **`accessible-frontend-dev` skill — Prompt engineering optimization** — Applied 10 research-backed prompt engineering patterns: Identity Establishment (role priming), Priority System legend (P0/P1 explained), STOP Escalation for `<div onClick>` anti-pattern, Scope Limitation (explicit boundaries), UX-Justified Defaults (disabled state, focus indicators, high contrast rationale), Error Normalization (pragmatic a11y debt guidance), Contrastive Examples (CORRECT/INCORRECT code for top 2 focus bugs), Affirmative Directives (converted 4 negative rules to affirmative framing), Confidence Building (trust decision tree outputs), and Conditional Sections (WordPress/Gutenberg skip instruction).

## [1.35.0] - 2026-02-27

### Added

- **`accessible-frontend-dev` skill — Decorative Content Rendering rules** — Decision tree for choosing between pseudo-elements, inline SVG, `mask-image`, and text nodes. Covers screen reader behavior of `::before`/`::after` (announced per W3C AccName spec), text selection/clipboard exclusion, translation tool immunity, and DOM-walker invisibility. Rule: never put Unicode symbols in CSS `content` for icons.
- **`accessible-frontend-dev` skill — CSS-First Presentational Concerns** — Prefer CSS mechanisms over JS runtime checks: `:dir(rtl)` over `isRTL()`, logical properties for layout, media queries for motion/color-scheme/forced-colors. Includes "workaround smell" heuristic for recognizing when an approach fights the platform.
- **`accessible-frontend-dev` skill — WordPress Twemoji platform hazard** — Documents how Twemoji's `MutationObserver`-based DOM walker replaces Unicode characters in text nodes (including arrows, symbols, not just emoji faces). Correct approach: render decorative symbols via `::after` or SVG to avoid interference entirely.
- **`accessible-frontend-dev` skill — Motion & Animation rules** — `prefers-reduced-motion` media query requirement, reduced-motion alternatives that preserve meaning, auto-playing content pause/stop control (WCAG 2.2.2).
- **`accessible-frontend-dev` skill — High Contrast & Forced Colors rules** — Windows High Contrast Mode testing guidance, `currentColor` for SVG fills, `outline` over `box-shadow` for focus indicators, border/outline state indicators.
- **`accessible-frontend-dev` skill — Keyboard Shortcuts Declaration** — `aria-keyshortcuts` attribute guidance for components with non-standard keyboard shortcuts.
- **`accessible-frontend-dev` skill — Skip Navigation** — Skip-to-content link requirement for SPA views with repeated navigation, WordPress target selectors.
- **`component-patterns.md` — External Link / Opens in New Tab pattern** — Security (`rel="noreferrer noopener"`), accessible name patterns, icon rendering (prefer `mask-image` or SVG, avoid Unicode text nodes), RTL via `:dir(rtl)::after`, hash-link edge case.
- **`component-patterns.md` — Treeview pattern** — Full APG Tree View pattern with `role="tree"`/`role="treeitem"`/`role="group"`, arrow key navigation, expand/collapse, and structure example.
- **`component-patterns.md` — Drag-and-Drop pattern** — Accessible drag-and-drop with keyboard alternatives (action mode, move buttons), live announcements for grab/move/drop/cancel, and implementation skeleton.
- **`a11y-reviewer` agent — P1 checklist items** — `prefers-reduced-motion`, forced-colors focus indicators, `aria-disabled` vs HTML `disabled`, `aria-keyshortcuts` presence, Unicode symbols in CSS `content`, decorative text node clipboard leakage, JS RTL checks for presentational concerns.
- **`a11y-reviewer` agent — P2 checklist items** — Skip navigation, drag-and-drop keyboard alternative, treeview arrow key navigation, Twemoji-vulnerable decorative symbols in WordPress context.
- **`a11y-reviewer` agent — Anti-pattern heuristics AP-10 through AP-16** — Motion without reduced-motion fallback, focus indicator lost in high contrast, inaccessible drag-and-drop, Unicode symbols in pseudo-element content, `wp-exclude-emoji` workaround smell, JS RTL for presentational styling.

## [1.34.0] - 2026-02-27

### Added

- **`accessible-frontend-dev` skill** — Comprehensive accessibility skill for writing WCAG 2.2 AA-compliant frontend code. Includes decision trees (ARIA vs HTML, focus strategy, announcements, disabled state), universal rules distilled from 450+ Gutenberg a11y bug fixes, component pattern quick reference, and Gutenberg infrastructure reference (`useConstrainedTabbing`, `useFocusReturn`, `speak()`, etc.). Heavy reference file covers 13 APG component patterns with full ARIA, keyboard, and focus specifications.
- **`a11y-reviewer` agent** — Accessibility-focused code review agent (the 14th review agent). Runs P0/P1/P2 checklists against changed files, applies 13 anti-pattern detection heuristics from real Gutenberg bugs, confidence-scores each finding, and follows the "keyboard-only thought experiment" methodology. Integrates with the existing review orchestration system via bootstrap script and `a11y` domain in review-scope.

## [1.33.4] - 2026-02-27

### Changed

- **`/copy-as` strips review process artifacts from PR comments** — PR review comments now omit internal review methodology (agent counts, tool names), finding IDs (F1, F3+F4), and label-like prefixes (Verdict:, Approach:, Suggestion:). Uses descriptive headings and natural prose instead of machine-readable references.

## [1.33.3] - 2026-02-27

### Changed

- **`/copy-as` dual-audience PR content** — When copying for PR descriptions or review comments, content is now structured with a human-scannable recap (3-5 bullets, ~100 words) followed by detailed AI-friendly context below a separator. Includes contrastive before/after example showing wall-of-text vs. recap+details structure. Explicitly reconciles this with the "default to human-readable" rule via exception clause. PR review comments specifically strip contextually obvious details (PR number, verdict, restating what the PR does, filler praise) and use a direct peer-to-peer voice.

## [1.33.2] - 2026-02-27

### Changed

- **`gemini-reviewer` forces Gemini 2.5 Pro** — All CLI invocations now pass `-m gemini-2.5-pro` instead of relying on auto-routing, ensuring consistent model selection for code review quality.

## [1.33.1] - 2026-02-26

### Changed

- **`/ingest-code-review` prompt strengthened** — Added pre-work context sentence establishing the 6-call loop before step 1 runs; added explicit STOP escalation when the script exits with an error; replaced the passive loop-continuation paragraph with a labeled affirmative-directive numbered list.

## [1.33.0] - 2026-02-26

### Changed

- **`/ingest-code-review` uses step-by-step prompt injection** — Replaced single-pass instructions with a 6-step script-driven workflow (`scripts/ingest-code-review.py`). Claude now enforces factored verification in steps 4-5: it generates falsification questions per finding, reads the actual code with the Read tool, then answers questions independently before judging a finding. Grounded in Chain-of-Verification (Dhuliawala et al., 2023).

## [1.32.4] - 2026-02-26

### Changed

- **`/code-review` auto-ingests findings** — Added Step 7 that automatically invokes `pirategoat-tools:ingest-code-review` after the reconciliator finishes, matching the behaviour added to `/full-code-review` in 1.32.3.

## [1.32.3] - 2026-02-26

### Changed

- **`/full-code-review` auto-ingests findings** — Added Step 7 that automatically invokes `pirategoat-tools:ingest-code-review` after the reconciliator finishes. Previously users had to manually run `/ingest-code-review` as a follow-up. The ingest step now runs back-to-back with Step 6 without waiting for user input.

## [1.32.2] - 2026-02-26

### Fixed

- **`/copy-as` content quality** — Two Step 2 refinements: (1) default to human-readable form — extract prose/structured output rather than raw data (JSON, logs, tool output) unless explicitly requested; (2) no hard line breaks — output each paragraph/list item/heading as a single continuous line so paste targets reflow correctly.

## [1.32.1] - 2026-02-25

### Added

- **`/copy-as` P2/Gutenberg HTML format** — New `p2` target for the `/copy-as` command. Converts markdown to semantic HTML (15 element rules) and uses a Swift NSPasteboard script to set both `public.html` and `public.utf8-plain-text` on the clipboard. Gutenberg auto-converts pasted HTML to blocks, so users can Cmd+V directly into P2 posts and comments without needing Cmd+Shift+V for plain text paste.

## [1.32.0] - 2026-02-25

### Added

- **`/copy-as` command** — Copies content to clipboard formatted for the target destination. Defaults to standard markdown (pass-through); when `slack` is specified, converts to Slack's mrkdwn syntax via a 14-rule conversion checklist — bold/italic syntax differences (`**` → `*`, `*` → `_`), link inversion (`[text](url)` → `<url|text>`), heading removal (→ bold text), table conversion (→ preformatted code blocks), strikethrough (`~~` → `~`), code block language identifier stripping, HTML tag removal, and special character escaping with explicit code span protection. Prompt-engineered with Identity Establishment, Scope Limitation, Completeness Checkpoint Tags, Emphasis Hierarchy (RULE 0), Contrastive Examples, and Confidence Building patterns.

## [1.31.1] - 2026-02-24

### Changed

- **`/pr-review` composition refinements** — Rewrote command to compose existing skill and commands instead of duplicating content. PR context gathering delegates to the pr-reviewing skill, agent dispatch uses `/full-code-review` (all 12 agents regardless of PR size), and validation uses `/ingest-code-review`. Applied prompt engineering patterns: RULE 0 emphasis for autonomy constraint, pipeline overview, compressed redundant step enumeration.

## [1.31.0] - 2026-02-24

### Added

- **`/pr-review` command** — End-to-end PR review pipeline that runs without interruption. Gathers full PR context (details, issue, review state), dispatches all 12 review agents in parallel, reconciles findings, validates each finding against actual code (filtering false positives and out-of-scope items), and saves a comprehensive review document to `/tmp/pr-review-<PR_NUMBER>/review-report.md`. Combines the pr-reviewing skill, full-code-review, and ingest-code-review workflows into a single non-interactive command.

### Changed

- **Tiered model assignments for reviewer agents** — Assigned models based on reasoning complexity to reduce cost and latency. Orchestration and pattern-matching agents (gemini-reviewer, codex-reviewer, technical-writer, go-tests-reviewer) downgraded to haiku. Checklist-driven agents (security, performance, dead-code, tests-mutation, php/js/e2e-tests reviewers) set to sonnet. Deep-reasoning agents (pr-reviewer, architecture, wp-architecture, patterns, history-insights) remain on inherit (Opus).

## [1.30.0] - 2026-02-23

### Fixed

- **review-scope.py merge-base always active** — Merge-base range rebasing now happens unconditionally when a merge-base exists, not only when the branch is >10 commits behind. Previously, any divergence from the base branch (even 1 commit) could cause review agents to flag unrelated files from trunk. The `STALE_BRANCH_THRESHOLD` now only controls the advisory warning message. `--no-merge-base` remains as an escape hatch. Text output now shows `RANGE_REBASED` even for non-stale branches.

### Added

- **pr-reviewing skill merge-base anchoring** — Step 1 now computes `MERGE_BASE` after fetching branches. Steps 7 and 8 use `${MERGE_BASE}..HEAD` for all diffs and agent dispatch (replacing `<baseRefName>...<headRefName>`). Agent context template includes an authoritative changed files list with a constraint that agents must only review listed files (defense-in-depth against wrong ranges).
- **Expanded noise filters** — `package-lock.json`, `pnpm-lock.yaml`, `npm-shrinkwrap.json`, `go.sum`, `.po` translation files, `.yarn/` directory, `__pycache__/` directory, coverage directories (`coverage/`, `.nyc_output/`, `htmlcov/`), `.cache/` directory, `tsconfig.tsbuildinfo`, `.eslintcache`, and `.stylelintcache`.
- **test_review_scope.py** — 57 new tests: pure function unit tests (`rebase_range_to_merge_base`, `detect_base_ref`, `count_diff_lines`, `filter_noise`, `filter_domain`) and integration tests for the merge-base gating fix (non-stale rebase, `--no-merge-base` escape hatch, stale warning decoupling, text/JSON output format, range rewriting).

### Changed

- **test_domain_routing.py** — Updated `test_non_stale_branch_no_range_rebase` → `test_non_stale_branch_still_rebased` to match new unconditional merge-base behavior.

## [1.29.1] - 2026-02-23

### Changed

- **browser-interaction skill restructured for clarity** — Consolidated 3 entry-point sections (Prerequisites, Quick Start, MCP Detection) into single Prerequisites. Merged RULE 0 with Common Operations so workflow loop and code examples appear together. Streamlined RULE 1 with action-first decision table (removed dot graph, demoted non-actionable explanation). Moved Chrome DevTools Profile Locations to Reference section at bottom. Removed vague "When to Use" section (covered by frontmatter). 167 → 118 lines, same content.

## [1.29.0] - 2026-02-23

### Added

- **browser-interaction token efficiency guidance** — New RULE 1 with decision flow for choosing snapshots vs screenshots, token cost formula (`width × height / 750`), comparison table of snapshot vs screenshot trade-offs, and warning about heavy-navigation pages where snapshots can exceed screenshot costs. Updated code examples to prefer element-targeted screenshots (`uid` parameter) over full-page captures.
- **token efficiency analysis doc** — Research analysis documenting the investigation into image tokenization behavior, grayscale/format experiments, and findings that led to the skill update.

## [1.28.1] - 2026-02-22

### Changed

- **software-architecture skill library** — Compressed 16 reference files from 21,772 to 2,886 lines (86.7% reduction) for AI agent token efficiency. Removed pattern history, UML diagrams, metaphors, quotes, generic OOP examples (Java/C#/Python), "further reading" sections, and definition paragraphs that Claude already knows from training. Kept all WordPress/PHP and JS/TS/React code examples, When to Use / When NOT to Use decision criteria, Common Mistakes with WRONG/RIGHT pairs, and pattern relationship maps. Added JS/TS examples to Composite (React component tree), Decorator (HOC), Facade (module re-export), and Factory (React component factory). Fixed patterns/README.md to only reference files that exist (removed 18 aspirational entries for unwritten pattern files). All SKILL.md routing table headings verified intact.

## [1.28.0] - 2026-02-20

### Added

- **go-tests-reviewer agent** — New specialized reviewer for Go test quality: standard `testing` package patterns, table-driven tests, subtests, test helpers (`t.Helper()`), `httptest`, interface-based mocking, benchmarks, fuzz testing, and bubbletea TUI testing. Dispatched as the 12th reviewer agent (13 total with dead-code) in `/code-review` and `/full-code-review`.
- **go-testing-patterns skill** — User-facing skill with Go assertion quick reference, red flags table, table-driven test template, and interface-based mocking guidance.
- **go-testing-patterns.md reference** — ~420-line deep reference covering the full Go testing ecosystem: table-driven tests, subtests, `TestMain`, cleanup/isolation (`t.TempDir`, `t.Setenv`, `t.Cleanup`), parallel tests, `httptest`, interface-based mocking, benchmarks, fuzz testing, bubbletea TUI testing, `testdata/` conventions, race detection, and build tags.
- **go-tests domain** — `review-scope.py` now recognizes `_test.go` files as the `go-tests` domain for preflight filtering and scope discovery. Also added `_test.go` to the `dead-code` domain exclude pattern.
- **Go test fixtures** — `go-test-only.diff` and `go-source.diff` fixtures for domain routing tests.
- **Test updates** — All 3 test files updated: `go-tests-reviewer` in `TEST_AGENTS` and name derivation, agent count 11→12, `go-tests` domain in `ALL_DOMAINS` and all routing matrix entries (11 fixtures × 11 domains). 352 tests pass (up from 320).

## [1.27.0] - 2026-02-15

### Added

- **Stale branch detection** — `review-scope.py` now detects when a feature branch is far behind the base branch (>10 commits) and automatically rebases the diff range to the merge-base (common ancestor). This excludes unrelated trunk files from leaking into review scope. Adds `BRANCH_FRESHNESS:` section to preflight output with `AHEAD`, `BEHIND`, `IS_STALE`, `MERGE_BASE`, and `RANGE_REBASED` fields. JSON output includes `branch_freshness` dict. New `--no-merge-base` flag disables the automatic adjustment.
- **full-code-review command** — Step 3.5 now parses `BRANCH_FRESHNESS` from preflight output, informs the user when scope was adjusted, and suggests rebasing.
- **code-review command** — Step 3.5 adds conditional stale branch check (only acts when `history-insights-reviewer` is dispatched).
- **TestBranchFreshness** — 6 new tests in `test_domain_routing.py`: stale detection, non-stale detection, merge-base range rebasing, `--no-merge-base` bypass, JSON output validation, and non-stale no-rebase.
- **Structural tests** — 3 new tests in `test_commands.py`: stale branch handling in full-code-review, merge-base reference in full-code-review, conditional stale handling in code-review.

## [1.26.2] - 2026-02-13

### Changed

- **dead-code-reviewer agent** — Reframed as "prove reachability" (innocent until proven dead) with stronger evidence requirements. Excludes test files entirely from analysis. Added contrastive examples (correct vs incorrect findings), dynamic dispatch risk assessment (Step 0), universal search template with error handling, categorized false positive checklist (framework callbacks, language magic, dynamic dispatch, build/test infra), worked confidence scoring example, and explicit collaboration boundary rules with handoff signals.

## [1.26.1] - 2026-02-13

### Changed

- **review-scope.py** — Added `--preflight` mode that checks all 10 domains in a single invocation (one `git diff --name-only` call) and outputs `DISPATCH_DOMAINS` / `SKIP_DOMAINS` lists. Supports both text and JSON (`--format json`) output formats.
- **full-code-review command** — Added Step 3.5 (pre-flight scope check) before agent dispatch. Agents whose domain has no matching files are skipped entirely instead of launched and self-exiting. Domain column added to agent table for clarity.
- **code-review command** — Same pre-flight scope check and conditional dispatch changes as full-code-review.
- **test_domain_routing.py** — Added `TestPreflight` class with 8 tests: text/JSON output format, no-domain-required, cross-validation against individual domain checks (all 9 fixtures × 10 domains), dispatch/skip consistency, and all-skip scenarios.

## [1.26.0] - 2026-02-11

### Added

- **dead-code-reviewer agent** — Identifies dead code introduced or exposed by changes: unused functions, unreachable code paths, orphaned imports, unused parameters, and code made obsolete by refactors. Uses `git grep` verification protocol (RULE 0: prove it's dead before reporting) with a comprehensive false positive checklist covering WordPress hooks, magic methods, dynamic dispatch, DI containers, and 17 other dynamic patterns. Categories: `unused-function`, `unused-import`, `unused-variable`, `unused-parameter`, `unreachable-code`, `orphaned-survivor`, `unused-export`, `unused-class`. Dispatched as the 12th reviewer agent in `/code-review` and `/full-code-review`.

## [1.25.0] - 2026-02-09

### Added

- **pr-update command** — Analyzes the current PR branch, discovers relevant artifacts (plans, reviews), respects the project's PR template, generates an accurate description proportional to PR size, validates every claim against the actual diff, and updates the PR after user approval. Supports `gh` and `ghe` (GitHub Enterprise). 8-step protocol: PR detection, branch context, template detection, artifact discovery, draft generation, validation pass, user approval gate, PR update.
- **TestPrUpdate test class** — 12 structural tests for the pr-update command: frontmatter, PR detection, template detection, validation step, approval gate, PR edit, ghe fallback, STOP conditions, brevity calibration, artifact discovery, marketplace registration, and REVIEW_COMMANDS exclusion.

## [1.24.0] - 2026-02-09

### Added

- **code-review command** — Incremental branch-level code review that tracks last reviewed commit and only reviews new changes. Persists state in `.review-state.json` in the output directory. Supports `full`/`reset` arguments to force a full review, and auto-detects rebases to fall back gracefully.
- **ingest-code-review command** — Reads review findings from `/code-review` or `/full-code-review`, validates each finding against the actual diff, filters false positives and out-of-scope noise, and proposes a prioritized action plan.
- **test_commands.py** — Deterministic evals for review command files: frontmatter validation, agent reference cross-checking against marketplace.json, script existence verification, dispatch consistency between commands, and command-specific content checks. 36 test cases.
- **grade_review_state grader** — Validates `.review-state.json` files: required fields, SHA format, positive review count, range separator. 8 test cases in test_graders.py.

## [1.22.1] - 2026-02-09

### Fixed

- **review-scope.py** — Auto-fetch and use remote tracking ref (`origin/<branch>`) as the base for review ranges. Prevents stale local branch refs from inflating review scope with commits already merged to the remote default branch. Best-effort fetch with 15s timeout; falls back gracefully when offline. Guards against double-prefixing (`origin/origin/...`) and SHA-based ranges.

## [1.22.0] - 2026-02-09

### Added

- **full-code-review command** — Branch-level multi-agent code review without requiring a PR. Dispatches 10 specialized reviewer agents in parallel, reconciles findings, and presents a unified summary.

## [1.21.1] - 2026-02-08

### Changed

- **Shared reviewer protocol** - Strengthened reviewing-vs-exploring enforcement
  - Added STOP escalation checkpoint before reporting findings on explored code
  - Added CORRECT/INCORRECT contrastive examples for finding validation
  - Strengthened project-specific knowledge section with explicit READ instruction and priority ordering

- **6 specialist agents** (security, performance, architecture, wp-architecture, patterns, history-insights) - Added confidence scoring gates
  - 0-100 confidence scoring with domain-specific boosters/reducers
  - Findings below 60 confidence are dropped, 60-79 noted as uncertain

- **7 agents** (security, performance, wp-architecture, patterns, history-insights, pr-reviewer + architecture already had it) - Added emotional stimuli
  - Domain-specific "This review matters. [consequence]." statements for identity priming

- **4 agents** (pr-reviewer, php-tests, js-tests, e2e-tests) - Added Core Mission one-liners
  - Consistent arrow-chain format matching existing specialist agents

- **gemini-reviewer, codex-reviewer** - Added error normalization
  - CLI failures framed as expected outcomes, clean UNAVAILABLE report is success

- **review-reconciliator** - Added STOP escalation for unsourced findings
  - Every finding must trace to a specific agent's report

## [1.21.0] - 2026-02-08

### Added

- **Bootstrap reviewer evals** (`tests/`) - Deterministic test suite and grading framework for bootstrap-reviewer.py
  - `test_bootstrap_reviewer.py` — Pytest suite with unit tests (name derivation, protocol extraction, field parsing, output building) and integration tests (subprocess runs for all 11 agents verifying structure, identity, conditional sections, personalization, error handling)
  - `graders.py` — Reusable code-based grading functions for review output files (JSON schema, markdown structure, signal format, no-domain-files, error exit, output pair)
  - `test_graders.py` — Validates graders themselves: valid input passes, missing fields fail, invalid verdicts fail, empty files fail
  - `eval_agent_compliance.py` — Agent compliance runner with `--grade-only` (grade existing outputs) and `--dispatch` (temp repo → bootstrap → dispatch agent → grade) modes
  - `fixtures/no-code-changes.diff` — Docs-only diff fixture for NO_DOMAIN_FILES testing

- **bootstrap-reviewer.py script** (`scripts/`) - Single-command setup that consolidates all reviewer agent initialization into one call
  - Finds plugin root (cached `/tmp/.pirategoat-tools-root`, self-location, or `find` fallback)
  - Validates agent name against known configuration
  - Reads and extracts behavioral rules from `reviewer-protocol.md` (skips setup sections the bootstrap already performed)
  - For test agents, also includes full `tests-reviewer-protocol.md` content
  - Runs `review-scope.py` with agent-specific domain and flags
  - For patterns-reviewer, runs scope twice (normal + `--base-ref-only` for exploration)
  - For tests-mutation-reviewer, skips scope (no domain) but still provides protocol and output instructions
  - Outputs structured prompt block ordered by steering importance: rules (primacy) → scope (processing) → output instructions (recency)
  - Supports `--range` and `--output-dir` pass-through flags
  - Exit codes: 0 (success), 1 (error)

### Changed

- **All 11 reviewer agents** - Simplified MANDATORY SETUP from 3 steps to 1 step
  - Single `bootstrap-reviewer.py --agent <name>` call replaces: get plugin root + read protocol + run scope discovery
  - Reduces setup instructions from ~15 lines to ~7 lines per agent
  - Agents that previously skipped multi-step setup are more likely to comply with a single command
  - Each agent specifies its own `--agent` flag matching its configuration

- **Shared reviewer protocol** - Step 0 now references bootstrap script as preferred method
  - Added bootstrap command as primary setup approach
  - Kept manual steps as fallback if bootstrap unavailable

## [1.20.0] - 2026-02-08

### Added

- **Plugin root discovery hook** (`hooks/`) - PreToolUse:Bash hook writes `$CLAUDE_PLUGIN_ROOT` to `/tmp/.pirategoat-tools-root` so agents can find plugin files when dispatched into target repos
  - `hooks.json` registers the hook for all Bash tool invocations
  - `init-plugin-root.sh` writes the path on each Bash call; agents read it with `cat /tmp/.pirategoat-tools-root`
  - Fallback `find ~/.claude` command when hook hasn't run yet

### Changed

- **All 11 reviewer agents** - Restructured with `## MANDATORY SETUP` as first content after frontmatter
  - Three numbered steps: (1) get plugin root, (2) read shared protocol, (3) run `review-scope.py --domain <X>`
  - Explicit gate: "Do NOT start reviewing code until these 3 steps are done"
  - Identity/expertise section moved below the setup, separated by `---`
  - Previously agents sometimes ignored setup instructions buried in the middle of their definitions

- **Test reviewer agents** (php-tests, js-tests, e2e-tests) - Fixed reference file paths
  - Added explicit `$PLUGIN_ROOT/skills/testing-patterns/references/` prefix
  - Reference table entries now resolve correctly when agents run outside plugin directory

- **architecture-reviewer agent** - Fixed pattern reference paths
  - Added explicit `$PLUGIN_ROOT/skills/software-architecture/` prefix for design pattern files

- **Shared reviewer protocol** - Step 0 uses hook-based discovery with `find` fallback
  - `cat /tmp/.pirategoat-tools-root` as primary method (set by hook)
  - `find ~/.claude -path "*/pirategoat-tools/*/scripts/review-scope.py"` as fallback

## [1.19.0] - 2026-02-08

### Added

- **review-scope.py script** - Shared Python CLI tool that all reviewer agents call to efficiently determine their review scope in a single invocation
  - Replaces 5+ ad-hoc git/grep commands per agent with one structured call
  - Single source of truth for all filtering logic: range detection, noise filtering, domain filtering, context budgeting
  - Parameterized domain catalog: `code`, `security`, `performance`, `architecture`, `wp-architecture`, `php-tests`, `js-tests`, `e2e-tests`, `patterns`
  - Auto-detects default branch (`main`, `master`, `trunk`, `develop`), staged/unstaged changes, and PR number via `gh`/`ghe` CLI
  - Smart `gh` vs `ghe` selection based on remote URL (`github.a8c.com` → `ghe`, `github.com` → `gh`)
  - `--summary` flag for large PRs: outputs diffstat overview of ALL matched files (sorted largest-first) without diffs, letting agents pick which files to deep-dive
  - `--base-ref-only` flag for agents exploring preexisting code (patterns-reviewer, history-insights-reviewer) — skips diff collection, lists all matched files
  - Context budget (`--max-lines`, default 2000) — files sorted smallest-first (focused changes before large files), budget-exceeded files shown with diffstat so agents can selectively read them
  - Defensive error handling: structured error output on both stdout and stderr so agents always see failures; never silently eats errors
  - Extended noise filter: images, fonts, archives, binaries (.wasm, .pyc, .so), PDFs, translation artifacts (.mo, .pot), Jest snapshots (.snap), build artifacts, IDE/OS config
  - Exit codes: 0 (success), 1 (error), 2 (no changes)

### Changed

- **Shared reviewer protocol** - Scope Discovery section now references `review-scope.py` as primary method with bash fallback
  - Output Directory section simplified: script handles `gh`/`ghe` detection automatically
  - Added GHE note for repos on `github.a8c.com`

- **All reviewer agents** - Scope sections simplified to single `review-scope.py --domain <X>` call
  - `pr-reviewer` → `--domain code`
  - `security-reviewer` → `--domain security`
  - `performance-reviewer` → `--domain performance`
  - `architecture-reviewer` → `--domain architecture`
  - `wp-architecture-reviewer` → `--domain wp-architecture`
  - `php-tests-reviewer` → `--domain php-tests`
  - `js-tests-reviewer` → `--domain js-tests`
  - `e2e-tests-reviewer` → `--domain e2e-tests`
  - `patterns-reviewer` → `--domain patterns` + `--base-ref-only` for exploration
  - `history-insights-reviewer` → `--domain code --base-ref-only` for scenario extraction

## [1.18.0] - 2026-02-08

### Changed

- **Shared reviewer protocol** - Agents are now self-sufficient: work both dispatched (from pr-reviewing) and standalone (ad-hoc invocation)
  - New **Scope Discovery** section: agents detect their own review scope from Git Range (if provided), current branch divergence, staged changes, or unstaged changes — in that fallback order
  - New **noise filter**: all agents skip `.lock`, `vendor/`, `node_modules/`, `dist/`, `build/`, binary files, IDE config before any review work
  - New **Output Directory fallback**: agents detect PR number via `gh`/`ghe` CLI when no output dir provided; fall back to `/tmp/` with timestamped filenames to avoid collisions
  - New **Reviewing vs Exploring** rule: explicitly distinguishes analyzing changed code (generates findings) from reading existing code for context (no findings); agents that explore preexisting code must search the base ref state, not HEAD
  - New **context budget**: agents prioritize smaller diffs first and note skipped large files instead of silently ignoring them
  - "Read diffs, not entire files" directive: agents read `git diff <range> -- <file>` and only use `Read` with offset+limit for surrounding context on specific findings

- **All 11 reviewer agents** - Added concrete domain file filters referencing the shared scope discovery
  - `pr-reviewer`: broad code file filter (generalist)
  - `security-reviewer`: code files only (no docs, stylesheets)
  - `performance-reviewer`: code files with queries and operations
  - `architecture-reviewer`: implementation files excluding tests (updated from ad-hoc filter to shared protocol chain)
  - `wp-architecture-reviewer`: PHP/JS/TS files
  - `php-tests-reviewer`, `js-tests-reviewer`, `e2e-tests-reviewer`: concrete grep filters for their test file scopes, with early exit when no matching files in diff
  - `history-insights-reviewer`: scope discovery for scenario extraction, searches are inherently history-scoped
  - `tests-mutation-reviewer`: references shared protocol for scope discovery and output directory

- **patterns-reviewer agent** - Now searches preexisting code only via base ref
  - All codebase searches use `git grep <pattern> <base_ref>` instead of `grep -r .` on working tree
  - Prevents finding the PR's own code when checking for existing patterns
  - Git log searches unchanged (inherently history-scoped)
  - Pattern Search Protocol step 1 updated: "Search base ref code" instead of "Search current code"

## [1.17.0] - 2026-02-08

### Added

- **history-insights-reviewer agent** - Mines git history and GitHub PRs for fixes, enhancements, and lessons learned from similar scenarios elsewhere in the codebase
  - Phase-based approach: scenario extraction, git history mining (commit messages, pickaxe search, PR search), classification, insight report
  - Supports both `gh` (github.com) and `ghe` (github.a8c.com) for PR searches
  - Distinct from `patterns-reviewer`: focuses on bug fixes, edge cases, and improvements rather than pattern consistency
  - Verdicts: `APPLY_FIX`, `CONSIDER_ENHANCEMENT`, `LEARN`, `APPROVE`
  - Categories: `applicable-fix`, `enhancement-opportunity`, `cautionary-precedent`, `edge-case-precedent`, `performance-precedent`, `security-precedent`
  - Integrated into review-reconciliator and pr-reviewing skill parallel dispatch

## [1.16.0] - 2026-02-08

### Changed

- **tests-reviewer agent** - Split into three language-specific agents for focused, non-overlapping reviews
  - `php-tests-reviewer` — PHPUnit, WordPress (WP_UnitTestCase, factories), WooCommerce, Brain Monkey
  - `js-tests-reviewer` — Jest, Vitest, React Testing Library, async patterns, snapshot discipline
  - `e2e-tests-reviewer` — Playwright, Page Object Model, locator strategies, auto-waiting
  - Shared test quality protocol extracted to `agents/shared/tests-reviewer-protocol.md`
  - Each agent reads shared reviewer protocol + shared tests protocol, then applies language-specific red flags
  - Non-overlapping file scopes prevent duplicate findings across agents

- **testing-patterns skill** - Reduced to shared core, language-specific patterns split into dedicated skills
  - `php-testing-patterns` — PHPUnit assertions, WordPress factories, `assertSame` > `assertEquals`, data providers
  - `js-testing-patterns` — RTL query priority, `toMatchObject` > `toEqual`, async assertions, mock scope
  - `e2e-testing-patterns` — Locator priority, Page Object Model, `waitForTimeout` alternatives, network interception
  - Core skill retains: test philosophy, smells, mocking decisions, coverage, test data, test layers
  - Language-specific routing entries removed from core (phpunit-patterns, jest-vitest-patterns, playwright-patterns)
  - Reference files remain in `testing-patterns/references/` (no moves)

- **review-reconciliator agent** - Updated to read three test review outputs instead of one
- **pr-reviewing skill** - Updated parallel dispatch to spawn three test reviewers

### Removed

- `tests-reviewer` agent — replaced by `php-tests-reviewer`, `js-tests-reviewer`, `e2e-tests-reviewer`

## [1.15.0] - 2026-02-08

### Changed

- **Review agents** - Extract shared boilerplate into shared reviewer protocol, reducing agent context by ~45%
  - New `agents/shared/reviewer-protocol.md` (~96L) consolidates: Changed Code Only rule, ReviewOutputBuilder API, file-based output format, return signal template, project-specific knowledge search, ground truth data loading, verbose reasoning mode
  - All 9 reviewer agents now reference shared protocol via `**FIRST:** Read shared/reviewer-protocol.md`
  - Domain-specific content preserved in each agent: RULE 0s, red flags, verification protocols, checklists, review philosophy
  - Boilerplate removed: Structured Output sections, Context format, File-Based Output steps (all identical across agents)

- **software-architecture skill** - Restructured as section-aware routing hub (461L -> 111L, 76% reduction)
  - Code smell -> pattern routing table maps symptoms to specific `## ` headings in reference files
  - Agents read ~200L per reference file instead of ~2,000L (90% reference context savings)
  - Kept inline: SOLID quick reference, architecture review checklist, pattern selection decision matrix, when-not-to-apply rules
  - Removed: GoF pattern categories overview, DEMS D'FFACTS mnemonic, design pattern combinations, inline hexagonal architecture overview, language-specific considerations (all available in reference files or training knowledge)

- **testing-patterns skill** - Restructured as section-aware routing hub (365L -> 104L, 71% reduction)
  - Test smell -> reference routing table maps findings to specific sections in reference files
  - Kept inline: "What Makes a Good Test" table, FORBIDDEN patterns, mocking decision table, test smells quick diagnosis
  - Removed: Inline PHP/JS/Playwright code examples, test review checklist (in tests-reviewer), test layer context table (covered by routing)

- **architecture-reviewer agent** - Replaced skill loading with inline routing table and SOLID reference (674L -> 133L)
- **security-reviewer agent** - Condensed function tables to quick reference, removed code examples (611L -> 119L)
- **performance-reviewer agent** - Condensed optimization tables inline, removed code examples (480L -> 118L)
- **wp-architecture-reviewer agent** - Condensed code examples, kept ecosystem patterns (643L -> 145L)
- **tests-reviewer agent** - Preserved all verification protocols and red flags (803L -> 163L)
- **pr-reviewer agent** - Preserved goal alignment rules and confidence scoring (509L -> 127L)
- **patterns-reviewer agent** - Preserved git history search protocol (421L -> 139L)
- **tests-mutation-reviewer agent** - Preserved all mutation phases and safety rules (552L -> 199L)
- **review-reconciliator agent** - Preserved JSON-first reconciliation with REQUIRED directive (365L -> 209L)

### Added

- `agents/shared/reviewer-protocol.md` - Shared protocol for all review agents

## [1.14.0] - 2026-02-08

### Added

- **tests-reviewer agent** - Overprescriptive test detection and refactoring resilience checks
  - New HIGH severity category (6a-6e): copy/string-based assertions, snapshot overuse, exact data shape assertions, internal call sequence assertions, pinning on incidental details
  - New "Test Resilience" review checklist (7 items) and "overprescriptive" red flags table
  - Extended verification protocol with questions 6-7 targeting refactoring resilience
  - Refactoring Resilience Test diagnostic for verbose reasoning mode
  - New test categories: `overprescriptive-test`, `copy-based-assertion`
  - RULE 0 corollary: fewer meaningful tests beat many overprescriptive tests
- **tests-mutation-reviewer agent** - Adversarial mutation testing that temporarily mutates production code to verify tests catch real bugs
  - Runs SOLO (no other review agents alongside) due to code modification
  - 10-category mutation catalog: boolean flip, comparison swap, string corruption, guard removal, default change, return value change, boundary shift, null swap, array empty, conditional removal
  - Pre-flight safety: stash/unstash, branch verification, test runner auto-detection
  - Per-mutation execution loop: mutate → test → capture → revert → verify revert
  - Mutation score calculation with verdict mapping (>=80% APPROVE, 60-79% COMMENT, <60% REQUEST_CHANGES)
  - Surviving mutation root cause analysis: over-mocking, weak assertions, untested paths, false tests
  - ReviewOutputBuilder integration for reconciliator compatibility
  - Emergency cleanup with nuclear revert option
  - Integrates with pr-reviewing skill as optional post-review phase

## [1.13.1] - 2026-02-06

### Fixed

- **browser-interaction** - Add chrome-devtools profile locations and profile-aware kill procedure
  - Document default (`chrome-profile`) and isolated (`puppeteer_dev_chrome_profile-*`) profile paths
  - Kill procedure tries isolated pattern first, then falls back to default persistent profile
  - Remove `SingletonLock` file that blocks relaunch after a kill
  - Note limitation: isolated pkill kills all instances, no way to target a specific one

## [1.13.0] - 2026-02-05

### Changed

- **Review agents** - Standardized output file naming and added structured output
  - All reviewers now output both JSON and Markdown files consistently
  - Naming pattern: `{domain}-review.json` and `{domain}-review.md`
  - `wp-architecture-reviewer` now outputs to distinct `wp-architecture-review.*` (was conflicting with `architecture-reviewer`)
  - `pr-reviewer` renamed output from `pr-reviewer.md` to `pr-review.md/json`
  - Fixed internal inconsistencies where documentation and code examples showed different filenames

- **pr-reviewer agent** - Added ReviewOutputBuilder and verbose reasoning
  - Now generates structured JSON output alongside Markdown
  - Added comprehensive verbose reasoning mode with templates for:
    - Detection methodology
    - Goal alignment checks
    - Code path analysis
    - Edge case tables
    - Confidence score rationale
    - Alternative interpretations

- **wp-architecture-reviewer agent** - Added ReviewOutputBuilder
  - Now generates structured JSON output alongside Markdown
  - Added WordPress-specific categories for issues
  - Improved pragmatic hooks guidance (don't require hooks everywhere)

- **review-reconciliator agent** - Updated to match new file naming
  - Updated expected file list with all reviewer outputs
  - Added `wp-architecture` and `pr` to agent list
  - Fixed references to old `pr-reviewer.md` filename

## [1.12.0] - 2026-02-05

### Removed

- **browser-navigator agent** - Removed due to MCP tools not being available to subagents
  - Claude Code subagents cannot access MCP tools loaded in the parent session
  - ToolSearch in subagents doesn't discover deferred MCP tools

### Changed

- **browser-interaction skill** - Now instructs direct MCP tool usage instead of agent delegation
  - Quick start guide with ToolSearch → Navigate → Snapshot → Interact workflow
  - Tool mapping table for Chrome DevTools and Playwright MCPs
  - RULE 0 (fresh snapshot after navigation) documented inline
  - Error recovery patterns for profile locks, stale refs, timeouts

## [1.11.3] - 2026-02-05

### Fixed

- **browser-navigator agent** - Enforce MCP-only browser automation
  - Never use Playwright CLI or curl/wget as fallback
  - Bash only allowed for profile lock recovery (pkill)
  - Fail immediately with clear error if no browser MCP available

## [1.11.2] - 2026-02-05

### Added

- **browser-navigator agent** - Support for Playwright MCP as alternative to Chrome DevTools
  - Auto-detects available MCP (Chrome DevTools preferred, Playwright as fallback)
  - Tool mapping table for both MCPs
  - Profile lock recovery only applies to Chrome DevTools (Playwright manages its own lifecycle)

## [1.11.1] - 2026-02-05

### Fixed

- **browser-navigator agent** - Add cyan color (#0891b2) and register in marketplace.json

## [1.11.0] - 2026-02-05

### Added

- **browser-navigator agent** - Isolated browser automation with automatic error recovery
  - Executes all browser tasks in subagent for context isolation
  - Auto-recovers from profile locks, stale refs, tool stalls (max 3 retries)
  - Timeout enforcement: 30s navigation, 10s waits, configurable overall
  - RULE 0 compliance: fresh snapshot after every navigation
  - Flexible output: summary, screenshot, data extraction
  - Lifecycle control: `fresh`, `reuse`, `leave_open`
  - Escalates auth errors and server errors to caller

### Changed

- **browser-interaction skill** - Now dispatches to browser-navigator agent
  - Simplified to lightweight dispatcher + reference documentation
  - All browser logic moved to agent for single source of truth
  - Consistent behavior whether called from main session or subagent

## [1.10.1] - 2026-02-05

### Fixed

- **browser-interaction skill** - Add profile lock recovery and timeout guidance
  - New "Profile Lock Errors" section with `pkill` recovery command
  - Mention of `--isolated` flag for parallel browser sessions
  - New "Timeouts (CRITICAL)" section enforcing explicit timeouts
  - Recommended timeouts: `navigate_page` 30s, `wait_for` 10s
  - Updated error patterns with "Profile lock errors" and "Tool stalls"
  - Updated recovery table with profile lock and stall recovery actions

## [1.10.0] - 2026-01-22

### Added

- **current-datetime skill** (formerly date-time-wrangling) - Verify current date/time before writing timestamps, plus temporal reasoning with Unix date commands
  - Date operations: current date, day of week, date arithmetic, days between dates
  - Time operations: current time (12h/24h), ISO 8601, Unix timestamps, time arithmetic
  - Time zone support: 16 major geographic regions with TZ identifiers
  - Localization guidance: `LC_TIME=C` for English, locale-independent formats
  - Platform support: GNU date (Linux) and BSD date (macOS) syntax
  - Adapted from Matt Hodges' temporal-awareness skill (MIT)

- **Rich Feedback Loops - Phases 2-4 Complete** - Agents now integrate with linters, coverage, and security scanners

  **Phase 2: Linter Integration**
  - `run-linters-for-review.sh` - Executes ESLint and PHPCS with JSON output
  - `parse-linter-results.py` - Unifies linter outputs into standard format
  - architecture-reviewer now uses PHPCS violations as ground truth for code quality
  - wp-architecture-reviewer now uses PHPCS for WordPress Coding Standards (WPCS) violations
  - Linter results treated as definitive for coding standards issues
  - Supports ESLint (JavaScript/TypeScript) and PHPCS (PHP/WordPress)

  **Phase 3: Coverage Integration**
  - `run-coverage-for-review.sh` - Executes test suites with coverage instrumentation
  - `parse-coverage-results.py` - Unifies coverage from Jest and PHPUnit (Clover XML)
  - tests-reviewer now uses coverage data to identify untested code paths
  - Coverage gaps flagged with specific uncovered line numbers
  - Supports Jest (JavaScript/TypeScript), PHPUnit (PHP), and Playwright (E2E)
  - Coverage interpreted as necessary but not sufficient indicator of test quality

  **Phase 4: Security Scanner Integration**
  - `run-security-scanners-for-review.sh` - Executes Semgrep and Bandit with JSON output
  - `parse-security-results.py` - Unifies security scanner outputs
  - security-reviewer now uses scanner findings as ground truth for vulnerabilities
  - CWE mapping to security categories (SQL injection, XSS, CSRF, etc.)
  - Supports Semgrep (multi-language) and Bandit (Python)
  - Scanner findings treated as definitive for pattern-based vulnerabilities

### Changed

- architecture-reviewer and wp-architecture-reviewer now check for linter results
- tests-reviewer now checks for both test results AND coverage data
- security-reviewer now checks for security scanner results
- All feedback phases provide ground truth data that agents treat as definitive
- Agents correlate manual analysis with tool outputs for higher confidence

### Technical Details

- All runner scripts support configurable output directories
- All parser scripts output unified JSON to stdout with consistent schema
- All integrations follow Phase 1 pattern (check for file, load JSON, use as ground truth)
- Zero new dependencies - all scripts use standard library (Python 3, Bash)
- Tools optional - agents gracefully degrade when tools not available

**Implements:** Proposal #5 (Rich Feedback Loops) - Phases 2-4
**Total Phases Complete:** 4 of 5 (Phase 5: Benchmark integration deferred)
**Annual Value:** $240K+ (from eliminating false positives/negatives)

## [1.9.0] - 2026-01-21

### Added

- **Structured Output Integration** - All 5 review agents now output both JSON and Markdown
  - Integrated ReviewOutputBuilder into all agents (security, architecture, performance, tests, patterns)
  - Agents automatically generate dual outputs: `.json` (machine-readable) + `.md` (human-readable)
  - JSON enables automation: CI/CD integration, metrics dashboards, auto-issue creation
  - Markdown maintains human-readable reviews with verbose reasoning support
  - Auto-calculated verdicts from issue severities
  - Structured metadata: confidence scores, tools used, files reviewed, timestamps
  - Completes Proposal #3 integration from Tier 1 agentic patterns
  - Agent-specific categories:
    - Security: sql-injection, xss, csrf, capabilities, file-upload, data-exposure
    - Architecture: solid-violation, coupling, cohesion, abstraction-leak, god-class
    - Performance: n-plus-one, caching, autoload, remote-requests, scale-issues
    - Tests: test-failure, missing-coverage, flaky-test, brittle-test, over-mocking
    - Patterns: inconsistency, duplication, anti-pattern, naming-convention

### Changed

- All 5 review agents now use ReviewOutputBuilder for consistent output format
- Output files now include both `.json` and `.md` extensions
- Verdicts auto-calculated (no manual verdict writing needed)
- Moved review output library to plugin directory (lib/ → plugins/pirategoat-tools/lib/)
  - review_output_simple.py (dependency-free builder - ONLY implementation kept)

### Removed

- Pydantic-dependent implementations (review_output_builder.py, review_schemas.py)
  - Removed to eliminate dependencies - review_output_simple.py provides all needed functionality
  - No pydantic installation required

## [1.8.3] - 2026-01-21

### Added

- **Structured Output Foundation** - JSON schema infrastructure for reliable automation
  - `schemas/review-output.ts` - Complete TypeScript type definitions for all review types
  - `lib/review_schemas.py` - Pydantic models for runtime validation (requires pydantic package)
  - `lib/review_output_simple.py` - Dependency-free builder (works immediately, no installs)
  - ReviewOutputBuilder helper class with dual output (JSON + Markdown)
  - Schema definitions: Issue, SecurityIssue, PerformanceIssue, ArchitectureIssue, TestIssue, PatternIssue
  - Verdict auto-calculation from issue severity
  - Confidence scoring and metadata tracking
  - Implements Proposal #3 foundation from Tier 1 agentic patterns

Note: Agent integration will follow in next release. Foundation ready for use.

## [1.8.2] - 2026-01-21

### Added

- **Rich Feedback Loops - Phase 1: Test Runner Integration**
  - `scripts/run-tests-for-review.sh` - Executes Jest, PHPUnit, Playwright with JSON output
  - `scripts/parse-test-results.py` - Unifies test results from multiple frameworks into standard format
  - `tests-reviewer` agent now consumes actual test execution results (ground truth)
  - Agent decision logic updated: test failures = automatic BLOCK verdict
  - Eliminates false approvals based on "code looks good" without execution
  - Test results format: unified JSON with pass/fail counts, failure details, locations
  - Demo test suite in `test-samples/feedback-loops-demo/` with failing tests
  - Baseline documented: 100% false approval rate without feedback, 0% with feedback
  - Implements Proposal #5 Phase 1 from Tier 1 agentic patterns

## [1.8.1] - 2026-01-21

### Added

- **Semantic Context Filtering MVP** - Regex-based diff noise reduction for efficient reviews
  - `scripts/semantic-filter-mvp.py` - Production-ready filter removing blank lines, docblocks, comments, pure formatting
  - Achieves 40.5% noise reduction with 100% signal preservation
  - No dependencies (pure Python regex), fast implementation (1 hour)
  - Validates on test case: 78 lines → 47 lines, all 6 semantic changes preserved
  - Conservative filtering approach (when in doubt, keep the line)
  - Test suite in `test-samples/semantic-filter-test/` with baseline and results
  - Foundation for future AST-based enhancement (70%+ reduction)
  - Implements Proposal #1 from Tier 1 agentic patterns (Phase 1 MVP)

- **Verbose Reasoning Mode** - All review agents now support detailed reasoning transparency
  - `architecture-reviewer` - Shows SOLID analysis, pattern opportunities, confidence scoring
  - `security-reviewer` - Shows exploitation paths, CVSS scoring, defense-in-depth analysis
  - `performance-reviewer` - Shows 10x/100x scale impact, query analysis, optimization paths
  - `tests-reviewer` - Shows test quality analysis, root cause diagnosis, mocking analysis
  - `patterns-reviewer` - Shows git history evidence, consistency analysis, consolidation opportunities
  - Reasoning includes: detection process, checks performed, confidence scores, severity rationale, cross-references, alternative interpretations
  - Optional mode enabled via VERBOSE=true environment variable
  - Uses expandable `<details>` blocks for readability
  - Implements Proposal #2 from Tier 1 agentic patterns

- `pr-reviewing` skill - Added VERBOSE flag documentation and passing to all agents
  - When to enable verbose mode (learning, debugging, low confidence, critical findings)
  - How to enable (export VERBOSE=true)
  - Context preparation includes verbose mode flag
  - Agents receive VERBOSE signal and include reasoning when enabled

### Changed

- `pr-reviewing` skill - Strengthened parallel spawning requirements (Proposal #4)
  - Added CRITICAL instruction emphasizing single message with multiple Task calls for parallel execution
  - Added anti-pattern section showing sequential spawning (what NOT to do)
  - Added explicit timing comparison (parallel: 28s vs sequential: 75s)
  - Clarified correct parallel spawning pattern with examples
  - Result: Ensures 3x faster reviews through proper parallel agent orchestration

## [1.7.1] - 2026-01-14

### Added

- `architecture-reviewer` agent - General-purpose software architecture code review
  - Leverages software-architecture skill for comprehensive pattern knowledge
  - Reviews: Design patterns, SOLID principles, coupling/cohesion, architectural code smells
  - Works with any codebase: PHP, JavaScript, TypeScript, Python, Java, etc.
  - Analyzes: God objects, tight coupling, SOLID violations, design pattern opportunities
  - Provides: Specific recommendations with file/line references, pattern implementation guides
  - Prioritizes by impact: Critical (blocks changes) → Important (creates debt) → Nice-to-have
  - Includes: Rule of three, YAGNI principles, over-engineering detection, testability analysis
  - Output: Structured markdown with executive summary, SOLID violations, pattern opportunities, prioritized recommendations
  - Complements wp-architecture-reviewer (WordPress-specific) for general architectural analysis
  - References specific pattern docs (e.g., `patterns/behavioral/strategy.md`) for implementation

## [1.7.0] - 2026-01-14

### Added

- `software-architecture` skill - Comprehensive design patterns and software architecture guidance
  - Covers GoF design patterns, SOLID principles, hexagonal architecture, and composable designs
  - Pattern selection guide mapping architectural problems to pattern solutions
  - Essential patterns (DEMS D'FFACTS): Command, Strategy, Template Method, Adapter, Façade, Factory, Dependency Injection
  - Common architectural problems troubleshooting table with SOLID violations
  - Pattern combinations and anti-patterns guidance
  - Refactoring to patterns tactical guide
  - Architecture review checklist
  - Language-specific considerations for PHP/WordPress and JavaScript
  - Comprehensive pattern reference library (716KB total) synthesized from jhumelsine.github.io architecture series:
    - **Behavioral patterns:** Command, Strategy, Template Method, Chain of Responsibility, Specification
    - **Structural patterns:** Adapter, Façade, Decorator, Composite, Proxy
    - **Creational patterns:** Factory (Method, Class, Abstract), Dependency Injection
    - **Architectural patterns:** Hexagonal Architecture (Ports & Adapters, Clean Architecture)
    - **Core concepts:** SOLID Principles, Composable Design, Pattern Relationships
    - **Navigation:** patterns/README.md with 4 reading paths and pattern taxonomy
  - All pattern references include: when to use, when NOT to use, structure, implementation guide (PHP), benefits, trade-offs, common mistakes, pattern relationships, decision criteria
  - Real-world examples, quotes, and further reading sections throughout

## [1.6.0] - 2026-01-14

### Added

- `testing-patterns` skill - Comprehensive test quality patterns for PHP (PHPUnit/WordPress), JavaScript (Jest/Vitest), and E2E (Playwright)
  - Reference guides for test quality, structure (AAA), mocking strategies, test data management, and coverage
  - Language-specific patterns including WordPress/WooCommerce testing utilities
  - Test philosophy section emphasizing tests as specifications, not verification
  - Test smells diagnostic guide with root cause analysis
  - Enhanced quality principles table (9 attributes including behavior-based, declarative, complete)
  - Mocking principles section with clear guidance on when/how to mock
  - Test layer context comparing unit/integration/E2E with strategy guidance
  - Skill now includes contextual pointers to deep-dive references throughout
  - Organized reference library section: Quick Reference (tactical) vs Deep Dives (strategic)
  - "Using the Reference Library" guide at end of skill with navigation by problem type
  - Comprehensive reference documents synthesized from jhumelsine.github.io architecture blog series (77KB total):
    - `README.md` - Navigation guide with 4 reading paths and key insights summary
    - `test-philosophy.md` - Mental models, behavior vs implementation, the fundamental shift (12KB)
    - `test-smells.md` - Diagnostic guide for flaky, brittle, slow, complex tests with root cause analysis (16KB)
    - `tdd-workflow.md` - Complete Red-Green-Refactor cycle with examples and anti-patterns (15KB)
    - `test-layers.md` - Unit/Integration/System comparison with Mars Orbiter lesson and strategy guidance (17KB)
    - `test-benefits.md` - 13 benefits of testing from specifications to future bug prevention (17KB)
  - All reference docs include real-world examples, quotes, and further reading sections
- `tests-reviewer` agent - Test quality-focused code review for test structure, assertions, mocking patterns, coverage, and anti-patterns

## [1.5.0] - 2026-01-10

### Added

- `pr-reviewer` agent - Generalist PR reviewer that validates code changes against stated goals
- `security-reviewer` agent - WordPress security-focused review (XSS, SQL injection, CSRF/nonces, capabilities, sanitization/escaping)
- `performance-reviewer` agent - WordPress performance-focused review (N+1 queries, caching/transients, autoloaded options, WP_Query)
- `wp-architecture-reviewer` agent - WordPress architecture-focused review (hooks/extensibility, WPCS, backwards compatibility, i18n)
- `patterns-reviewer` agent - Explores codebase and git history for existing patterns, ensures consistency, identifies consolidation opportunities
- `gemini-reviewer` agent - Cross-validates PR changes using Google Gemini CLI
- `codex-reviewer` agent - Cross-validates PR changes using OpenAI Codex CLI
- `review-reconciliator` agent - Reads all review files, reconciles findings, produces consolidated summary
- File-based output architecture - All review agents write to temp files, return only signals to conserve context

### Changed

- Updated `pr-reviewing` skill to orchestrate specialist agents
- Added cross-validation with external AI (Gemini/Codex) for critical PRs
- Generalist always runs first and anchors reconciliation of specialist findings
- Patterns reviewer runs on all PR sizes to prevent reinventing the wheel
- All specialist agents now search for project-specific AI docs before reviewing

### Removed

- `architect` agent - Unused, replaced by specialized review agents
- `developer` agent - Unused, replaced by specialized review agents
- `debugger` agent - Unused, replaced by specialized review agents
- `quality-reviewer` agent - Unused, replaced by specialized review agents
- `adr-writer` agent - Unused

## [1.4.0] - 2026-01-10

### Added

- `pr-reviewing` skill - Structured PR review workflow ensuring context gathering (Linear issue, PR state, previous reviews) before code review

## [1.3.0] - 2026-01-10

### Added

- `browser-interaction` skill - Browser automation for debugging, verification, testing using MCP servers (chrome-devtools, playwright, puppeteer)
- `dig-into-linear-issue` skill - Thorough Linear issue investigation workflow with RCA templates and validation paths
- `woocommerce-browser-interaction` skill - WooCommerce-specific browser automation patterns (login, admin, frontend, block checkout)

## [1.2.0] - 2025-12-11

### Changed

- Extracted `prompt-optimizer` skill and `/optimize-prompt` command into standalone plugin

## [1.1.0] - 2025-12-11

### Changed

- Extracted `image-optimizer` skill into standalone plugin

## [1.0.0] - 2025-12-09

### Added

- Initial release of pirategoat-tools plugin
- **Skills:**
  - `image-optimizer` - Lossless image optimization using imageoptim-cli and svgo
  - `prompt-optimizer` - Two-phase prompt optimization with pattern attribution
  - `wordpress-backend-dev` - WordPress backend development guidance (WPCS, security, i18n, hooks)
- **Commands:**
  - `/fix-github-issue` - Analyze and fix GitHub issues end-to-end
  - `/execute-plan` - Project manager mode for executing implementation plans
  - `/optimize-prompt` - Quick access to prompt optimization
- **Agents:**
  - `architect` - Lead architect for code analysis and solution design
  - `developer` - Implementation specialist with test focus
  - `debugger` - Systematic bug analysis through evidence gathering
  - `quality-reviewer` - Code review for real issues (security, performance)
  - `technical-writer` - Documentation creation after feature completion
  - `adr-writer` - Architecture Decision Record creation
