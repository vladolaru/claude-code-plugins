# pirategoat-tools — Agent Instructions

You are the maintainer of pirategoat-tools, a code review orchestration plugin. You dispatch domain-specific reviewer agents in parallel, reconcile their findings through semantic deduplication and verification, then stress-test conclusions via an independent decision critic.

## Key Files

**IMPORTANT: `scripts/review/agent_registry.json` is the single source of truth for all reviewer agent configuration.** Every agent change starts and ends here.

| File | Role |
|------|------|
| `scripts/review/pipeline.py` | Unified 12-step review pipeline. Owns step sequence, routing, state management, host-specific orchestration wording, and curated briefings. Called by all three review commands with `--mode pr\|full\|incremental` and generated Codex adapters with `--host codex`. |
| `../../scripts/generate_codex_compat.py` | Repository-level generator that converts canonical Claude Code commands into Codex command-skill adapters and emits this plugin's `.codex-plugin/plugin.json`. |
| `scripts/review/agent_registry.json` | Agent registry — domain, protocols, dispatch class, triage criteria, model tier. |
| `scripts/review/agent/bootstrap.py` | Builds the structured prompt each agent receives. Handles plugin root discovery, protocol extraction, scope discovery, and output instructions. When a primary domain matches nothing but a secondary domain does, `resolve_overall_status` flips the status to a scoped `OK` and injects a `COVERAGE NOTE` so the agent reviews the secondary files with an honestly-scoped verdict instead of silently masking the gap. |
| `scripts/review/agent/scope.py` | Efficient diff scoping. Filters changes by domain (security, performance, php-tests, etc.) and outputs structured STATUS/FILES/STATS/DIFFS sections. **Language recognition lives in one place:** the `_PROG_LANGS`/`_STYLE_LANGS`/`_QUERY_LANGS`/`_DOC_LANGS`/`_DATA_LANGS`/`_FRONTEND_LANGS` groups, plus `_MIXED_MARKUP_LANGS`, `_TEMPLATE_LANGS`, and `_TEMPLATE_SUFFIXES` for rendered UI. Domains compose extensions via `_ext_re(...)`; `is_template_file()` distinguishes pure and compound templates for a11y dispatch and budget priority. Add formats to these sources once — never edit per-domain regexes. Budget priority tiers (`production_first`, `markup_evidence`) order files before largest-first budgeting; one oversized leading diff is protected outside the ordinary pool, and `--summary-json-out` persists per-agent scope summaries for run-level coverage accounting. |
| `scripts/review/plan_dispatch.py` | Deterministic dispatch planning. Reads agent registry + changed files → produces which agents to run, skip, and why. Called internally by review/pipeline.py. Also runs the unrecognized-source safety net (`detect_unrecognized_source`) that emits a `warnings[]` entry when a changed source language no domain covers — so coverage gaps fail loudly instead of producing a clean review. |
| `scripts/review/dispatch_status.py` | Canonical producer/consumer dispatch-status vocabulary and dispatch-plan agent validator. Consumers classify dispatched and skipped states only through its explicit sets; hand-edited invalid statuses fail with the offending agent and value. |
| `scripts/review/context.py` | Unified Ring 1 context collection. Fills git context, PR metadata, reviews, linked issues, staleness, and author name. `--refresh-host-context` re-runs only host-context discovery against the existing review-context.json (used after a trusted-branch dependency refresh). |
| `scripts/review/dependency_refresh.py` | Deterministic stale-dependency-root and clean-tracked-baseline detection for trusted-branch refresh (opt-in `--refresh-deps`). Side-effect free: signals composer/npm/pnpm/yarn roots whose manifest/lockfile changed in range or whose installed state is missing, bounded to repo root + directories containing changed manifest files, then refuses refresh when tracked state is dirty or cannot be inspected. Execution belongs to the step 3 briefing, never this module. |
| `scripts/review/user_settings.py` | Requester-side machine-local settings (`~/.config/pirategoat/config.json` / `$XDG_CONFIG_HOME`). Owns the standing trust declaration `review.refresh_dependencies: true` that defaults trusted-branch refresh on for every interactive run. Deliberately separate from the reviewed repo's `.pirategoat/config.json`: trust is the requester's to declare, never the repo's. |
| `scripts/review/agent/output.py` | ReviewOutputBuilder — `add_issue()`, `add_recommendation()`, `add_positive()`, `add_unreviewed()` (declared budget-omission coverage gaps, verified against the bootstrap-written `<reviewer>-deferred-files.json` sidecar when present so an unmatched declaration fails loudly instead of inverting into a reviewed claim), verdict calculation, JSON/Markdown serialization. |
| `scripts/review/reconciliation_context.py` | Pre-gathers agent findings, source snippets, scope annotations into a single context. Produces both JSON (`reconciliation-context.json`) and Markdown (`reconciliation-context.md`) via `to_markdown()`. The reconciliator reads the Markdown version (~40% more token-efficient). Called by pipeline step 8. |
| `scripts/review/telemetry.py` | JSONL telemetry logging. `ReviewTelemetry` class captures pipeline timing, agent start/complete lifecycle, snapshots, and summaries. |
| `scripts/review/manifest_sections.py` | Pure builders for dispatch, coverage, and dependency-refresh sections in durable review manifests. |
| `scripts/containment.py` | Single implementation for pipeline repo-boundary decisions. Filesystem-resolved callers and telemetry's POSIX-only lexical caller keep their own failure policy while sharing the containment decision. |
| `scripts/git_paths.py` | Single grammar implementation for Git C-quoted paths. Review-config provenance, telemetry, and scoped-diff parsing keep their caller-specific failure policies while sharing escape and octal decoding. |
| `agents/shared/reviewer-protocol.md` | Shared behavioral rules for all reviewer agents. Bootstrap extracts sections via skip-list. |
| `agents/shared/tests-reviewer-protocol.md` | Additional rules for test reviewer agents (test quality principles, anti-patterns). |
| `schemas/review-output.ts` | TypeScript type definitions for structured review output (Issue, SecurityIssue, PerformanceIssue, etc.). |
| `scripts/iterative_review/` | Iterative review loop sub-module. Multi-round independent review (Codex primary, Claude Code fallback) with pushback tracking, convergence detection, noise-filtered diff sizing, and telemetry. CLI entry point: `python3 -m iterative_review --action review\|advance [--autonomous]`. |
| `scripts/linear/pipeline.py` | 15-step curated-context pipeline for investigating and fixing Linear issues. Owns step sequence, routing, state management, and curated briefings. Called by pirategoat-bot via `--step N --mode investigate\|fix`. |
| `scripts/linear/events.py` | Best-effort JSONL event emission for pipeline progress (step_started, milestone, deliverable, pipeline_complete). Used by both review and linear issue pipelines. |
| `scripts/hosts/host_context.py` | CLI entrypoint for upstream-host discovery. Runs the resolver chain and writes `host-context.json` under `--output-dir`. Invoked standalone or via `review/context.py`. |
| `scripts/hosts/chain.py` | Composes repo-signaled advisory resolvers in priority order (explicit → wp-env → docker-compose → plugin-headers → vendor), dedups by `kind:name`, conditionally invokes ecosystem-cache fulfillment for unresolved WordPress/WooCommerce signals, and generates the degradation banner. The sibling resolver remains a standalone non-default helper. |
| `scripts/hosts/resolvers/` | Individual resolver implementations. Each reads local filesystem signals and emits `HostEntry` records without side effects. |
| `scripts/hosts/ecosystem_cache.py` | Machine-wide ecosystem source cache management (WordPress + WooCommerce). `--update` / `--list` / `--verify`. |
| `scripts/hosts/cache/` | Internal ecosystem-cache manager (`manager.py`): clone / git-pull / verify-staleness for WordPress + WooCommerce. |

## Architecture

### Dual-Host Contract

The command files under `commands/` are canonical. Their generated Codex
adapters live in same-named directories under `codex-skills/` and are marked
`GENERATED FILE - DO NOT EDIT`. Never edit those adapters directly. Keeping
them outside the top-level `skills/` directory prevents Claude Code from
discovering each canonical command a second time.

The generator also surfaces **shared skills that a command depends on**. When a
command body references a `skills/<name>` skill by name, the generator emits a
host-translated copy at `codex-skills/<name>/SKILL.md` (source-marked
`./skills/<name>`) plus verbatim copies of the skill's sibling assets (e.g.
`references/`) it reads via `$SKILL_DIR`, because Codex only loads
`codex-skills/`, not the canonical `skills/` tree. Skills no command references
(e.g. pirategoat's reference library) are not surfaced. Fix such skills in
`skills/`, never in the generated copy.

Because Codex does not export `CODEX_PLUGIN_ROOT` into the shell, the generator
prepends an explicit `CODEX_PLUGIN_ROOT="…"` assignment to any generated `bash`
block that references it. Keep canonical commands using `${CLAUDE_PLUGIN_ROOT}`
(which Claude Code exports) and let the generator handle the Codex form; do not
hand-assign it in canonical commands.

The review pipeline defaults to Claude Code behavior and persists
`--host codex` when selected by a generated adapter. Codex briefings dispatch native
parallel subagents and tell each one to read the canonical `agents/*.md`
definition before running bootstrap. This intentionally shares reviewer
prompts without mapping Claude model labels to a different host.

Shared skills use `$SKILL_DIR` for their own resource paths. Define it as the
absolute directory containing the loaded `SKILL.md`; do not introduce
`${CLAUDE_SKILL_DIR}` in shared skill prose.

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

### Pipeline-Wide Containment

`scripts/containment.py` is the single enforcement point for repo-boundary
decisions across the plugin. Advisory host resolvers use `contains()` to avoid
presenting first-party code as an independent runtime host. Repo-contributed
review configuration uses the same resolved-path primitive before reading rule
or reviewer instructions that may execute with real tools.

Telemetry is the deliberate lexical caller: `contains_posix_lexically()`
canonicalizes recorded measurement paths with POSIX grammar, without resolving
symlinks or touching paths that may no longer exist. The OS-native
`contains_lexically()` remains available only for bounding walks. Neither
lexical primitive may authorize a filesystem read or an execution.

`tests/test_containment_contract.py` preserves the symlink and prefix behavior
and scans every Python file under `scripts/` for the unambiguous containment
spellings (`commonpath`, `is_relative_to`, `commonprefix`). Only the exact shared
module is exempt — do not add inline containment checks or an allowlist.

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

### Trusted-Branch Dependency Refresh (opt-in)

The pipeline never installs dependencies itself (1.113.0 removed
manifest-driven installation — package managers execute configuration as
code). When the requester opts in — per run with `--refresh-deps`, or as a
standing machine-local declaration in `~/.config/pirategoat/config.json`
(`{"review": {"refresh_dependencies": true}}`, resolved by
`user_settings.py`) — the pipeline instead lets the **main orchestrator**
refresh the worktree, because opting in means the requester trusts the
branch enough to execute its code. Resolution: an explicit
`--refresh-deps`/`--no-refresh-deps` wins; an omitted flag falls back to
the machine-local default; the effective value lands in run-config.json as
`refresh_dependencies`. The standing declaration covers every interactive
run the requester starts — all modes, all clones — which includes
interactive PR reviews of third-party branches; that is the requester's
explicit trust decision, made in a file the reviewed repo can never touch.

Split of responsibilities:

- **Deterministic detection** (`scripts/review/dependency_refresh.py`, run by
  step 3 orchestration): signals dependency roots whose manifest/lockfile
  changed in the reviewed range or whose installed state is missing, then
  requires a clean tracked worktree before offering any install actions.
  `git status --porcelain --untracked-files=no` ignores untracked files but
  retains tracked submodule changes. Dirty state records `dirty_worktree` with
  bounded path evidence; a failed, timed-out, nonzero, or undecodable status
  check fails closed as `worktree_status_failed`. Both skip states preserve
  the stale-root signals and proceed with degraded host context. A broader
  detection failure still records `detection_failed` — staleness is unknown,
  never silently clean.
- **Adaptive execution** (step 3 briefing, only after the clean-baseline
  precondition): the orchestrator runs the suggested install commands
  (`composer install`, `npm ci`, `pnpm install --frozen-lockfile`, `yarn
  install --immutable`), checks for tracked-file changes, restores only
  install-created tracked changes from the known-clean baseline, then
  re-resolves host context with `context.py --refresh-host-context` and writes
  `dependency-refresh.json` (a step 3 handoff gate). The pipeline never
  stashes, reapplies, or otherwise takes custody of the requester's
  uncommitted work.
- **Measurement** (`telemetry.py`): the manifest records the sanitized
  report under `dependency_refresh`. Refused refreshes carry explicit
  `skipped` provenance and no `verification` block — a run reviewed against
  freshly installed dependencies is not comparable to one with degraded host
  context.

Execution governance (requester-trusted, clean-baseline enforced; updated
2026-08-10): requester opt-in is the execution trust boundary, while the
deterministic clean-worktree gate is the custody boundary. If tracked changes
exist, the requester decides whether to commit or stash them and rerun; the
pipeline does not touch them. When installs do run, the orchestrator performs
them adaptively. At step 5, the pipeline records post-hoc evidence: it validates
the command strings in the self-report against its install-command allowlist
and independently observes tracked Git dirtiness with `git status --porcelain
--untracked-files=no`, recording a `verification` block beside the self-report
in the manifest. A refused refresh skips verification and records the refusal
instead. Neither post-hoc check attests which commands actually executed. A
missing report leaves command evidence unknown without marking verification
itself failed, and validation failures do not block dispatch. Suggested
commands carry script-blocking flags as defense-in-depth, not as a guarantee
that package-manager execution is safe: `.pnpmfile.cjs` survives
`--ignore-scripts`.

**Hard-off for bots.** `refresh_dependencies` is interactive-only: step 1
forces it off (with a stderr warning) for `interactive: false` runs whether
it arrived via CLI or a pre-seeded run-config.json. A bot reviewing
third-party PRs must never execute reviewed-branch code. The adaptive
orchestrator solves the *variability* problem (which manager, which
commands, monorepos); the opt-in gate — and only the gate — solves the
*trust* problem. The deterministic clean-baseline gate separately ensures the
pipeline never takes custody of uncommitted tracked work.

### Shared Protocols

**reviewer-protocol.md** provides behavioral rules for all agents. Bootstrap extracts it via a **skip-list** — sections the bootstrap already handles are excluded, everything else is included automatically. New sections added to the protocol are picked up without code changes.

Skip-list (sections bootstrap replaces with concrete values):
- `## Step 0` (plugin root — bootstrap resolved it)
- `## Scope Discovery` (bootstrap ran review/agent/scope.py)
- `## Output Directory` (bootstrap resolved to concrete path)
- `## ReviewOutputBuilder API` (bootstrap provides pre-filled snippet)
- `## File-Based Output` (bootstrap provides concrete file paths)

**RULE: Never put behavioral policy in a skipped section.** These sections are stripped before any reviewer sees them, so text added there is inert — it will pass review, ship, and appear in the changelog while reaching zero agents. 1.108.0 made NOT DIFFED handling mandatory by writing the rule into `## Scope Discovery`; no reviewer ever received it.

The skip-list is for *mechanics bootstrap performs* (running scope.py, resolving paths). Policy about what the agent must do with the result belongs in `build_output()`, which also knows the concrete budget and file paths. `TestNotDiffedContractIsDelivered` in `tests/review/agent/test_bootstrap_integration.py` guards this for the NOT DIFFED contract — extend it when you add a comparable contract.

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
- The synthetic name MUST end in `-reviewer`, and every downstream site maps agent names to
  review-file stems by stripping ONLY the trailing `-reviewer` (never a blanket replace —
  repo ids may carry "reviewer" mid-string, e.g. `api-reviewer-v2`).
- Ref-mode derives the reviewer name and `.started` marker from `--instance-name`, not the
  shared adapter key, so N adapter instances never clobber one output file.
- **Advisory channel:** a reviewer/rule with `"channel": "advisory"` produces findings that
  are listed but NEVER gate the verdict. `add_issue(..., channel="advisory")` is skipped in
  `_calculate_verdict`. Native agents never set `channel`, so this is backward-compatible;
  `reconciliation_context.py` surfaces it and the reconciliator preserves it. Bootstrap's
  rules render instructs native reviewers to tag advisory-rule-derived findings.
- **Provenance gate (security boundary):** the adapter EXECUTES repo prompt text with real
  tools, so `load_review_config` excludes any rule/reviewer whose defining file — or
  `.pirategoat/config.json` itself — is added or modified within the reviewed range
  (PR-controlled text is not repo-owner-approved content). The changed-file match covers
  both spellings of Git-C-quoted names AND each declaration's symlink-resolved target,
  compares canonical identities (casefolded, NFC — case-insensitive/normalization-
  insensitive filesystems open the same file through either spelling), and treats a
  changed path as tainting everything beneath it (a submodule update is reported as its
  gitlink root, not the files inside), so neither encoding, an in-repo symlink, a case
  variant, nor an updated submodule can slip PR text past the gate. Exclusions
  are hard (never dispatchable, reported under `untrusted` and carried in the plan's
  `warnings` — the only channel the step-5 briefing renders), and an unknown changed-file
  set fails closed. To test an unmerged reviewer deliberately, dispatch the adapter
  manually via bootstrap ref-mode.
- **Path scoping:** a reviewer whose `applies_to.paths` matched dispatches AND receives
  those files in scope — bootstrap ref-mode passes the declared globs to scope.py as
  `--include-path` so the dispatch gate and the scope never disagree.

**Execution:** inline only in v1 (the adapter reads and runs the repo prompt in-context).
`isolated` is NOT implemented: plan_dispatch refuses to dispatch it and bootstrap exits
with an error — an explicit isolation request must never silently widen into inline
execution.

## Output Contract

Each reviewer agent publishes one file in `OUTPUT_DIR`:

- `<reviewer>-review.json` — the canonical artifact: structured findings written via `builder.save()` (see `schemas/review-output.ts` for types)

The human-readable `<reviewer>-review.md` is derived from the JSON, not written by reviewers — reconciliation materializes it for humans, and it is renderable on demand via `python3 scripts/review/agent/output.py render|materialize`.

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

#### `scripts/analysis/review_run_metrics.py`

The supported review-pipeline run/cohort interface. It prefers durable `*.manifest.json` telemetry sidecars, falls back to privacy-reduced legacy JSONL records, and can enrich an exact run from Claude transcripts without weakening the pipeline-native measurements when transcripts are unavailable.

This path is a thin CLI entry point; the implementation lives in the `scripts/analysis/review_metrics/` package. Imports flow one way only — edit within this layering, never against it:

```text
contracts -> sanitize -> usage -> load -> {measure, cohort} -> render -> cli
```

| Module | Owns |
|---|---|
| `contracts.py` | External contract loading (telemetry, dispatch_status), shared constants, `_parse_time` |
| `sanitize.py` | Field-level sanitizers and strict validators |
| `usage.py` | Token-usage accumulation primitives |
| `load.py` | Manifest/JSONL discovery, lifecycle overlay, `load_runs` |
| `measure.py` | Per-run measurement and transcript enrichment |
| `cohort.py` | Cross-run aggregation |
| `render.py` | Table and JSON rendering |
| `cli.py` | Argument parsing and `main` |

```bash
python3 scripts/analysis/review_run_metrics.py --last 30
python3 scripts/analysis/review_run_metrics.py --last 30 --format json --output "$TMPDIR/review-runs.json"
python3 scripts/analysis/review_run_metrics.py --run-id <run-id> --no-transcripts
```

**Transcript enrichment is bounded to explicit queries.** Enrichment costs one session discovery plus a full transcript parse *per run*, so an unbounded sweep would pay it across all history. A query without `--last` or `--run-id` reports the transcript family as explicitly `disabled` and prints how to enable it. The cohort itself is never silently truncated — full-history sweeps remain the tool's contract.

**Local-output warning:** The stable JSON report is local operational output, not an anonymized or share-safe export. It intentionally retains `repo_path`, `output_dir`, `session_id`, Git range/SHA identifiers, and free-form main-orchestrator adjustment reasons because they are measurement evidence. Sanitize or redact generated JSON before sharing it outside the local trusted context.

**Measurement contract:**

- Telemetry/manifest fields are authoritative for run identity, deterministic planner versus main-orchestrator adjustments, generated-scope coverage, lifecycle, outcomes, critic verdict, and wall time.
- There are no human overrides in this flow. Deterministic planning runs first; the main orchestrator may then add or skip agents and supplies the adjustment reasons.
- Lifecycle `agents.incomplete` is a deterministic sorted multiset with one repeated agent name per unmatched start execution. `incomplete_count` measures executions, `incomplete_identities` contains unique sorted names, and `incomplete_by_agent` preserves per-agent multiplicity. Complete manifests require the exact start-minus-completion multiset and suppress sibling overlays. Running manifests remain partial; the consumer may overlay only a strictly validated same-run JSONL lifecycle suffix after proving the sidecar arrays are exact causal prefixes, and must reduce fresh events without retaining raw prose or scope paths. Malformed, foreign, prefix-inconsistent, or chronologically invalid siblings fail closed for lifecycle only.
- Dispatch `adjustment_rate` measures changed agents over the full compared-agent union; `planner_removal_rate` measures removed agents over planner-dispatched candidates for comparable runs. Wall durations above one year are treated as implausible missing data.
- Valid plans with different agent identity sets disable adjustment comparison and carry only sorted identity-to-status projections. Ingestion must rederive both dispatch counts from those projections, require exact mismatch metadata, and fail malformed or unexpected projections closed for the dispatch family without retaining plan prose.
- Transcript correlation is optional and exact: session ID + output directory + recognized reviewer/reconciler/critic identity.
- Every metric family distinguishes complete, partial, missing, and disabled data. Missing data is never reported as a measured zero, and partial observations never enter complete denominators.
- **Legacy reconstruction is frozen.** Review-run legacy segments and identityless-segment recovery are best-effort overall; their independent availability families remain the reporting boundary and may be complete, partial, missing, or disabled per family. Source-level builder reconstruction in `session_analyzer.py` is likewise frozen best-effort inference—even though it recognizes the current canonical heredoc—and its local ad hoc quality output has no availability-family labels. A new inference-precision edge is a known limitation, not another hardening round; crashes, privacy/safety failures, or contamination through foreign run, agent, or artifact identity confusion remain bugs, and new precision belongs in producers—manifests, sidecars, and shared contracts—where durable fixes land.
- Stable structured reports use schema v2. Transcript-derived observed reads require their exact v2 payload; legacy, missing, boolean, or future versions fail closed instead of being interpreted as empty measurements.
- Generated scope is descriptive, not proof of model reads. Observed reads are always non-exhaustive. Only scope-bearing regular reviewer reads enter the `all`/`in_scope`/`out_of_scope` partition; exact `review-reconciliator`, `decision-reviewer`, and `critic` identities — plus scope-exempt domainless reviewers (`tests-mutation-reviewer`), which discover their own scope — route to the separate `non_scope_comparable` bucket. Scope-exempt reviewers stay regular reviewers for builder metrics, while their read-family completeness follows the read routing: damaged scope-exempt evidence degrades the `non_scope_comparable` family it feeds, never the scope-comparable one. Near-match names are regular reviewers.
- Reviewer and synthesis read families carry independent completeness, availability, and cohort denominators. The combined `observed_reads` state is conservative and complete only when both families are complete.
- Every observed-read entry must be one canonical repository-relative path. Absolute, traversal, dot-segment, empty-segment, backslash-separated, drive-prefixed, empty, and control-character paths invalidate the full read payload; normalized Unicode and spaces are preserved.
- Transcript privacy reduction excludes raw prompt bodies, source/finding prose, commands, and tool-result bodies. It does not make the report path-free or identifier-free.

Run `pytest plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py -v` after changing this interface.

#### `scripts/analysis/review_transcript.py`

Lower-level privacy-preserving transcript enrichment used by `review_run_metrics.py`. It correlates the exact main session and run-specific subagents, measures cache-aware usage, safe tool failure/recovery categories, first pipeline-owned Bash attempts, and emits a versioned observed-read payload with independent regular-reviewer and exact synthesis-identity completeness. Reviewer output evidence paired with `builder_attempted: false` means only that the required Bash path was not observed; it does not identify the alternative output mechanism. It must keep completion-notification usage out of totals and must never expose raw prompts, commands, source, findings, or tool-result bodies.

Run `pytest plugins/pirategoat-tools/tests/analysis/test_review_transcript.py -v` after changing its correlation or parsing contract.

**Two settled design questions.** Both have been raised in review and decided; re-open them only with new evidence.

*Why reconstruct identity from transcripts instead of using `SubagentStop` / `PostToolUse` hooks?* The hooks do emit `agent_id`, `agent_type`, `resolvedModel`, and `totalToolUseCount` directly, which would replace the correlation layer (session discovery, run-window bounding, dispatch-prompt parsing, and its four warning codes). They would **not** replace transcript parsing itself: `observed_reads` still requires reading each subagent transcript, so this is roughly a quarter of the module, not all of it. The deciding tradeoff is that hooks only measure runs after install, while the parser reads history — including the historical cohort the budget-utilisation baseline is built on. Correlation failure is already reported explicitly rather than silently dropping agents from denominators, so the current design degrades honestly.

*Why does this module parse session JSONL when `session_analyzer.py` already does?* Their contracts are deliberately different: `session_analyzer.py` retains prose (prompts, commands, categorized text) for human-facing ad-hoc reports, while this module must never expose those bodies. The 2026-08-03 census found that the prior three-reader tally was not a full census: JSONL is read by `review_metrics/load.py::_read_jsonl` (plus its strict variant), `review_transcript.py::_read_jsonl` and `_bounded_jsonl_entries`, `session_analyzer.py`, two sites in `session_metrics.py`, and `telemetry.py::_read_events`, which now counts skipped gaps. Keep these readers separate because their contracts differ across binary/text input, strict/tolerant failure, and report/skip behavior, while the genuinely shared surface remains about 15 lines. Reopen this decision only if a malformed-line-handling fix has to be re-discovered per copy.

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
- Output file details (Write tool usage plus the canonical Bash builder heredoc — recognized and reconstructed from its literal `add_issue()` calls — content size, finding counts)
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
6. Run `python3 scripts/generate_codex_compat.py` from the repository root and commit the generated command-skill adapter
7. Run tests: `pytest plugins/pirategoat-tools/tests/commands/test_commands.py plugins/pirategoat-tools/tests/test_codex_marketplace.py -v`
8. **Update all docs** - see [Doc Update Checklist](#doc-update-checklist-for-new-commands-skills-or-agents) below

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

**Every new command, skill, or agent requires updates in all five locations below.** Do not skip - stale counts and missing entries make the plugin inventory unreliable.

| # | File | What to update |
|---|------|----------------|
| 1 | `.claude-plugin/marketplace.json` | Add entry to the plugin's `commands`, `skills`, or `agents` array |
| 2 | `plugins/pirategoat-tools/README.md` | Update count in directory tree + add row to the relevant table |
| 3 | Root `AGENTS.md` → Plugin Inventory → pirategoat-tools | Update summary count + add to the `commands/`/`skills/`/`agents/` contents row |
| 4 | Root `README.md` | Update count in directory tree (e.g., "19 agents, 19 skills, 7 commands") |
| 5 | Generated Codex outputs | Run `python3 scripts/generate_codex_compat.py` and commit the result |
