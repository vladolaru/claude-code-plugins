# pirategoat-tools — Agent Instructions

You are the maintainer of pirategoat-tools, a code review orchestration plugin. You dispatch domain-specific reviewer agents in parallel, reconcile their findings through semantic deduplication and verification, then stress-test conclusions via an independent decision critic.

## Key Files

**IMPORTANT: `scripts/review/agent_registry.json` is the single source of truth for all reviewer agent configuration.** Every agent change starts and ends here.

| File | Role |
|------|------|
| `scripts/review/pipeline.py` | Unified 12-step review pipeline. Owns step sequence, routing, state management, and curated briefings. Called by all three review commands with `--mode pr\|full\|incremental`. |
| `scripts/review/agent_registry.json` | Agent registry — domain, protocols, dispatch class, triage criteria, model tier. |
| `scripts/review/agent/bootstrap.py` | Builds the structured prompt each agent receives. Handles plugin root discovery, protocol extraction, scope discovery, and output instructions. When a primary domain matches nothing but a secondary domain does, `resolve_overall_status` flips the status to a scoped `OK` and injects a `COVERAGE NOTE` so the agent reviews the secondary files with an honestly-scoped verdict instead of silently masking the gap. |
| `scripts/review/agent/scope.py` | Efficient diff scoping. Filters changes by domain (security, performance, php-tests, etc.) and outputs structured STATUS/FILES/STATS/DIFFS sections. **Language recognition lives in one place:** the `_PROG_LANGS`/`_STYLE_LANGS`/`_QUERY_LANGS`/`_DOC_LANGS`/`_DATA_LANGS`/`_FRONTEND_LANGS` groups, plus `_MIXED_MARKUP_LANGS`, `_TEMPLATE_LANGS`, and `_TEMPLATE_SUFFIXES` for rendered UI. Domains compose extensions via `_ext_re(...)`; `is_template_file()` distinguishes pure and compound templates for a11y dispatch and budget priority. Add formats to these sources once — never edit per-domain regexes. Budget priority tiers (`production_first`, `markup_evidence`) order files before largest-first budgeting; one oversized leading diff is protected outside the ordinary pool, and `--summary-json-out` persists per-agent scope summaries for run-level coverage accounting. |
| `scripts/review/plan_dispatch.py` | Deterministic dispatch planning. Reads agent registry + changed files → produces which agents to run, skip, and why. Called internally by review/pipeline.py. Also runs the unrecognized-source safety net (`detect_unrecognized_source`) that emits a `warnings[]` entry when a changed source language no domain covers — so coverage gaps fail loudly instead of producing a clean review. |
| `scripts/review/context.py` | Unified Ring 1 context collection. Fills git context, PR metadata, reviews, linked issues, staleness, and author name. |
| `scripts/review/agent/output.py` | ReviewOutputBuilder — `add_issue()`, `add_recommendation()`, `add_positive()`, verdict calculation, JSON/Markdown serialization. |
| `scripts/review/reconciliation_context.py` | Pre-gathers agent findings, source snippets, scope annotations into a single context. Produces both JSON (`reconciliation-context.json`) and Markdown (`reconciliation-context.md`) via `to_markdown()`. The reconciliator reads the Markdown version (~40% more token-efficient). Called by pipeline step 8. |
| `scripts/review/telemetry.py` | JSONL telemetry logging. `ReviewTelemetry` class captures pipeline timing, agent start/complete lifecycle, snapshots, and summaries. |
| `agents/shared/reviewer-protocol.md` | Shared behavioral rules for all reviewer agents. Bootstrap extracts sections via skip-list. |
| `agents/shared/tests-reviewer-protocol.md` | Additional rules for test reviewer agents (test quality principles, anti-patterns). |
| `schemas/review-output.ts` | TypeScript type definitions for structured review output (Issue, SecurityIssue, PerformanceIssue, etc.). |
| `scripts/iterative_review/` | Iterative review loop sub-module. Multi-round independent review (Codex primary, Claude Code fallback) with pushback tracking, convergence detection, noise-filtered diff sizing, and telemetry. CLI entry point: `python3 -m iterative_review --action review\|advance [--autonomous]`. |
| `scripts/linear/pipeline.py` | 15-step curated-context pipeline for investigating and fixing Linear issues. Owns step sequence, routing, state management, and curated briefings. Called by pirategoat-bot via `--step N --mode investigate\|fix`. |
| `scripts/linear/events.py` | Best-effort JSONL event emission for pipeline progress (step_started, milestone, deliverable, pipeline_complete). Used by both review and linear issue pipelines. |
| `scripts/hosts/host_context.py` | CLI entrypoint for upstream-host discovery. Runs the resolver chain and writes `host-context.json` under `--output-dir`. Invoked standalone or via `review/context.py`. |
| `scripts/hosts/chain.py` | Composes repo-signaled advisory resolvers in priority order (explicit → wp-env → docker-compose → install-cache → vendor), dedups by `kind:name`, and generates the degradation banner. The `install-cache` resolver runs before `vendor` so a freshly-populated per-clone cache wins via name-collision dedup; `vendor` still serves repos with in-repo `vendor/`/`node_modules/` but no lockfile. Ambient sibling/ecosystem-cache resolvers exist as standalone helpers but are not in the default chain. |
| `scripts/hosts/resolvers/` | Individual resolver implementations. Each reads local filesystem signals and emits `HostEntry` records without side effects. |
| `scripts/hosts/ensure_installed.py` | Per-clone library-dep install cache. One slot per (clone, manager) at `~/.cache/pirategoat/library-deps/<clone_id>/<manager>/`. Replaced when the lockfile content changes; never modifies the working tree. Invoked by `review/context.py` at step 3 (Gather Context); also runnable standalone. Opportunistic stale-clone GC runs at every invocation. Mandatory `--ignore-scripts`; known-failure retry table; banner-on-failure semantics. |
| `scripts/hosts/ecosystem_cache.py` | Machine-wide ecosystem source cache management (WordPress + WooCommerce). `--update` / `--list` / `--verify`. |
| `scripts/hosts/install/` | Internal install submodule: lockfile hashing (`lockfile.py`), per-clone cache with atomic staging + stale-clone GC (`cache.py`), subprocess runner with retry table (`runner.py`), overrides parsing (`overrides.py`). |
| `scripts/hosts/cache/` | Internal ecosystem-cache manager (`manager.py`): clone / git-pull / verify-staleness for WordPress + WooCommerce. |

## Architecture

### Review Pipeline

```
Command (thin wrapper: pr-review.md, full-code-review.md, code-review.md)
  │
  └─ review/pipeline.py --step N --mode pr|full|incremental
      │
      ├─ Step 3: review/context.py → review-context.json
      ├─ Step 5: review/plan_dispatch.py → dispatch-plan.json
      │
      ├─ Step 6: For each agent (parallel):
  │   │
  │   └─ review/agent/bootstrap.py
  │       ├─ Extracts protocol sections (skip-list)
  │       ├─ Runs review/agent/scope.py (domain-filtered diff)
  │       └─ Builds structured prompt:
  │           Section 1: REVIEW RULES    (top — primacy effect)
  │           Section 2: REVIEW CONTENT  (middle — processing zone)
  │           Section 3: OUTPUT          (bottom — recency effect)
  │
  ├─ Step 8: review/reconciliation_context.py
  │   └─ Gathers all agent JSONs + source snippets + scope annotations
  │       → reconciliation-context.json + reconciliation-context.md
  │
  ├─ review-reconciliator agent (semantic dedup + scope check + fact verification)
  │   └─ Reads reconciliation-context.md → produces review-findings.json + review-findings.md
  │
  └─ decision-reviewer agent (independent stress test)
      └─ Produces decision-critic-findings.md with STAND/REVISE/ESCALATE verdict
```

### Pipeline Briefing Design

The step briefings in `review/pipeline.py` follow deliberate design patterns. These are inline rules — see `docs/patterns/curated-context-pipeline.md` for the general principles and rationale behind them.

**Identity anchoring.** `_PIPELINE_MISSION` constant holds the orchestrator's mission statement. Step 1 prepends it to `situation`. Do not modify the mission text without reviewing the pattern doc's "Pipeline Identity Anchoring" principle — it was designed to anchor the LLM on dedication, precision, and artifact discipline.

**Phase transitions.** `_PHASE_TRANSITIONS` dict maps phase names to contextual reminders injected at phase-entry steps:

| Phase | Injected at | Focus |
|-------|------------|-------|
| EXECUTION | Step 5 | Precision in dispatch |
| SYNTHESIS | Step 8 | Faithful synthesis, no bias |
| VALIDATION | Step 10 | Stress-test before it reaches a human |
| OUTPUT | Step 11 | Complete delivery, nothing missing |

These are variations on the mission, not repetitions. Each connects the mission to what's about to happen.

**Artifact discipline.** File-producing steps follow Write → Verify → Proceed:
- `handoff` is the sole gate mechanism. If a step requires an artifact before the next step can proceed, it goes in `handoff`, not buried in `actions`.
- JSON examples use schema format: `{"verdict": "<APPROVE | REQUEST_CHANGES | COMMENT>"}` — never copyable placeholder values.
- Steps 3/4, 8, 9, and 10 have `handoff` gates on their output files.

**Voice.** Senior reviewer briefing the orchestrator — authority on process, trust on execution. The voice lives within the structural section headers (SITUATION / ACTIONS / HANDOFF). The headers themselves stay rigid as machine-readable landmarks.

**Modifying briefings.** Tests check for keywords in briefing text (e.g., `"review-reconciliator" in text`, `"STAND" in text`). When rewriting briefing prose, preserve these keywords. Run the relevant `TestStep*` class after any text change.

### Step 8 Readiness Gate

Before reconciliation, step 8 checks if all dispatched agents have finished via `review/agents_status.py`. If agents are still running, returns a WAITING briefing. Tracks `first_waiting_at` in pipeline state. If elapsed wait exceeds `agent_timeout_seconds + 60s`, escalates: clears the waiting state and proceeds with reconciliation using available results, instructing the LLM to TaskStop stuck agents first.

### Shared Protocols

**reviewer-protocol.md** provides behavioral rules for all agents. Bootstrap extracts it via a **skip-list** — sections the bootstrap already handles are excluded, everything else is included automatically. New sections added to the protocol are picked up without code changes.

Skip-list (sections bootstrap replaces with concrete values):
- `## Step 0` (plugin root — bootstrap resolved it)
- `## Scope Discovery` (bootstrap ran review/agent/scope.py)
- `## Output Directory` (bootstrap resolved to concrete path)
- `## ReviewOutputBuilder API` (bootstrap provides pre-filled snippet)
- `## File-Based Output` (bootstrap provides concrete file paths)

**tests-reviewer-protocol.md** is appended for agents with `"tests-reviewer"` in their `protocols` list. It adds test quality principles (RULE 0: tests verify behavior, not implementation) and common anti-patterns.

### Bootstrap Output Positioning

The prompt bootstrap builds uses deliberate section ordering. Preserve this order when modifying `review/agent/bootstrap.py` or protocol files:

1. **REVIEW RULES** (top) — behavioral steering via primacy effect. Agent reads rules first, anchoring behavior.
2. **Context sections** — PR INTENT (raw PR metadata), REVIEW FOCUS (pipeline synthesis from change-purpose.md), REVIEWER-REQUESTED FOCUS (requester's additional instructions from `run-config.json`, present only when steering keywords were provided), HOST CONTEXT (advisory upstream runtime-host and library-dep path hints from `review-context.json.host_context`, injected when available), and REVIEW BUDGET (scope-proportionate tool call calibration).
3. **REVIEW CONTENT** (middle) — the actual diff/scope. Processing zone where the agent does its work.
4. **OUTPUT INSTRUCTIONS** (bottom) — format and file paths. Recency effect ensures the agent remembers how to produce output.

## Agent Registry

`scripts/review/agent_registry.json` configures all reviewer agents. Each entry:

| Field | Required | Description |
|-------|----------|-------------|
| `domain` | yes | Scope domain for `review/agent/scope.py` filtering. `null` for agents that don't use scope (e.g., tests-mutation-reviewer). |
| `protocols` | yes | List of protocol files to include: `"reviewer"` (all agents), `"tests-reviewer"` (test agents). |
| `scope_flags` | yes | Extra flags passed to `review/agent/scope.py` (e.g., `["--max-lines", "500"]`). Empty list `[]` for defaults. |
| `dispatch_class` | yes | When agent runs — see dispatch classes below. |
| `focus` | yes | One-line description of the agent's review focus. Surfaced in the step 5 dispatch summary for override decisions — see sync rule below. |
| `model_tier` | yes | `"inherit"` (caller's model), `"sonnet"`, or `"haiku"`. Match reasoning depth needed. |
| `triage_criteria` | conditional | Required for `dispatch_class: "conditional"`. List of conditions that trigger dispatch. **Every bullet is an executable contract**: `tests/review/test_criteria_coverage.py` requires a minimal probe diff per criterion that MUST dispatch through the real pipeline. Adding or rewording a criterion without a matching probe fails CI. If no keyword/check can back a criterion, give the agent one (prefer structural `triage_checks` for structural criteria) or reword the criterion — never write criteria the machinery can't honor. **One signal-able clause per bullet**: a compound bullet ("queries, API calls, fetching hooks") hides unprobed branches — the meta-test sees one probe quoting the bullet and cannot tell the other clauses have no signal. Split compounds so each clause gets its own probe. **Probes must be text-neutral**: `TestProbeNeutrality` re-runs every probe with commit/PR text blanked — unless the criterion is explicitly about commit/PR text, the signal must live in the probe's diff, files, or diffstat, or the clause is silently unsignaled for real diffs with neutral wording. |
| `triage_keywords` | optional | Change-local keywords matched against commits, changed paths, PR metadata, and scoped patch text. Word-start-anchored, separator-tolerant prefix match (`move` ≠ `remove`; `screen reader` matches `screen-reader`); repo-structural directory segments (`plugins/`, `src/`, …) are excluded from path matching. Never use language-structural terms (`function`, `class`, `remove`, …) — a registry test bans them; use `triage_checks` for structural signals. |
| `require_triage_keyword_match` | optional | Blanket evidence gate: skip unless a keyword OR a `triage_checks` entry fired (checks run before the gate). Used by woo-regression-reviewer (WC-signal requirement). |
| `triage_repository_keywords` | optional | Ambient repository-identity keywords matched against all fetch remote URLs plus the checkout basename. Opt in only when repository membership is itself sufficient for applicability. |
| `secondary_domains` | optional | Additional scope domains to include (e.g., `["config-ops"]`). |
| `extra_scope` | optional | Additional scope invocations (e.g., `["--base-ref-only"]` for patterns-reviewer). |
| `budget_override` | optional | Fixed tool-call budget, bypassing scope-proportional computation. Use for agents whose workload doesn't correlate with diff size (e.g., history-insights explores git history). |
| `file_history` | optional | If `true`, bootstrap includes git history per changed file. |
| `max_history_commits` | optional | How many commits of history per file (default: 5). |

### Dispatch Classes

| Class | Behavior |
|-------|----------|
| `always` | Auto-dispatched on every review |
| `conditional` | Dispatched when triage criteria match the diff. Dispatch-by-default when the domain has files and no explicit evidence gate fires; detector silence and diff size never prove irrelevance. Skips remain visible in `agent_signals` for orchestrator override. |
| `manual` | Only on explicit user request |
| `special` | Orchestration/synthesis agents, not dispatched by triage |

Commands handle triage at the "Adaptive Agent Triage" step — they check each conditional agent's `triage_criteria` against diffstat, changed paths and patch text, commit messages, and PR metadata. Repository identity participates only when the agent explicitly declares `triage_repository_keywords`.

### Agent Name and Focus Sync

The registry `focus` field is surfaced to the main session at step 5 so the LLM can make informed dispatch override decisions. The agent `.md` `description` field is loaded by Claude Code into the system prompt. These must stay aligned:

| Source | Field | Purpose | Audience |
|--------|-------|---------|----------|
| `agent_registry.json` | `focus` | Dispatch summary in step 5 briefing | Main session LLM (during pipeline) |
| `agents/<name>.md` | `description` (frontmatter) | Agent catalog in CC system prompt | Any session using the Agent tool |

**Rule: When updating an agent's specialization, update both the registry `focus` and the agent `.md` `description` to reflect the same scope.** They don't need identical wording — `focus` is a concise keyword list, `description` is a full sentence — but they must cover the same capabilities. A `focus` that lists "XSS, SQL injection" while the `description` says "sanitization, escaping, nonces, auth" creates a misleading dispatch summary.

**Calibration:** `focus` should be specific enough to inform override decisions (not just "test quality") but concise enough to scan in a list (not a full sentence). Aim for 5-10 keywords/phrases that distinguish this agent from others.

## Repo-Contributed Reviewers and Rules

The repository **under review** can extend a review with its own regression-seeded
knowledge and domain-expert lenses, declared in an optional `review` section of its
`.pirategoat/config.json` (the same repo-owned file `ExplicitResolver` reads for
`hosts.runtime`). `scripts/review/review_config.py` is the single source of truth —
it parses/validates the section and owns the shared applicability primitives
(`glob_match`, `rule_applies_to_agent`, `reviewer_applies_to_diff`). `context.py`
carries the normalized result into `review-context.json` under `review_config`
(recomputed each run, like `host_context`).

**Two capabilities:**

1. **Repo rules** (`review.rules[]`) — markdown checklists the repo authors. `bootstrap.py`
   selects the rules applicable to each agent (by agent name, domain, or a changed file
   matching a path glob) and injects a `=== REPO REVIEW RULES ===` block after DOMAIN RULES
   (project standards override generic patterns). Repo bodies are SEMI-TRUSTED: rendered
   inside a dynamic backtick fence with a provenance/demotion banner so they cannot override
   the reviewer's output contract.

2. **Repo reviewers** (`review.reviewers[]`) — self-contained, pirategoat-agnostic reviewer
   prompts. `plan_dispatch.py::expand_repo_reviewers` turns each into a synthetic dispatch
   entry named `repo-<id>-reviewer` targeting the generic `repo-reviewer-adapter` agent,
   gated by applicability like a conditional agent. The adapter (registry `special`,
   `domain: null`) runs in bootstrap **ref-mode** (`--repo-agent-ref/--instance-name/
   --execution/--channel/--scope-domains`): it reads the repo prompt, runs it against the
   scoped diff, and normalizes findings via `ReviewOutputBuilder`.

**Load-bearing invariants** (break these and findings silently vanish or collide):
- The synthetic name MUST end in `-reviewer` — reconciliation maps `-reviewer`→`-review` to
  find `repo-<id>-review.json`.
- Ref-mode derives the reviewer name and `.started` marker from `--instance-name`, not the
  shared adapter key, so N adapter instances never clobber one output file.
- **Advisory channel:** a reviewer/rule with `"channel": "advisory"` produces findings that
  are listed but NEVER gate the verdict. `add_issue(..., channel="advisory")` is skipped in
  `_calculate_verdict`. Native agents never set `channel`, so this is backward-compatible;
  `reconciliation_context.py` surfaces it and the reconciliator preserves it.

**Execution:** inline only in v1 (the adapter reads and runs the repo prompt in-context).
`isolated` (headless CLI, different model family) is reserved behind the `--execution` flag.

## Output Contract

Each reviewer agent produces two files in `OUTPUT_DIR`:

- `<agent-name>.json` — structured findings using `ReviewOutputBuilder` (see `schemas/review-output.ts` for types)
- `<agent-name>.md` — human-readable Markdown summary

**ReviewOutputBuilder API** (`scripts/review/agent/output.py`):

```python
from review.agent.output import ReviewOutputBuilder
builder = ReviewOutputBuilder(reviewer="security-reviewer", pr_id="123")
builder.add_issue(severity="high", category="xss", title="...", description="...", file="...", line=42, recommendation="...")
# line=None records a verdict-counting FILE-SCOPED issue (line: null, scope: "file") —
# only for findings that are line-less by nature (missing coverage, precedent, cross-file architecture)
builder.add_positive("Good input validation on...")
builder.add_recommendation("immediate", "Fix the XSS vulnerability")
output = builder.build()  # Returns dict with verdict, summary, issues, etc.
```

Verdict is auto-calculated from issue severities:
- Any critical → `block`
- 3+ highs → `block`
- Any high (or 5+ mediums) → `request_changes`
- Any medium → `comment`
- Otherwise → `approve`

### Cross-Repo Dependency: pirategoat-bot

The `pirategoat-bot` Slack bot (at `~/Work/a8c/pirategoat-bot`) wraps this plugin's review pipeline. The two repos share integration contracts that must stay in sync:

- **`review-context.json`** — The bot writes this file (orchestrator.js) before spawning the `claude` CLI. This plugin reads and enriches it (review/context.py). Field names, nesting, and required paths must match across both repos.
- **Outer-pipeline verdict values** — The bot's `pr-review.py` defines outer-pipeline verdicts (`APPROVE`/`COMMENT`/`REQUEST_CHANGES`) and `github.js` maps them to GitHub actions. This plugin has its own per-agent verdict system (`block`/`request_changes`/`comment`/`approve` in `review/agent/output.py`). These are distinct layers — changes to one may need corresponding changes in the other.
- **Prompt template variables** — The bot's `prompts/pr-review.md` injects variables (`{{MERGE_BASE}}`, `{{GIT_RANGE}}`, etc.) that this plugin's scripts consume via the review context.

**Rule: Before changing any integration surface in this plugin, read the corresponding code in pirategoat-bot first.** Do not assume the bot's expectations from this plugin's code alone — check the bot's actual implementation. When in doubt, read:
- `pirategoat-bot/src/orchestrator.js` (writes review-context.json, reads review output)
- `pirategoat-bot/src/github.js` (maps verdicts to GitHub actions)
- `pirategoat-bot/scripts/pr-review.py` (outer-pipeline prompt and verdict logic)

### Linear Issue Pipeline

`scripts/linear/pipeline.py` is a 15-step curated-context pipeline for investigating and fixing Linear issues. The pirategoat-bot spawns a Claude CLI session with `prompts/linear-issue.md`, which calls the pipeline step by step.

**Phases:** SETUP (1-3) → INVESTIGATION (4-8) → IMPLEMENTATION (9-10) → VALIDATION (11-13) → OUTPUT (14-15)

**Clarity gate (step 8).** After investigation completes, step 8 assesses whether the issue has sufficient clarity for implementation. Evaluates 3 hard gates (problem statement, reproduction/scope, success criteria) and 3 soft signals (conflicting signals, missing technical context, implicit assumptions). Produces `clarity-assessment.json`. If any hard gate fails, sets `clarity_blocked` in state → implementation steps 9-14 are skipped → result has `status: "blocked"` and `verdict: "needs_clarification"`.

**Override:** Bot can resume at step 9 by setting `skip_clarity_gate: true` in `run-config.json`. The pipeline checks this flag in `_eval_condition("fix_mode_and_unresolved")`. Step 9 briefing incorporates flagged ambiguities as documented risks when overridden.

**Verdict distinction:** `needs_more_info` = investigation inconclusive. `needs_clarification` = investigation succeeded but issue lacks implementation clarity.

**Cross-repo dependency:** The bot reads `pipeline-result.json` (status, verdict, clarity_gate, clarity_gate_overridden) and `clarity-assessment.json` (summary, questions_for_author) to construct Slack messages. Changes to these schemas must be synced with `pirategoat-bot/src/orchestrator-linear.js` and `pirategoat-bot/src/messages-linear.js`.

### Agent Analysis & Observability

Use the analysis scripts when you need to understand reviewer-agent behavior from raw Claude Code session logs.

**Path convention:** Paths in this section are relative to `plugins/pirategoat-tools/`. If your shell CWD is the repository root, prefix them with `plugins/pirategoat-tools/`.

#### `scripts/analysis/session_analyzer.py`

Parses subagent JSONL logs from Claude Code sessions to extract tool call sequences, categorize behavior patterns, and generate efficiency metrics.

**Usage:**

```bash
# The --sessions-dir value is your project's absolute path with "/" replaced by "-":
# e.g. /Users/alice/code/myproject -> ~/.claude/projects/-Users-alice-code-myproject

# Analyze all patterns-reviewer dispatches from the last 20 sessions
python3 scripts/analysis/session_analyzer.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --agent patterns-reviewer \
    --max-sessions 20

# JSON output for programmatic analysis
python3 scripts/analysis/session_analyzer.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --agent security-reviewer \
    --format json

# Analyze all agents (no --agent filter)
python3 scripts/analysis/session_analyzer.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --max-sessions 5

# Write to file
python3 scripts/analysis/session_analyzer.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --agent patterns-reviewer \
    --output "$TMPDIR/patterns-analysis.txt"
```

**What it extracts per dispatch:**
- Tool call sequence with categorization (git-grep, git-show, git-log, git-diff, bootstrap, file-read-bash, file-list, other)
- Dispatch classification (reviewer vs reconciliator vs crashed)
- File read patterns (unique files, duplicates, most-read files)
- Output file details (Write tool usage, content size, finding counts)
- Aggregate statistics (tool call breakdown, cross-dispatch patterns)

**Output formats:**
- `text` (default) — human-readable report with full tool sequences
- `json` — structured data for downstream analysis

#### `scripts/analysis/session_metrics.py`

General-purpose tool for extracting operational metrics (runtime, model, cache tokens, verdict) from session transcripts. Documented in-file.

#### Session Data Locations

Claude Code stores session transcripts at:

```text
~/.claude/projects/<encoded-project-path>/   # absolute path with "/" replaced by "-"
├── {session-uuid}.jsonl           # Main session transcript
├── {session-uuid}/
│   ├── subagents/
│   │   └── agent-{id}.jsonl       # Subagent logs (one per dispatched agent)
│   └── tool-results/
│       └── {hash}.txt             # Cached tool results
```

Each subagent JSONL file contains one JSON object per line, with the first line being the dispatch message (containing the prompt). Subsequent lines alternate between assistant tool calls and tool results.

## Development Workflows

### Adding a Reviewer Agent

1. Read existing agent `.md` files in `agents/` to understand the format and conventions
2. Create `agents/<agent-name>.md` with the agent definition
3. Add entry to `scripts/review/agent_registry.json` — choose domain, protocols, dispatch class, model tier, and (if conditional) triage criteria
4. For conditional agents: add one probe per `triage_criteria` bullet to `tests/review/test_criteria_coverage.py` (the completeness meta-test fails until you do) and make each probe dispatch — this is where you discover whether your keywords/checks actually back your criteria
5. Add agent to `.claude-plugin/marketplace.json` in the `agents` array
6. Run tests: `pytest plugins/pirategoat-tools/tests/ -v` (parameterized tests auto-include new agents)

### Adding a Command

1. Read existing commands in `commands/` to understand the dispatch pattern
2. Create `commands/<command-name>.md` — commands are orchestrators that invoke agents via the `/Agent` tool
3. Use `review/plan_dispatch.py` for triage decisions (don't duplicate triage logic)
4. Add command to `.claude-plugin/marketplace.json` in the `commands` array
5. Add structural tests in `tests/commands/test_commands.py` (new `TestXxx` class)
6. Run tests: `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v`
7. **Update all docs** — see [Doc Update Checklist](#doc-update-checklist-for-new-commands-skills-or-agents) below

### Adding a Skill

1. Create `skills/<skill-name>/SKILL.md` with YAML frontmatter (`name`, `description`) and Markdown body
2. Add skill to `.claude-plugin/marketplace.json` in the `skills` array
3. **Update all docs** — see [Doc Update Checklist](#doc-update-checklist-for-new-commands-skills-or-agents) below

### Expected Failures

These are normal — handle them, do not stop or apologize:

- **`review/agent/scope.py` returns empty scope**: The diff has no files matching this agent's domain. Skip the agent — this is correct triage behavior.
- **Tests fail after your changes**: Read the failure output, fix the root cause, and re-run. Test failures are feedback, not errors.
- **`review/agent/bootstrap.py` can't find plugin root**: Ensure you are running from within the repository. The script walks up from CWD looking for `.claude-plugin/`.

### Testing

**Always run tests after modifying scripts, agents, or commands.** See the root `AGENTS.md` [Testing > pirategoat-tools](#pirategoat-tools) section for the full test lookup table, test principles, and agent compliance eval commands.

**Subprocess tests must isolate from the real repo.** Tests that invoke pipeline scripts via `subprocess.run()` MUST pass `cwd=tmp_path` (with a temp git repo) so git-mutating scripts can't stash, checkout, or reset the real working tree. See [learning](../../.claude/docs/learnings/2026-03-19-isolate-subprocess-tests-from-real-repo.md).

### Doc Update Checklist for New Commands, Skills, or Agents

**Every new command, skill, or agent requires updates in all four locations below.** Do not skip — stale counts and missing entries make the plugin inventory unreliable.

| # | File | What to update |
|---|------|----------------|
| 1 | `.claude-plugin/marketplace.json` | Add entry to the plugin's `commands`, `skills`, or `agents` array |
| 2 | `plugins/pirategoat-tools/README.md` | Update count in directory tree + add row to the relevant table |
| 3 | Root `AGENTS.md` → Plugin Inventory → pirategoat-tools | Update summary count + add to the `commands/`/`skills/`/`agents/` contents row |
| 4 | Root `README.md` | Update count in directory tree (e.g., "19 agents, 19 skills, 7 commands") |
