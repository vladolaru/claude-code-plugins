# pirategoat-tools — Agent Instructions

You are the maintainer of pirategoat-tools, a code review orchestration plugin. You dispatch domain-specific reviewer agents in parallel, reconcile their findings through semantic deduplication and verification, then stress-test conclusions via an independent decision critic.

## Key Files

**IMPORTANT: `scripts/review/agent_registry.json` is the single source of truth for all reviewer agent configuration.** Every agent change starts and ends here.

| File | Role |
|------|------|
| `scripts/review/pipeline.py` | Executable facade for the unified 12-step review pipeline. Owns conditions, routing, state I/O, output formatting, telemetry/Git identity, and the CLI while re-exporting the split pipeline modules. Called by all three review commands with `--mode pr\|full\|incremental` and generated Codex adapters with `--host codex`. |
| `scripts/review/pipeline_contract.py` | Shared path, host, step-sequence, timeout, and Git vocabulary used across the pipeline modules. |
| `scripts/review/briefings.py` | Pure curated-context guidance, formatters, mission text, and output templates for the 12 review steps. |
| `scripts/review/orchestration.py` | Side-effecting per-step work, subprocess execution, the dependency-refresh safety precheck and adaptive briefing, dispatch-plan persistence, readiness-gated derived-Markdown materialization with outcome state (per-reviewer at step 8, `review-findings.md` at steps 9 and 11), `assemble_review_record()` — the machine projection of the ledger written at steps 9 and 11 — and step 11's two-pass terminal publication gate. |
| `../../scripts/generate_codex_compat.py` | Repository-level generator that converts canonical Claude Code commands into Codex command-skill adapters and emits this plugin's `.codex-plugin/plugin.json`. |
| `scripts/review/agent_registry.json` | Agent registry — domain, protocols, dispatch class, triage criteria, model tier. |
| `scripts/review/agent/bootstrap.py` | Builds the structured prompt each agent receives. Handles plugin root discovery, protocol extraction, scope discovery, and output instructions. When a primary domain matches nothing but a secondary domain does, `resolve_overall_status` flips the status to a scoped `OK` and injects a `COVERAGE NOTE` so the agent reviews the secondary files with an honestly-scoped verdict instead of silently masking the gap. |
| `scripts/review/reviewer_names.py` | Sole implementation of `derive_reviewer_name()` — the trailing-`-reviewer` stripping rule every per-agent artifact name is built from. A leaf module (stdlib only, no imports from elsewhere in `review/`) so any script can import it without risking the import cycle bootstrap used to cause: an earlier version defined this function inside `agent/bootstrap.py` itself, and a second script importing it from there re-entered `bootstrap.py` mid-initialization and silently broke `telemetry.py` loading. 6 importers: `agent/bootstrap.py`, `agents_status.py`, `manifest_sections.py`, `orchestration.py`, `reconciliation_context.py`, and `tests/review/agent/test_bootstrap_integration.py`. |
| `scripts/review/agent/scope.py` | Efficient diff scoping. Filters changes by domain (security, performance, php-tests, etc.) and outputs structured STATUS/FILES/STATS/DIFFS sections. **Language recognition lives in one place:** the `_PROG_LANGS`/`_STYLE_LANGS`/`_QUERY_LANGS`/`_DOC_LANGS`/`_DATA_LANGS`/`_FRONTEND_LANGS` groups, plus `_MIXED_MARKUP_LANGS`, `_TEMPLATE_LANGS`, and `_TEMPLATE_SUFFIXES` for rendered UI. Domains compose extensions via `_ext_re(...)`; `is_template_file()` distinguishes pure and compound templates for a11y dispatch and budget priority. **One domain looks past the extension:** a11y scope runs `filter_a11y_ui_evidence()` on bare `.js`/`.mjs`/`.cjs`/`.ts` files (never `.tsx`/`.jsx`/`.vue`/`.svelte`, whose extension IS the evidence), keeping them only when the change's own hunk or a bounded read of the file shows UI evidence — a backend-only server module in a full-stack monorepo is otherwise pure budget waste. Deliberately a11y-specific, not a per-domain config key; generalize when a second domain has the problem. Triage is untouched. Add formats to these sources once — never edit per-domain regexes. Budget priority tiers (`production_first`, `markup_evidence`) order files before largest-first budgeting; one oversized leading diff is protected outside the ordinary pool, and `--summary-json-out` persists per-agent scope summaries for the run-level file review. |
| `scripts/review/plan_dispatch.py` | Deterministic dispatch planning. Reads agent registry + changed files → produces which agents to run, skip, and why. Called internally by review/orchestration.py. Also runs the unrecognized-source safety net (`detect_unrecognized_source`) that emits a `warnings[]` entry when a changed source language no domain covers — so coverage gaps fail loudly instead of producing a clean review. |
| `scripts/review/dispatch_status.py` | Canonical producer/consumer dispatch-status vocabulary and dispatch-plan agent validator. Consumers classify dispatched and skipped states only through its explicit sets; hand-edited invalid statuses fail with the offending agent and value. |
| `scripts/review/context.py` | Unified Ring 1 context collection. Fills git context, PR metadata, reviews, linked issues, staleness, and author name. `--refresh-host-context` re-runs only host-context discovery against the existing review-context.json (used after a trusted-branch dependency refresh). |
| `scripts/review/dependency_refresh.py` | The validating save channel for trusted-branch dependency refresh: one bounded tracked-Git observation, strict schema-1 request and canonical validators, and atomic publication of `dependency-refresh.json`. The interactive orchestrator decides whether refresh work is needed and what commands to run; reported commands are evidence, not execution attestation. |
| `scripts/review/user_settings.py` | Requester-side machine-local settings (`~/.config/pirategoat/config.json` / `$XDG_CONFIG_HOME`). Owns the standing trust declaration `review.refresh_dependencies: true` that defaults trusted-branch refresh on for every interactive run. Deliberately separate from the reviewed repo's `.pirategoat/config.json`: trust is the requester's to declare, never the repo's. |
| `scripts/review/agent/output.py` | ReviewOutputBuilder — `open()`, stable `fN` finding and `cN` check mutation, observations, positives, recommendations, reviewed-file claims, synthesis-only assessment, and whole-state `save_draft()`. Every draft replacement derives the six canonical top-level reviewed-file fields through `derive_reviewed_files()` and the required `<reviewer>-assignment.json`; the draft becomes immutable final output only through the exact printed `FINALIZE REVIEW` command, and only `REVIEW FINALIZED` marks completion. Also owns verdict calculation, final JSON publication, the public `validate_review_document()` / `load_review_document()` authority every final-review content reader uses, and the derived Markdown `render\|materialize` CLI. `review_duration_ms` is derived from the actor's dispatch marker (`<agent>.started` for reviewers, `<agent>.synthesis-started` for synthesis agents), null when no marker is readable. |
| `scripts/review/critic.py` | The decision critic's validating `--save` channel. Accepts proposal-only fields, delegates normalization and stable ID allocation to `critic_adjustments.prepare_proposal()`, and commits findings plus the proposal under the shared output-directory lock by writing the schema-versioned, proposal-digest-bound verdict marker last. |
| `scripts/review/critic_adjustments.py` | Owns the critic lifecycle after proposal authorship: `write_critic_verdict()` is the one writer of the proposal and its digest-bound marker; `adjudicate()` validates the orchestrator's verified/refuted ids under the output lock, applies the proposal to the ledger once, and records each outcome in `applied_critic_adjustments` / `rejected_critic_adjustments`; `adjudication_state()` tells step 11 whether a REVISE proposal landed. Also owns `validate_findings_document()`, `read_findings_file()`, and `write_findings()` for the ledger. |
| `scripts/review/findings_ledger.py` | `FindingsLedgerBuilder` — the reconciliator's builder: review content plus its four concern counts; no reviewer identity and no reviewed files. `findings_save.py` stamps the pipeline-owned reconciliation facts from `reconciliation-context.json` at save. |
| `scripts/review/verdict_rules.py` | `verdict_for_counts()` — the ONE place the severity-to-verdict thresholds live (critical → `block`; 3+ high → `block`; high or 5+ medium → `request_changes`; medium → `comment`; else `approve`). Shared by `agent/output.py` (publishing any reviewer's or the reconciliator's verdict) and `critic_adjustments.py` (recomputing the ledger verdict after an applying critic batch changes severities), so the ladder can never drift into two copies. |
| `scripts/review/findings_save.py` | The reconciliator's validating save channel for `review-findings.json` — the sibling of `critic.py --save`. It reads the run's `reconciliation-context.json` and stamps every pipeline-owned `meta.reconciliation` fact (plus the degraded-host banner) onto the ledger, leaving the agent only its four judgment counts; it accepts the exact schema-3 findings/checks/assessment contract, rejects malformed shapes, retired fields, critic-owned fields, agent-authored pipeline fields, bad verdicts, and summary/finding-count mismatches with nothing written; on success it calls `critic_adjustments.write_findings()` itself (adding no new writer) and echoes the recorded verdict, finding count, and check count. This is the ONLY channel `agents/review-reconciliator.md` is allowed to write the ledger through — a raw `json.dump` or direct `atomic_write_json` closes the gap this module exists to guard. |
| `scripts/review/atomic_io.py` | Single implementation of the pipeline's atomic-JSON-write convention and `output_dir_lock()` convention. `critic.py --save` and `critic_adjustments.adjudicate()` share this directory lock so publication and adjudication never observe one another halfway through; critic code reuses this primitive and never imports reviewer lifecycle concepts. One forbidden direct use: `review-findings.json` may never be written with a bare `atomic_write_json` — it goes through `critic_adjustments.write_findings(output_dir, findings)`. |
| `scripts/review/reconciliation_context.py` | Pre-gathers agent findings, source snippets, and scope annotations into `reconciliation-context.json` — the reconciliator's single input; it carries only what that agent reads. Also owns `aggregate_file_review()`, the run-level file review the reconciliator never used: pipeline step 9 calls it directly for the review record, so the measurement has one producer and no stale copy in the context. It writes no Markdown: the two projections it used to render (`reconciliation-context.md` for the reconciliator, `critic-context.md` via `build_critic_context()` for the decision critic) each had exactly one reader, and that reader was an agent. **Retiring those renderers must not demote what they computed.** Two deterministic facts they produced stay machine-side and travel in the JSON: `compute_missing_agents()` (dispatched minus reporting, `null` when dispatch is unknown) seeds `meta.reconciliation.missing_agents`, and `annotate_prefiltered_findings()` marks every structurally-certain out-of-scope finding in place with `prefiltered` plus a checkable `prefiltered_out_of_scope` count. The reconciliator carries and obeys both; it recomputes neither. Called by pipeline step 8 after per-reviewer Markdown materialization; it does not render human-facing artifacts. Still owns `strip_severity_floor_markers()`, which the review-record assembler applies before rendering. |
| `scripts/review/telemetry.py` | JSONL telemetry logging. `ReviewTelemetry` class captures pipeline timing, agent start/complete lifecycle, snapshots, and summaries. |
| `scripts/review/synthesis_lifecycle.py` | Lifecycle measurement for the two SYNTHESIS agents — the review-reconciliator (step 8) and the decision critic (step 10). They never run `agent/bootstrap.py`, never write a `<agent>-review.json`, and are never in `dispatch-plan.json`, so `agents_status.py` structurally cannot see them. Steps 8 and 10 call `mark_dispatched()` at handoff, writing `<agent>.synthesis-started` — bootstrap's marker BODY (one aware UTC ISO timestamp) under a deliberately different NAME, derived from the single `MARKER_SUFFIX` constant that both the writer and the reader resolve through. The suffix is namespacing, not decoration: the reviewer `*.started` suffix is a contract other tools scan, and pirategoat-bot's resume path treated every hit as a reviewer — seeding both synthesis agents as permanently NOT_DISPATCHED and renaming their markers away as orphans, erasing the stall signal in the one window where the marker is the only record of a dispatch. A hand-maintained name list in another repo is a contract nobody enforces; the suffix is one nobody has to, and a third synthesis agent cannot reintroduce the collision. Steps 9 and 11 call `observe()`, which keys completion on the artifact each step's handoff gate makes mandatory — `review-findings.json` and `decision-critic-verdict.json`. Every row carries ONE clock: `completed_at` is the artifact's mtime and `duration_ms` the span from dispatch to it. The observation time is deliberately not recorded — the run's own step cadence bounds the lag, and a second number nobody queried was trimmed before release. Quick mode commits the pipeline's own `SKIPPED` verdict without a dispatch marker, so it creates no lifecycle row; a dispatched critic with no usable verdict retains its marker, records `stalled: true` at finalize, and makes the run unavailable/degraded. Report, never kill: lifecycle measurement never interrupts an agent. **Every step that re-enters after a handoff observes BEFORE it does anything else**, including before step 10 re-stamps its marker — step 10 is genuinely re-entered after a completed critic, and a bare re-stamp there publishes a finished critique as a zero-length stall. `ROW_KEYS` is the single declaration of the row shape; the manifest builder and the metrics sanitizer both assert parity against it. **Resume timing:** a resumed run that re-dispatches a synthesis agent keeps the FIRST dispatch's carried-forward timing, because an observation preserves a completed row verbatim and the earliest evidence is the tightest bound. That is the earliest-evidence design working as intended, and it is deliberate pending field evidence — if resumed runs turn out to need the re-dispatch's own span, the fix is a per-attempt record, not a looser carry-forward. |
| `scripts/review/manifest_sections.py` | Pure builders for dispatch, assignment, dependency-refresh, reviewer-Markdown outcome, findings-Markdown outcome, worktree-hygiene, synthesis-agent lifecycle, token-usage, and skipped-steps sections in durable review manifests (`build_skipped_steps_manifest` alongside the rest). |
| `scripts/containment.py` | Single implementation for pipeline repo-boundary decisions. Filesystem-resolved callers and telemetry's POSIX-only lexical caller keep their own failure policy while sharing the containment decision. |
| `scripts/git_paths.py` | Single grammar implementation for Git C-quoted paths. Review-config provenance, telemetry, and scoped-diff parsing keep their caller-specific failure policies while sharing escape and octal decoding. |
| `agents/shared/reviewer-protocol.md` | Shared behavioral rules for all reviewer agents. Bootstrap extracts sections via skip-list. |
| `agents/shared/tests-reviewer-protocol.md` | Additional rules for test reviewer agents (test quality principles, anti-patterns). |
| `schemas/review-output.ts` | TypeScript type definitions for structured review output (`Finding`, `ReviewCheck`, `ReviewDocument`, `FindingsLedger`, `FindingCriticAdjustment`, `CheckCriticAdjustment`, `HostContextBanner`). |
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
      ├─ pipeline_contract.py ← shared host, step, timeout, path, Git vocabulary
      ├─ briefings.py         ← pure get_step_guidance() + step text
      ├─ orchestration.py     ← side-effecting _orchestrate_step() dispatch
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
  ├─ Step 8: review/orchestration.py readiness gate
  │   ├─ Materializes derived <reviewer>-review.md from settled JSONs
  │   └─ review/reconciliation_context.py gathers agent JSONs + source
  │       snippets + scope annotations
  │       → reconciliation-context.json
  │
  ├─ review-reconciliator agent (semantic dedup + scope check + fact verification)
  │   └─ Reads reconciliation-context.json → findings_save.py validates and writes
  │      review-findings.json (the findings ledger)
  │
  ├─ Step 9: the pipeline renders `review-findings.md` from the JSON (same materializer
  │   as step 8), aggregates the run's file review via `aggregate_file_review()`, and
  │   assembles `review-record.md` — its own machine projection of the ledger plus this
  │   run's coverage and run notes. The orchestrator reads the record; it writes nothing
  │   here.
  │
  ├─ decision-reviewer agent (independent stress test)
  │   └─ Reads review-record.md + review-findings.json → critic.py --save
  │      commits findings + proposal + digest-bound STAND/REVISE/ESCALATE marker
  │
  ├─ Step 10 REVISE: the orchestrator probes each proposal entry, then submits its
  │   verified/refuted ids and any revised assessment to `critic_adjustments.py adjudicate`,
  │   which applies the proposal to the ledger in one locked write and records each
  │   entry's outcome (verified/refuted/not_checked) in the ledger itself
  │
  └─ Step 11, pass 1: reads `adjudication_state()` — a REVISE proposal never adjudicated
     is recorded as a degradation rather than applied on the orchestrator's behalf —
     re-renders review-findings.md, re-assembles review-record.md, derives the final
     verdict, and persists prepared state WITHOUT pipeline-result.json; the blocking
     briefing has the orchestrator author review-report.md once from the settled record
     and re-run step 11
      └─ Step 11, pass 2: repeats settlement idempotently, verifies review-report.md,
         atomically publishes pipeline-result.json with that exact report_path, closes
         the handoff, and only then completes the step (bot mode ends; interactive mode
         routes to step 12 as before)
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

### Artifact Schemas

**RULE: an artifact that carries a `schema` field gets that field bumped in the same commit as any change to its shape.** A shape change is a key added, removed, or re-typed. When you make one: bump the producing constant, update `schemas/review-output.ts` if the artifact is declared there, and note the bump in the changelog.

**One carve-out:** a shape change made within the same UNRELEASED version that introduced the current schema number updates the contract in the same commit but does NOT bump — UNLESS a reader must be able to distinguish the old shape from the new one. The number states a compatibility guarantee only once released, so bumping before release publishes a shape no artifact ever had; but a reader that would otherwise silently accept the old shape as if it were the new one (missing required keys, a field whose meaning changed) needs the bump to fail closed instead. Check `git tag` for the plugin's last released version before deciding — if the number's introducing version is already tagged, the carve-out does not apply and you bump regardless. The `<reviewer>-assignment.json` schema went 3 → 4 within 1.114.0's own unreleased window because the new `review_budget`/`channels` keys are required and a schema-3 reader could not tell an old input from a truncated new one. By contrast, telemetry's `EVENT_SCHEMA` and the cohort report's `_REPORT_SCHEMA` stayed at 3 across the same window's key renames: nothing needs to distinguish the renamed keys from what they replaced, so the carve-out's default (update without bumping) applied. A consequence of that default: an in-window manifest key rename (e.g. `reviewed_files_by_agent` replacing `review_claim_accounting_by_agent`) intentionally reads as unavailable on a manifest written before the rename landed rather than resolving to the old key's value, since no schema bump marks the boundary a reader could check against.

The key is always the integer `schema` — never `schema_version`, never a `version` string. Both of those existed and were retired in 1.114.0.

Not every JSON file in a run directory carries one, and this rule does not ask you to add it to them. `pipeline-state.json` and `dispatch-plan.json` carry no `schema` and are read only by this plugin within a single run; the critic's proposal and verdict artifacts do carry schema 2 and appear below. `dependency-refresh.json` and `reconciliation-context.json` cross validating producer/consumer boundaries, so they carry schemas 1 and 3 respectively. `pipeline-result.json` and `run-config.json` carry no `schema` even though pirategoat-bot parses the former and writes the latter (see Cross-Repo Dependency: pirategoat-bot below) — that cross-repo contract is tracked by reading the bot's source before changing either file, not by the schema mechanism. The field earns its place where an artifact **outlives the run that wrote it, or crosses a validating producer/consumer boundary whose reader did not write it** — that is the criterion for deciding whether a new artifact needs one. The families that meet it today:

| Artifact | Schema | Producing authority |
|---|---:|---|
| `<agent>-review.draft.json`, `<agent>-review.json` | 2 | `REVIEW_OUTPUT_SCHEMA` — `scripts/review/agent/output.py` |
| `review-findings.json` (the findings ledger) | 3 | `LEDGER_SCHEMA` — `scripts/review/findings_ledger.py` |
| `review-intake.json` | 2 | `close_review_intake()` — `scripts/review/reviewer_lifecycle.py` |
| Telemetry JSONL events + `<log>.manifest.json` | 3 | `EVENT_SCHEMA` — `scripts/review/telemetry.py` |
| `synthesis-agents.json` | 1 | `LIFECYCLE_SCHEMA` — `scripts/review/synthesis_lifecycle.py` |
| `usage-snapshot.json` | 1 | `SNAPSHOT_SCHEMA` — `scripts/analysis/usage_snapshot.py` |
| `dependency-refresh.json` | 1 | `REPORT_SCHEMA` — `scripts/review/dependency_refresh.py` |
| `observed_reads` payload in transcript enrichment | 2 | `_OBSERVED_READS_SCHEMA` — `scripts/analysis/review_transcript.py`. The same-named constant in `review_metrics/contracts.py` is the *consumer's* expected value, and must be bumped in lockstep |
| `review_run_metrics.py --format json` report | 3 | `_REPORT_SCHEMA` — `scripts/analysis/review_metrics/contracts.py` |
| `<reviewer>-assignment.json` | 4 | `persist_review_assignment()` — `scripts/review/agent/bootstrap.py` |
| `reconciliation-context.json` | 3 | `main()` — `scripts/review/reconciliation_context.py` |
| `decision-critic-adjustments.json`, `decision-critic-verdict.json` | 2 | `ADJUSTMENTS_SCHEMA`, `VERDICT_MARKER_SCHEMA` — `scripts/review/critic_adjustments.py`. The orchestrator's adjudication request to `adjudicate()` (stdin only, never persisted) validates at the same `ADJUDICATION_SCHEMA` value. |
| Per-agent sidecars: worktree baseline / hygiene | 1 | Literal at the write site |
| Per-agent scope summaries | 2 | `write_scope_summary()` — `scripts/review/agent/scope.py` |

**Exception — `review-context.json` and `issue-context.json` carry `version: 1`, and that key is not ours.** pirategoat-bot writes both files and asserts on that field (`src/orchestrator-review.test.js`, `src/orchestrator-linear.test.js`). Renaming it to `schema` would break the bot. Leave it alone.

Readers accept exactly the schema they were written against and route anything else down their unsupported path — never a crash, and never a silent read of fields whose meaning the producer did not vouch for. At validating boundaries, tests pin the exact integer and exercise prior/future integers, numeric strings, missing keys, booleans, and non-object payloads where that boundary accepts external input. Dropping support for an old schema is allowed; reporting a *wrong measurement* for artifacts written under it is not (see `_BUILDER_ENV_REQUIRED` in `scripts/analysis/review_transcript.py` for the shape this takes when the artifact is a transcript).

This rule exists because the review JSONs shipped a `version: "1.0.0"` string that survived six format changes unbumped: a schema number that lags the shape is worse than none, because it states a compatibility guarantee the producer is not honoring.

### Pipeline Briefing Design

The step briefings in `review/briefings.py` follow deliberate design patterns. These are inline rules — see `docs/patterns/curated-context-pipeline.md` for the general principles and rationale behind them.

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
- Steps 3/4, 8, 10, and 11 have `handoff` gates on their output files. Step 9 has none on purpose: it asks the orchestrator for no artifact, and gating on a file the pipeline itself just wrote would be theatre.

Step 11 is intentionally re-entrant. Its first pass records `publication_pending: true`, fingerprints the exact record/ledger bytes plus terminal presentation facts, blocks progress, leaves step 11 out of `completed_steps`, and writes no `pipeline-result.json`; missing `review-report.md` is the expected handoff state, not a degradation. A later pass publishes atomically only when settlement still matches that prepared fingerprint and the report is not byte-identical to one already rejected as stale; a changed source, unchanged stale report, or pre-existing unbound report regenerates the handoff instead of exposing a terminal marker. Prepared guidance may say state is prepared; only the publication pass may call it published or complete. Settlement work repeats idempotently, while step-11-owned degradation records carry stable producer codes across the handoff in first-seen order, retain the first ordinary diagnostic for audit, and project back to the public string-list contract; fingerprints use the ordered identities rather than volatile prose. Malformed or legacy private state is ignored, and generic presentation notes are never an inheritance source. The one mutating measurement, the probe-residue sweep, accumulates removed paths in `worktree-hygiene.json`; its single aggregate record and public note report the cumulative count, while a private hash of the sorted unique path set makes any newly swept path invalidate the prepared report without exposing path provenance.

**Voice.** Senior reviewer briefing the orchestrator — authority on process, trust on execution. The voice lives within the structural section headers (SITUATION / ACTIONS / HANDOFF). The headers themselves stay rigid as machine-readable landmarks.

**Modifying briefings.** Tests check for keywords in briefing text (e.g., `"review-reconciliator" in text`, `"STAND" in text`). When rewriting briefing prose, preserve these keywords. Run the relevant `TestStep*` class after any text change.

### Step 8 Readiness Gate

Before reconciliation, step 8 checks dispatched agents via `review/agents_status.py`. A canonical schema-2 final review is `FINISHED`; an invalid final filename is terminal process evidence (`INVALID_OUTPUT`) but never contributes semantic completion, verdict, finding counts, or reviewed files. `ALL_DONE` means only that nothing remains to wait for, so invalid output does not hang the gate. If agents are still running, step 8 returns a WAITING briefing. Once the gate proceeds, orchestration materializes human-facing `<reviewer>-review.md` files from every settled canonical JSON before building reconciliation context and records the complete/partial/failed outcome in pipeline state and the run manifest. Tracks `first_waiting_at` in pipeline state. If elapsed wait exceeds `agent_timeout_seconds + 60s`, escalates: clears the waiting state and proceeds with reconciliation using available results, instructing the LLM to TaskStop stuck agents first.

### Trusted-Branch Dependency Refresh (opt-in)

The pipeline never installs dependencies itself (1.113.0 removed manifest-driven installation because package managers execute configuration as code). When the requester opts in — per run with `--refresh-deps`, or as a standing machine-local declaration in `~/.config/pirategoat/config.json` (`{"review": {"refresh_dependencies": true}}`, resolved by `user_settings.py`) — the pipeline lets the **main orchestrator** inspect the trusted worktree and refresh dependencies adaptively. An explicit `--refresh-deps`/`--no-refresh-deps` wins; an omitted flag falls back to the machine-local default; the effective value lands in `run-config.json` as `refresh_dependencies`. The standing declaration covers every interactive run the requester starts — all modes, all clones — including interactive PR reviews of third-party branches; that is the requester's explicit trust decision, made in a file the reviewed repo can never touch.

Split of responsibilities:

- **Pipeline config + tracked Git precheck → whether refresh actions may be offered.** Step 3 observes tracked state with `git status --porcelain --untracked-files=no --ignore-submodules=untracked`; untracked files do not make the baseline dirty. A dirty or unknown observation fails closed and offers no dependency commands or save handoff. A clean result allows the adaptive briefing. This gate is separate from the whole-run hygiene baseline and never takes custody of the requester's tracked changes.
- **Main orchestrator → whether and what to run, plus the reported outcome.** After a clean precheck, the orchestrator inspects the repository and reviewed change, decides whether refresh work is needed, chooses appropriate lockfile-preserving commands without a manager or flag allowlist, and refreshes host context after any installation. It writes a schema-1 request under `$TMPDIR`; `not_needed` with an empty command list is the required outcome when inspection finds no work.
- **`dependency_refresh.py save` → schema validation, final Git observation, and atomic publication.** The save command accepts exactly the request schema, records bounded final tracked-state evidence, and publishes the sole canonical `dependency-refresh.json` through `atomic_write_json()`. `completed`, `partial`, `failed`, and dirty or unknown final state are valid evidence. Only invalid request input blocks publication; reported command strings are not parsed and do not attest that execution occurred.

At step 5, the pipeline reads the canonical artifact through `load_dependency_refresh_report()`. A missing or malformed report after a clean precheck, or a report whose final tracked state is dirty or unknown, becomes explicit degraded evidence before dispatch; no verification sidecar or command-policy state exists. The manifest preserves `requested`, `reported`, optional unsafe-precheck evidence, and the validated canonical report fields.

**Hard-off for bots.** `refresh_dependencies` is interactive-only: step 1 forces it off (with a stderr warning) for `interactive: false` runs whether it arrived via CLI or a pre-seeded `run-config.json`. A bot reviewing third-party PRs must never execute reviewed-branch code. The adaptive orchestrator solves the variability problem; the opt-in gate is the execution trust boundary, and the clean tracked-worktree precheck is the custody boundary.

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

**RULE: `build_output()` never re-derives a fact from the `scope_output` text it just rendered.** Every fact it needs (review-claimable-file count, PHP-in-scope, and whatever comes next) must arrive as a required parameter the caller computed from a structured source — `main()`'s scope-facts/telemetry-path machinery, not a regex or string split over rendered output. A rename or reformat of scope.py's rendered text should never be able to silently flip a decision a reviewer's briefing depends on; see `review_claimable_count` and `has_php` for the pattern, and `TestNotDiffedContractIsDelivered`/`TestDynamicDispatchRisk` for the executable contracts.

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
| `model_tier` | yes | `"inherit"` (caller's model), `"sonnet"`, `"opus"`, or `"haiku"`. Match reasoning depth needed. `tests/review/test_registry_docs.py` pins this vocabulary to the registry's actual values in both directions. |
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
  review-file stems through `reviewer_names.derive_reviewer_name()` — the one implementation
  of that stripping rule everything imports, never a blanket `.replace()` restated inline
  (repo ids may carry "reviewer" mid-string, e.g. `api-reviewer-v2`, and only the trailing
  occurrence may go).
- Ref-mode derives the reviewer name and `.started` marker from `--instance-name`, not the
  shared adapter key, so N adapter instances never clobber one output file.
- **Advisory channel:** `add_finding()` accepts only `"blocking"` or `"advisory"`; blocking is
  the default and is normalized to an absent field. Native agents set advisory only for a
  finding caused by a selected advisory repo rule—their own-domain findings omit `channel`.
  The assignment's `channels` field (schema 4) is the authoritative record of which
  channels an effective reviewer identity may use: `["blocking"]` normally, `["blocking",
  "advisory"]` when that reviewer selected any advisory rule, or `["advisory"]` only when
  ref-mode dispatched the instance with `--channel advisory`. `ReviewOutputBuilder` enforces
  it from the assignment in the output directory it is bound to: `add_finding()` and
  `update_finding()` reject a channel outside `channels`, and `save_draft()` rejects the
  whole draft when any finding carries one — an unbound or unreadable input fails open at
  add time to vocabulary-only validation, never at publication. Advisory findings remain
  listed but never gate the verdict; the summary records how many were suppressed and, only
  when stricter, the verdict over all findings.
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

- `<reviewer>-review.draft.json` — the mutable, rehydratable artifact replaced via `builder.save_draft()` until the exact receipt command finalizes it
- `<reviewer>-review.json` — the immutable final artifact published via `finalize_review()` (see `schemas/review-output.ts` for types)

The human-readable `<reviewer>-review.md` is derived from the canonical JSON, not written by reviewers — the step 8 readiness gate materializes it before reconciliation begins, and it remains renderable on demand via `python3 scripts/review/agent/output.py render|materialize`. Every live reader of final-review contents calls `load_review_document(path, reviewer)`, which delegates to the exact schema-2 `validate_review_document()` authority; existence-only scans may observe process evidence but cannot project findings, verdicts, reviewed files, or semantic completion.

`review-findings.md` follows the same rule one level up: the review-reconciliator publishes `review-findings.json` and nothing else, and the pipeline renders the Markdown from it through the SAME materializer (`materialize_markdown(output_dir, suffix="review-findings.json")`) at step 9 and again at step 11 — after the critic adjustments apply, so the rendering describes the ledger the run actually publishes. Every section the old hand-written report carried has a structured home: `assessment` (the overall conclusion), `checks` (material verification results), `meta.reconciliation` (pipeline metrics and not-applicable agents), `recommendations`, `observations` (verified tradeoffs), and `host_context_banner`. A render failure is a degradation note, never an exception — and never a file that disagrees with its JSON.

**The critic lifecycle has exactly three owners** — see `scripts/review/critic_adjustments.py`'s module docstring. The critic never authors IDs or adjudication state, and the orchestrator never edits the committed proposal.

**The findings ledger has exactly one write path** — see `scripts/review/findings_save.py`'s module docstring. **Never add a third ledger writer or a second path to either artifact — no bare `atomic_write_json`.**

**ReviewOutputBuilder API** — `agents/shared/reviewer-protocol.md` §Canonical Draft Lifecycle and §ReviewOutputBuilder API state the reviewer-facing contract once; `scripts/review/agent/output.py`'s docstrings state the implementation contract once. Neither is restated here. What matters for maintenance: `open()` is the only raw-reviewer entry point, a save derives the six top-level reviewed-file fields and prints a digest-bound finalization command, and `<reviewer>-review.json` is self-validating — `_validate_reviewer_envelope()` re-checks the claimed/unclaimed/count partition, so downstream readers read those fields rather than re-deriving them.

Findings use stable `fN` IDs and checks use stable `cN` IDs. A check has exactly `id`, `question`, `method`, `result`, and `source_reviewers`; it records material verification evidence and never affects verdict counts. Raw reviewers own findings, checks, observations, positives, recommendations, confidence, and reviewed-file claims. Only the reconciliator authors the initial nullable `assessment`; a real critic mutation invalidates it unless the orchestrator's `adjudicate` request carries a revised assessment, while a no-op leaves it untouched.

Verdict is auto-calculated from finding severities by `verdict_for_counts()` in `scripts/review/verdict_rules.py` — the ONE place the thresholds live, shared with `critic_adjustments.py`, which recomputes the ledger verdict after every applying critic batch. Never re-inline the ladder: step 11 derives the published pipeline verdict from that ledger, so a second copy that drifts reaches GitHub.
- Any critical → `block`
- 3+ highs → `block`
- Any high (or 5+ mediums) → `request_changes`
- Any medium → `comment`
- Otherwise → `approve`

The outer-pipeline verdict (`APPROVE`/`COMMENT`/`REQUEST_CHANGES` in `pipeline-result.json`) is DERIVED from the reconciled ledger's verdict at step 11 — `orchestration.py` owns the mapping between the two layers, and `block` maps to `REQUEST_CHANGES`. A critic `ESCALATE` overrides it to `COMMENT`. Nothing transcribes a verdict by hand any more; `review-verdict.json` is gone. Derivation settles on the prepare pass, but it is not published until the report handoff completes.

### Cross-Repo Dependency: pirategoat-bot

The `pirategoat-bot` Slack bot (at `~/Work/a8c/pirategoat-bot`) wraps this plugin's review pipeline. The two repos share integration contracts that must stay in sync:

- **`review-context.json`** — The bot writes this file (orchestrator.js) before spawning the `claude` CLI. This plugin reads and enriches it (review/context.py). Field names, nesting, and required paths must match across both repos.
- **Outer-pipeline verdict values** — The bot's `pr-review.py` defines outer-pipeline verdicts (`APPROVE`/`COMMENT`/`REQUEST_CHANGES`) and `github.js` maps them to GitHub actions. This plugin has its own per-agent verdict system (`block`/`request_changes`/`comment`/`approve` in `review/agent/output.py`). These are distinct layers — changes to one may need corresponding changes in the other.
- **Prompt template variables** — The bot's `prompts/pr-review.md` injects variables (`{{MERGE_BASE}}`, `{{GIT_RANGE}}`, etc.) that this plugin's scripts consume via the review context.
- **Terminal publication marker** — Resume discovery treats any `pipeline-result.json` as already complete, while delivery reads `review-report.md` immediately afterward. Therefore step 11 may create `pipeline-result.json` only after that exact report exists, and `report_path` must name `review-report.md`; `review-record.md` and `review-findings.md` are never terminal report fallbacks.

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

- Telemetry/manifest fields are authoritative for run identity, deterministic planner versus main-orchestrator adjustments, the generated-scope assignment, lifecycle, outcomes, critic verdict, and wall time.
- The `synthesis_agents` family measures the reconciliator and decision critic and is deliberately SEPARATE from `lifecycle`, which projects reviewer `agent_start`/`agent_complete` events. Neither synthesis agent produces those events or appears in a dispatch plan, so folding them in would corrupt every reviewer count downstream. Its durations come from the completion artifact's mtime, the closest available proxy for true completion; the observation time is not recorded. A run predating the family reports `missing`, never a zero-duration synthesis phase. Quick mode commits the pipeline's own `SKIPPED` verdict without a dispatch marker and therefore creates no row; a dispatched critic whose usable verdict never appears records `stalled` with no duration, reads `unavailable`, and degrades the run. Historical `SKIPPED` rows remain reader-compatible but are counted separately as `skipped_runs` and excluded from `total_ms`/`mean_ms`.
- **Two DISTINCT `usage` keys**, deliberately SEPARATE the same way `synthesis_agents`/`lifecycle` are: `measured["usage"]` (`availability.usage`) is the durable PER-RUN SNAPSHOT — `manifest_sections.build_usage_manifest` projecting `usage-snapshot.json`, sanitized by `review_metrics/sanitize.py`'s `_sanitize_usage_snapshot` — while `measured["transcript"]["usage"]` (`metric_availability.usage`) is the live TRANSCRIPT-derived family `measure_run()` computes fresh from session transcripts on every call. `usage_snapshot.py` is the bridge between them: its `_capture()` calls `measure_run()` to get the transcript family, then `_build_snapshot()` reads `measured_run["transcript"]["usage"]` to WRITE the durable snapshot that later becomes `measured["usage"]` on subsequent runs. Since `_sanitize_manifest` now always produces a top-level `usage` key, `measure_run()`'s return value at that call site carries BOTH keys side by side — `measured_run["usage"]` (near-always `None`/stale, since no snapshot exists yet at first capture) beside the correct `measured_run["transcript"]["usage"]` — so a future edit to `usage_snapshot.py` reading the former instead of the latter would silently capture nothing while looking plausible.
- There are no human overrides in this flow. Deterministic planning runs first; the main orchestrator may then add or skip agents and supplies the adjustment reasons.
- Lifecycle `agents.incomplete` is a deterministic sorted multiset with one repeated agent name per unmatched start execution. `incomplete_count` measures executions, `incomplete_identities` contains unique sorted names, and `incomplete_by_agent` preserves per-agent multiplicity. Complete manifests require the exact start-minus-completion multiset and suppress sibling overlays. Running manifests remain partial; the consumer may overlay only a strictly validated same-run JSONL lifecycle suffix after proving the sidecar arrays are exact causal prefixes, and must reduce fresh events without retaining raw prose or scope paths. Malformed, foreign, prefix-inconsistent, or chronologically invalid siblings fail closed for lifecycle only.
- Dispatch `adjustment_rate` measures changed agents over the full compared-agent union; `planner_removal_rate` measures removed agents over planner-dispatched candidates for comparable runs. Wall durations above one year are treated as implausible missing data.
- Valid plans with different agent identity sets disable adjustment comparison and carry only sorted identity-to-status projections. Ingestion must rederive both dispatch counts from those projections, require exact mismatch metadata, and fail malformed or unexpected projections closed for the dispatch family without retaining plan prose.
- Transcript correlation is optional and exact: session ID + output directory + recognized reviewer/reconciler/critic identity.
- Every metric family distinguishes complete, partial, missing, and disabled data. Missing data is never reported as a measured zero, and partial observations never enter complete denominators.
- **Legacy reconstruction is frozen.** Review-run legacy segments and identityless-segment recovery are best-effort overall; their independent availability families remain the reporting boundary and may be complete, partial, missing, or disabled per family. `session_analyzer.py` no longer infers review content from heredoc source: it reads the finalized `<reviewer>-review.json` the builder envelope names, so an artifact it cannot read is unmeasured rather than approximated, and its local ad hoc quality output still has no availability-family labels. A new inference-precision edge is a known limitation, not another hardening round; crashes, privacy/safety failures, or contamination through foreign run, agent, or artifact identity confusion remain bugs, and new precision belongs in producers—manifests, sidecars, and shared contracts—where durable fixes land.
- Stable review-run metrics reports use schema 3. Transcript-derived observed reads remain their exact schema-2 payload; legacy, missing, boolean, or future versions fail closed instead of being interpreted as empty measurements.
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

*Why does this module parse session JSONL when `session_analyzer.py` already does?* Their contracts are deliberately different: `session_analyzer.py` retains prose (prompts, commands, categorized text) for human-facing ad-hoc reports, while this module must never expose those bodies. The 2026-08-03 census found that the prior three-reader tally was not a full census: JSONL is read by `review_metrics/load.py::_read_jsonl` (plus its strict variant), `review_transcript.py::_read_jsonl` and `_bounded_jsonl_entries`, `session_analyzer.py`, three sites in `session_metrics.py` (`extract_triage_decisions`, `identify_agent_type`, and `extract_subagent_metrics`), and `telemetry.py::_read_events`, which now counts skipped gaps. Keep these readers separate because their contracts differ across binary/text input, strict/tolerant failure, and report/skip behavior, while the genuinely shared surface remains about 15 lines. Reopen this decision only if a malformed-line-handling fix has to be re-discovered per copy.

#### `scripts/analysis/usage_snapshot.py`

Captures one review run's token usage into its own run directory as `usage-snapshot.json`. Invoked by pipeline step 11 as a subprocess; also runnable by hand over a finished run.

```bash
python3 scripts/analysis/usage_snapshot.py --output-dir <run dir> [--sessions-root ~/.claude/projects]
```

It is a thin projection over `review_metrics.measure_run` (which drives `review_transcript.py`), never a second correlation implementation. It resolves the run manifest through `ReviewTelemetry.manifest_path` — the producer's own marker-file derivation — and falls back to `run-config.json` for a `session_id` the manifest lacks.

**Why a subprocess seam, not an import.** `scripts/analysis/` already loads `scripts/review/`'s telemetry, dispatch-status, and critic contracts by exact path. Importing the analysis package back into `orchestration.py` would close that loop and make finalize depend on the analysis import graph; the CLI keeps the dependency one-way.

**The two halves are labelled independently, and that is the point.** At finalize every subagent transcript is closed — reviewers, reconciliator, and critic have all returned — so subagent usage is complete evidence. The orchestrator is measuring its own still-open session, so its number is partial by construction. The enrichment's own `completeness.agent_data` cannot express this: it is ANDed with the orchestrator's `main_data_complete`, so one unresolved tool call in the main session reports every closed reviewer transcript as incomplete. The subagent label is therefore derived from subagent-scoped facts (correlated-vs-expected executions plus the agent-scoped warning codes), and the orchestrator label is gated on `window.closed` so a capture-time snapshot can never read `complete`.

**Window substitution.** A running manifest has no `ended_at`, and an unbounded window closes at the first human turn after it opens — the requester's next message in an interactive review. The capture substitutes its own instant as the window end, in its private view only; the manifest on disk is untouched, and `window.closed` records which kind of window the numbers cover. Re-running over a settled manifest is what upgrades a partial orchestrator half — and both a manual re-run and the manifest it feeds are now first-class parts of that upgrade, not just the snapshot file:

* **MONOTONIC, scoped to the artifact.** A re-run's candidate is compared, half by half (subagents, orchestrator), against whatever `usage-snapshot.json` is already on disk. A candidate that would downgrade either half — evidence that used to correlate and no longer does, most often rotated-out transcripts — is discarded wholesale: the existing artifact is left byte-for-byte untouched rather than overwritten with weaker evidence. The guarantee protects the FILE, not the run: deleting `usage-snapshot.json` is an explicit act, and the next capture over an empty slate re-measures from scratch and records whatever it finds — including a fresh `missing` — per the same recorded-absence doctrine as every other unmeasured state here. The CLI's one-line stdout summary carries `written`/`downgrade_avoided` so a caller can tell a genuine upgrade apart from a preserved prior measurement.
* **The manifest follows, through `ReviewTelemetry.reproject_usage()`.** `ReviewTelemetry` projects this artifact into the manifest's `usage` section wholesale exactly once, at finalize; nothing else revisits that section afterward, so a manual re-run over an already-finalized run used to upgrade `usage-snapshot.json` while the manifest kept reporting the finalize-time partial number forever. The manifest keeps ONE owning module even with two entry points into it — the same shape `critic_adjustments.write_findings` gives the findings ledger: `reproject_usage()` is a `ReviewTelemetry` method (it already imports `manifest_sections` and `atomic_write_json`, so this needed no new imports), and the CLI's call site is `_TELEMETRY_CONTRACT.ReviewTelemetry(str(output_dir)).reproject_usage()` — the CLI itself carries no reference to `manifest_sections` at all. The method patches ONLY the manifest's `usage` key and its `availability.usage` companion flag, through the same `atomic_write_json` primitive `_materialize_manifest` uses, and never reconstructs `run`/`dispatch`/`assignment`/etc. from the pipeline's own JSONL events, which stay telemetry's alone to rebuild. Two gates keep the patch narrow, both fail closed (no write): `status == "complete"` — a still-running manifest is `finalize()`'s territory alone, so the in-pipeline step-11 call into this method is a no-op every time (status still reads "running" at that point; `finalize()`'s own full rebuild, moments later in the same run, is what actually settles `usage` for a normal pipeline run) — and `schema == EVENT_SCHEMA`, so an unsupported-schema manifest is never interpreted. Reprojection is best-effort like every other manifest write telemetry performs: its outcome surfaces on the CLI's stdout summary as `manifest_reprojection` — a reason string (`written`/`absent`/`not_settled`/`unsupported_schema`/`io_failure`) rather than a bool, so the one anomalous outcome on a settled current-schema manifest (`io_failure`) stays distinguishable from the everyday no-ops — and no non-written reason ever turns into a nonzero exit or a stderr line — deliberately diverging from `usage-snapshot.json`'s own write path, which DOES fail loudly, because that write IS this CLI's sole reason for existing while the manifest is a derived surface it can always regenerate on the next re-run.

**Availability doctrine.** An unreadable, absent, or transcript-less run (Codex writes no Claude-format transcripts) still writes the artifact with `missing` and null payloads — a recorded absence, distinct from a run that never attempted the capture and has no artifact. This Codex-host gap is known and permanently unsolved: no re-run of this CLI can measure a host that never wrote a Claude-format transcript in the first place. Per-model buckets key on the DISPATCHED model (`claude-opus-5[1m]`), not the per-message model inside the transcript (`claude-opus-5`), because the bracketed variant is separately priced.

The snapshot reaches two durable surfaces: the run manifest's `usage` section beside `availability.usage`, and a compact `usage` block in `pipeline-result.json` (a pirategoat-bot consumer surface). Both project through `manifest_sections.build_usage_manifest()`, so they cannot disagree about what a usable measurement is. Step 11 may capture twice because its settlement pass is idempotently re-entered, but only the post-report pass publishes the compact block; only the manifest is reprojected by a manual re-run, and `pipeline-result.json` is not revisited outside the pipeline.

Run `pytest plugins/pirategoat-tools/tests/analysis/test_usage_snapshot.py plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py plugins/pirategoat-tools/tests/review/test_telemetry.py -v` after changing the CLI, its step-11 seam, or `ReviewTelemetry.reproject_usage()`.

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
    --limit 20

# JSON output for programmatic analysis
python3 scripts/analysis/session_analyzer.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --agent security-reviewer \
    --format json

# Analyze all agents (no --agent filter)
python3 scripts/analysis/session_analyzer.py \
    --sessions-dir ~/.claude/projects/<encoded-project-path> \
    --limit 5

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
- Output file details (Write tool usage plus the canonical Bash builder heredoc — recognized by its envelope, with the review read from the `<reviewer>-review.json` that envelope names — content size, finding counts)
- Aggregate statistics (tool call breakdown, cross-dispatch patterns)

**Output formats:**
- `text` (default) — human-readable report with full tool sequences
- `json` — structured data for downstream analysis

#### `scripts/analysis/session_metrics.py`

General-purpose tool for extracting operational metrics (runtime, model, cache tokens, verdict) from session transcripts. Documented in-file.

#### `scripts/analysis/codex_rollout.py`

Shared primitives for reading Codex CLI rollout files: thread metadata parsing, date-windowed discovery, single-pass thread scan, and tree building. The only module that knows the Codex rollout schema — both Codex CLIs build on it.

Deliberately separate from the Claude Code readers. Consistent with the 2026-08-03 JSONL reader census: the contracts differ (different schema, different discovery model), and the genuinely shared surface is small.

Run `pytest plugins/pirategoat-tools/tests/analysis/test_codex_rollout.py -v` after changing this module.

#### `scripts/analysis/codex_session_analyzer.py`

Traces one Codex thread tree in depth — per-thread model, duration, tokens, commands with exit codes, and file changes.

**Usage:**

```bash
# Newest thread tree for one project
python3 scripts/analysis/codex_session_analyzer.py --cwd /path/to/project

# A specific thread as JSON
python3 scripts/analysis/codex_session_analyzer.py --thread-id <thread-id> --format json
```

#### `scripts/analysis/codex_session_metrics.py`

One row per Codex thread plus a roll-up by agent role. Metric names match `session_metrics.py` so Codex and Claude Code figures can share a table.

**Usage:**

```bash
python3 scripts/analysis/codex_session_metrics.py --agent code-reviewer --since 30 --format markdown
```

Run `pytest plugins/pirategoat-tools/tests/analysis/test_codex_session_scripts.py -v` after changing these scripts.

#### Codex Session Data Locations

```text
~/.codex/sessions/YYYY/MM/DD/
└── rollout-{ISO-timestamp}-{thread-id}.jsonl   # one thread per file
```

There is no per-project partitioning — `cwd` is a field on line 1 — and subagents are sibling rollouts linked by `agent_path`, not files in a subdirectory. Only finished sessions are analyzed; a live rollout grows while being read.

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

## Backlog

Deferred-but-valid work lives in [`BACKLOG.md`](BACKLOG.md) — the committed,
canonical home. When an audit, review, or field run defers a real finding
instead of fixing it, record it there with evidence and a do-when condition;
session analysis docs under `.claude/docs/` are gitignored and do not survive
as a place of record. Remove entries when done or dead.

## Development Workflows

### Running the Dev Version (`scripts/claude-pirategoat-tools-dev`)

To exercise unreleased plugin changes against a real repository before release, start Claude Code through the wrapper at the repo root:

```bash
scripts/claude-pirategoat-tools-dev                          # interactive
scripts/claude-pirategoat-tools-dev -p "review this branch"  # headless; args pass through
```

Symlink it onto your `PATH` if you want it available everywhere — it resolves the worktree from its own location, following symlinks, so it works from any directory.

**What it does.** Two flags that must always travel together:

```bash
claude --plugin-dir <worktree>/plugins/pirategoat-tools \
       --settings '{"enabledPlugins":{"pirategoat-tools@<marketplace>":false}}'
```

`--plugin-dir` loads the worktree **in place** — the plugin is not copied into `~/.claude/plugins/cache/`, so edits apply to the next session with no sync step. The `--settings` override is not optional: `--plugin-dir` alone loads the worktree *alongside* the installed release, and both then register the same commands and agents. The release's plugin id is derived from `.claude-plugin/marketplace.json` rather than hardcoded, so renaming the marketplace cannot leave the wrapper disabling a plugin that no longer exists.

**Nothing is installed, cached, or written to disk.** `claude plugin list` reports the worktree copy as `pirategoat-tools@inline` with `Status: loaded` and no installed record; the release keeps its own entry, disabled only inside that process. A plain `claude` is always the released version — dev is opt-in and never sticky. This is the opposite failure direction from a global switch: forgetting the wrapper means you are on the safe version.

**Which version actually ran** is recorded durably. `_detect_plugin_version()` falls back to the CHANGELOG's top version when the plugin root's directory name is not a semver, so a run under the wrapper records the worktree version (e.g. `1.114.0`) in `plugin_version` in the run manifest, even though `claude plugin list` shows `Version: unknown` for an inline load. Check it with:

```bash
python3 scripts/analysis/review_run_metrics.py --last 1 --format json | grep plugin_version
```

**Which BUILD ran** is a different question, and the one that matters under the wrapper: `plugin_version` only moves when a release is cut, so every dev-mount commit between two releases stamps the same number. `_detect_plugin_commit()` records the checkout's short HEAD as `plugin_commit` in `run-config.json` — deliberately there and nowhere else, since run-config is the artifact that could not answer it. It resolves for ordinary installs too — Claude Code installs a marketplace by cloning it, so an installed plugin usually sits in a repository — and is `null` only where there is no repository to ask (no Git binary, a distribution that arrived some other way). Read it straight from the run directory:

```bash
python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["plugin_commit"])' <run dir>/run-config.json
```

**Permission prompts are skipped.** The wrapper passes `--dangerously-skip-permissions`, because these sessions exist to exercise the review pipeline end to end and prompting on every tool call defeats that. It is scoped to the wrapper rather than aliased onto `claude`, so ordinary sessions keep their prompts. Once prompts are gone the remaining backstop is the `yoloing-safe` PreToolUse hook on `Bash|Write|Edit|Read` — if that plugin is disabled, these sessions have neither. Check with `claude plugin list | grep -A3 yoloing-safe`.

**Caveats.**

- The mount is the live working tree, uncommitted edits included. A half-finished edit is what reviews your PR. Check `git status` before starting a session you intend to trust.
- Edits made *during* a session do not affect the already-loaded plugin; restart the wrapper to pick them up.
- `/tmp/.pirategoat-tools-root` is repopulated from `$CLAUDE_PLUGIN_ROOT` by the PreToolUse hook, which under the wrapper is the worktree, so the fallback cache self-corrects. Its `find`-based fallback sorts on the full path and would otherwise favor the released install.

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
| 4 | Root `README.md` | Update count in directory tree (e.g., "34 agents, 21 skills, 7 commands") |
| 5 | Generated Codex outputs | Run `python3 scripts/generate_codex_compat.py` and commit the result |
