# Changelog

All notable changes to the pirategoat-tools plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.112.0] - 2026-07-21

Makes the review pipeline measurable, and puts the resulting pressure on reviewers to spend the budget they are given.

Runs now emit durable telemetry manifests and a supported run/cohort metrics interface, so planner decisions, scope coverage, retries, and resource use can be compared across executions instead of reconstructed by hand.

That measurement closes the loop on the 2026-07-21 large-branch review analysis (349 files, 52.9k insertions): agents spent 37% of their tool budget (535 of ~1,455 calls, median 27 against a target of 80) while the branch's largest files went effectively unread — one agent cited a "budget ceiling" it was 106 calls away from as its reason for a partial pass. Disclosure of coverage gaps landed in 1.108.0; this release adds the missing behavioral pressure to actually spend the budget, delivers the mandatory NOT DIFFED contract that 1.108.0 wrote into a section reviewers never receive, and finishes the stale-artifact cleanup whose gap made the run's change inventory report a previous day's numbers.

### Added

- **The budget briefing directs unspent budget at the NOT DIFFED queue.** When in-scope files were withheld for context budget, the REVIEW BUDGET section now instructs: while under target with NOT DIFFED files unread, read the next one (largest first) — finishing early with in-scope files unread is a coverage gap, not efficiency.
- **NOT DIFFED reads as a work queue, not an appendix.** scope.py's section header text ("read any of these selectively") licensed skipping; it now states the files ARE in scope, the list is the agent's remaining work queue largest-first, and declaring is only for files genuinely out of reach.
- **Declaring a file unreviewed requires genuine budget exhaustion.** The REVIEW BUDGET briefing now carries the whole NOT DIFFED contract: every such file must be reviewed or declared, an APPROVE that silently ignores them is a protocol violation, a `Not reviewed (budget):` declaration written with most of the budget unspent is a protocol violation, and citing the budget or ceiling for work the agent had calls left for is a false statement. Utilisation-vs-target is measurable per run via agent-start `budget_target` telemetry plus transcript enrichment (`review_run_metrics.py`).
- **Review telemetry records durable run and session identity.** Every event now carries a versioned schema and unique run ID, while the start event captures the Claude session, plugin version, repository, mode, and requested Git range identity for reliable cross-system correlation.
- **Review runs expose durable measurement manifests.** Each telemetry log now has an atomically refreshed, fail-open sidecar with run identity, resolved Git coordinates, step and agent lifecycle events, aggregate outcomes, and explicit availability metadata without retaining PR, prompt, finding, or tool-result prose.
- **Planner decisions remain measurable after orchestration.** Step 5 now preserves an immutable deterministic dispatch baseline before the main orchestrator adjusts the editable plan, and run manifests compare both decisions with explicit availability, duplicate-plan diagnostics, raw dispatch counts, and allowlisted routing evidence.
- **Generated reviewer scopes expose changed-file coverage.** Agent-start telemetry now records sanitized repository-relative scope paths, and run manifests derive explicit assigned, excluded, and uncovered path sets from actual dispatched starts while labeling generated scope as descriptive rather than proof of model reads.
- **Review transcripts can enrich run measurements without retaining review prose.** A fail-soft parser correlates one manifest to its exact Claude session and recognized subagents, unions validated manifest starts with exact run-matching reviewer and synthesis dispatches—including malformed unpairable dispatch blocks—for execution-level completeness, reports explicit expected/correlated/missing-agent and per-metric completeness instead of silent partial denominators, deduplicates cache-aware token usage, recognizes narrow corpus-replayed Read/Write/Edit success structures—including token-capped reads and null-original updates—without retaining their bodies, attributes bounded orchestrator usage by successful stage-entry timestamps recorded in the manifest, measures safe tool-failure and builder-attempt recovery categories, and reports explicitly non-exhaustive normalized repository reads with regular-reviewer scope classification separated from reconciler, decision-reviewer, and critic activity.
- **Review runs and cohorts have one supported measurement interface.** `scripts/analysis/review_run_metrics.py` prefers durable manifests, safely reduces legacy JSONL logs, optionally enriches exact Claude sessions, and reports planner-to-main-orchestrator adjustments—including distinct union-wide adjustment and planner-removal rates—generated-scope coverage, outcomes, critic verdicts, bounded wall time, cache-aware usage, tool recovery, first pipeline-owned Bash attempts, and separate reviewer out-of-scope versus non-scope-comparable synthesis reads with independent complete/partial/missing/disabled availability instead of zero-filling unavailable data. Transcript enrichment costs a session discovery and a full transcript parse per run, so it applies to bounded queries (`--last`, `--run-id`); an unbounded cohort sweep reports the transcript family as `disabled` rather than paying that cost across all history, and the cohort itself is never truncated.
- **Budget omissions have a supported output representation.** `ReviewOutputBuilder.add_unreviewed(file)` records NOT DIFFED files a reviewer genuinely could not reach at budget exhaustion: declared paths surface as an `unreviewed` array in the JSON output and render the mandated `**Not reviewed (budget):**` line in the Markdown summary, without affecting the verdict. The budget briefing and bootstrap heredoc snippet prescribe the API instead of a hand-written Markdown line the fixed-form renderer could not produce.

### Fixed

- **Budget sizing counts the NOT DIFFED workload.** Scope-proportional budgets summed only the inline `=== FILES ===` sections, so the largest reviews — exactly the ones with a deferred NOT DIFFED queue — computed the smallest targets and missed the capped-budget framing. NOT DIFFED `(+N -M)` stats now enter the line count; lock/generated `CHANGED (no diff)` files stay excluded.
- **Deferred files count as reviewer scope in telemetry.** Agent-start events persisted only inline FILES entries as scope paths, so coverage reported NOT DIFFED files as uncovered and transcript analysis classified a reviewer inspecting its deferred queue as reading out of scope. Deferred paths (stats-shaped lines only, never section prose) now enter the telemetry scope path set and file count; the inline-only list keeps its meaning for file-history consumers.
- **Per-step orchestrator usage keeps the final cumulative record.** The step attribution retained its own first-wins dedup for repeated message IDs after the total/per-model reducer moved to last-wins, undercounting per-step totals and letting them disagree with total usage from the same transcript. Steps now use the same last-record-is-authoritative contract, attributed to the stage where the response began.
- **Coverage and lifecycle scope paths obey the canonical path contract.** Malformed or hand-edited sidecars could carry absolute, traversal, backslash, drive-prefixed, dot-segment, or Unicode control/format-character paths through coverage ledgers and lifecycle scope paths into the privacy-reduced report, while observed-read paths already rejected them. All are now validated with the canonical repository-relative validator: strict ingestion fails closed (coverage to manifest fallback, lifecycle for that family only) and the lenient legacy sanitizer drops non-canonical paths.
- **Deferred-file outcomes reconcile into coverage before it is reported.** Inline coverage came purely from pre-review scope-summary sidecars, so a reviewer that read a NOT DIFFED file per the budget contract still had it reported as a hard gap ("no agent saw it"), and `add_unreviewed` declarations were never consumed. Coverage now reconciles the sidecars with each agent's output: undeclared deferred files from agents with output move to `files_deferred_reviewed` (agent claim, not proof of read), declarations surface in `files_declared_unreviewed` and annotate the remaining genuine gaps, and agents without output can neither claim nor declare.
- **Manifest refreshes keep the resolved git identity.** Step 1 resolves symbolic range endpoints to SHAs, but every later manifest refresh overwrote `base_sha`/`head_sha` with raw context values — reintroducing the movable-ref identity (`main`) from step 3 onward. Refreshes now replace resolved endpoints only with validated full SHA object names.
- **Run metrics include the opening orchestrator turn.** telemetry.start() runs inside the Step 1 subprocess, so the transcript entry that invoked it — timestamped ~139ms before `started_at` in a real run, carrying 73,944 cache-read tokens — was always filtered out of usage and per-step totals. The window's lower bound now anchors to the run's triggering prompt (symmetric to the presentation-turn upper bound), and the opening turn attributes to Step 1.
- **Interactive PR runs record the reviewed commit, not the pre-checkout one.** Step 1 resolves HEAD before step 2 checks out the PR branch, and context never recorded a full head_sha, so the pre-checkout SHA survived as the durable run identity. Step 3's context fill now resolves the reviewed head (range endpoint or HEAD) to a full SHA after workspace setup; bot-precomputed identity is preserved.
- **One damaged legacy log no longer aborts reports.** Text-mode line iteration raised UnicodeDecodeError outside the per-line handler on any invalid UTF-8 byte — one damaged historical JSONL failed the whole cohort CLI, and one damaged main-session line discarded a run's entire transcript enrichment. Both non-strict readers now iterate binary lines so a bad byte costs exactly that line; strict readers keep failing closed.
- **Non-text Read variants are successful reads.** `file_unchanged` and image Read results were classified unknown — degrading completeness and omitting the reads; both known non-error envelopes are now recognized.
- **Legacy Task dispatches share the dispatch carve-out.** A dangling legacy `Task` dispatch degraded every actor family instead of staying with per-family correlation evidence; all carve-out sites now share one dispatch-name constant covering both tool names.
- **Unresolved orchestrator calls degrade main evidence.** Interrupted main-session operations no longer vanish while orchestrator/tool-failure metrics claim completeness; Agent dispatch anomalies stay with the per-family correlation machinery instead of collapsing into whole-run degradation.
- **Domainless reviewers are scope-exempt.** `tests-mutation-reviewer` discovers its own scope; its reads now route to the `non_scope_comparable` bucket instead of all reporting out-of-scope against its empty mapping, while it remains a regular reviewer for builder metrics.
- **Non-repo-relative unreviewed declarations fail loudly.** Absolute, traversal, drive-prefixed, and normalized-dot (`.`, `./`, `foo/..`) paths can never match canonical scope paths and would invert into reviewed claims; the builder now rejects them and canonicalizes backslash separators at both ends.
- **Malformed unreviewed fields claim nothing.** A non-null, non-list `unreviewed` value in a parseable review JSON was coerced to an empty declaration list, turning unknowable intent into a full-review claim that erased the agent's deferred files from `files_never_inline`. Coverage reconciliation now treats it like unparseable output — the agent can neither claim nor declare — while canonical null and absent keys keep meaning "declared nothing".
- **List-only files count as reviewer scope in telemetry.** Lock/generated files a domain rescues into `CHANGED (no diff)` instruct the reviewer to inspect them when relevant, yet telemetry scope carried only FILES and NOT DIFFED paths — so `coverage.by_agent` omitted them and transcript enrichment classified a legitimate read as out-of-scope. All three stat-shaped sections now share one parser feeding the telemetry scope set, while list-only lines stay out of budget sizing and the inline FILES list.
- **Active runs stay out of complete transcript totals.** A running manifest's transcript can still grow, yet its observed families could classify complete and enter cohort complete denominators; every transcript family now caps at partial until the run settles.
- **Fractional token counts are rejected, not truncated.** A non-integral usage value (corruption/schema drift) was silently floored while usage claimed complete; token counts now require exact nonnegative integers, and corrupted records downgrade through the damaged-record channel.
- **Superseded-turn parse gaps don't degrade the run.** Malformed JSON/UTF-8 lines in older session turns are discarded with their turn on supersession, exactly like timestamp gaps.
- **Malformed tool-use blocks count as issued, unresolved calls.** Blocks with non-string id/name were dropped before accounting; they now enter the budget numerator and flip evidence families to partial.
- **Builder reconstruction requires terminal success.** The save gate uses the canonical tri-state result classification, so nonterminal (`status: "running"`) and unclassifiable structured payloads stay unresolved instead of counting as persisted reviews; the legacy bare-result success signal is preserved.
- **Range endpoints peel annotated tags to commit identity.** Plain `rev-parse` on an annotated tag returns the tag object id; both endpoint resolvers now resolve with `^{commit}`, and a supplied full object id survives unpeeled only when git is unavailable.
- **Overlapping reviewer executions keep both completions.** The lifecycle projection revised the completion slot regardless of outstanding starts, so two overlapping executions reported a false incomplete; a completion now matches an outstanding start while any remain, in both the telemetry producer and the mirrored overlay projection.
- **Superseded-turn timestamp gaps don't degrade the run.** A damaged record in an older turn is discarded with that turn when a later prompt supersedes it; gaps degrade availability only when their turn enters the window.
- **Duplicate tool-call IDs count as unresolved evidence.** Ambiguously paired calls were skipped silently while the transcript measured complete; they now flip the affected families to partial.
- **Failed builder saves with structured-only errors don't reconstruct.** The save gate now reuses review_transcript's canonical structured-failure classifier (exitCode/status/interrupted/error) alongside block-level `is_error`.
- **Read classification requires scope evidence.** An absent `coverage.by_agent` mapping was treated as an empty reviewer scope, reporting every read as out-of-scope with complete confidence; the reads family now goes partial with an `agent_scope_evidence_missing` diagnostic while usage and builder evidence keep their own accuracy.
- **Unclassifiable tool results count as incomplete evidence.** A paired result matching no recognized schema resolved to "unknown" and vanished from metrics while families stayed complete; every unknown-state call now marks the agent's evidence incomplete (empirically zero such results across 744 real calls, so healthy runs stay complete).
- **Builder compliance completeness is regular-reviewer evidence only.** A missing synthesis transcript no longer downgrades fully observed reviewer builder data; synthesis-only expectation gaps leave artifact metrics complete-and-empty.
- **Reconstructed reviews require a save and dedupe reruns.** Heredocs that never call `builder.save()` reconstruct nothing, and successive successful saves to the same artifact keep only the final record — quality reports match what actually persisted.
- **The TypeScript contract declares `unreviewed`.** `schemas/review-output.ts` now describes the builder's emitted coverage-gap field (`string[] | null`).
- **Three-dot ranges parse correctly for run identity.** "main...topic" was split naively on "..", storing ".topic" as head_ref so the reviewed head could not resolve and interactive post-checkout runs kept the pre-checkout SHA. Ranges now partition on "..." before ".." and omitted endpoints default to HEAD.
- **Synthesis-only runs keep builder metrics available.** Excluding synthesis agents from builder entries left such runs with the contradictory available=false/complete=true object the sanitizer rejects; availability now keys off expected regular reviewers, reporting available-and-empty instead of missing.
- **Reconstructed findings honor severity floors, and only saved reviews count.** The session-analyzer heredoc reconstruction now applies the builder's severity lowercasing and floor promotion, and synthesizes a review record only when the paired Bash tool result confirms the save succeeded — failed attempts contribute nothing and retries count once.
- **Budget utilization has its numerator.** Agent-usage entries in the transcript enrichment and stable report now carry `tool_calls` (every issued call), pairing with agent-start `budget_target` so reviewers that declare budget exhaustion with calls remaining are auditable.
- **Running lifecycle overlays preserve validated numerics.** The fresh-suffix privacy projection zeroed issue counts, severities, scope sizes, and budget targets — reporting measured zeros for work that occurred. Numerics are preserved; free-string fields and scope paths stay reduced.
- **Synthesis agents stay out of builder-compliance metrics.** Reconciliator/decision-reviewer/critic artifact analysis no longer enters `by_agent`, so their normal non-builder saves stop inflating the cohort `no_builder_attempts` reviewer-noncompliance counter.
- **Unresolved tool calls mark agent evidence incomplete.** A transcript ending after tool_use but before tool_result (crash mid-call) previously vanished from reads and failures while the run measured complete; it now flips the affected families to partial with an `agent_transcript_unresolved_calls` diagnostic.
- **Transcript enrichment parses Z-suffixed timestamps on Python 3.10.** Claude Code writes `...Z` timestamps, which `datetime.fromisoformat()` only accepts from 3.11 — on 3.10 every record became a timestamp gap and enrichment measured nothing. The transcript parser now normalizes the Z suffix exactly like the metrics contract parser.
- **Task notifications no longer truncate the run window.** Harness-injected `<task-notification>` user records were classified as human prompts, so a background agent completing between `ended_at` and the final response closed the window before the presentation turn. Synthetic notifications (string or text-block form) are excluded from the boundary check.
- **Completed-run metrics include the final presentation turn.** telemetry.finalize() records `ended_at` inside the final step's subprocess, before the orchestrator's report read and summary reach the transcript — strict end-bounding dropped that turn from orchestrator usage, per-step usage, and tool-failure totals on every completed run. The window now stays open through the in-flight turn and closes at the next human prompt, preserving same-session next-run isolation.
- **Unreviewed declarations match their canonical scope paths.** A declaration like `./src/omitted.php` failed the exact comparison against the sidecar's `src/omitted.php` and inverted into a deferred-but-reviewed claim. `add_unreviewed()` now stores the posix-normalized canonical form, and the coverage loader normalizes declarations it reads.
- **Session quality analysis recognizes the mandated Bash builder.** Compliant reviewers save through the one-shot Bash heredoc and no longer emit Write calls, but quality metrics only consumed Write payloads — new sessions produced empty per-agent records. The analyzer now recognizes the canonical builder envelope and reconstructs the review record from the heredoc's literal `add_issue()` calls, flowing through the existing quality pipeline with graceful degradation for unparseable bodies.
- **Repeated transcript message IDs no longer undercount usage.** One assistant response split across JSONL records shares `message.id` with identical input/cache fields while `output_tokens` grows toward the final cumulative count. Usage summaries kept the first record per ID; they now keep the last, so per-agent, per-model, and total output usage reflect what was actually generated.
- **One malformed sidecar no longer aborts the cohort.** Manifest validators tested raw JSON values for set membership, so a structured value where a scalar was expected (`status: []`, list warning codes, list critic verdicts, list legacy event names) raised `TypeError` from `load_runs()` and took down the entire cohort. Values are type-checked before membership tests, degrading only the malformed file to its legacy fallback.
- **Durable git identity stores commit SHAs, not movable refs.** With an explicit symbolic range such as `main..HEAD`, the context layer stores the literal branch name as `merge_base`, and run identity trusted any nonempty supplied value — so the manifest recorded a ref that stops identifying the reviewed code once the branch advances. Supplied endpoints now pass through only when they are full SHA object names; anything symbolic is resolved with `rev-parse`.
- **Orchestrator transcript diagnostics survive sanitization.** `orchestrator_transcript_time_gap` and `orchestrator_stage_timeline_invalid` were emitted but missing from the warning allowlist, so reports degraded the affected metric families while stripping the explanation. Both codes are allowlisted, and a contract test keeps every transcript-emitted code in sync with the allowlist.

- **Mandatory NOT DIFFED handling now actually reaches reviewers.** 1.108.0 made reviewing or declaring each budget-skipped file mandatory, but the rule lived in the reviewer protocol's `## Scope Discovery` section — which `bootstrap.py` strips before handing the protocol to an agent, so no bootstrap-driven reviewer ever received it. The contract is delivered in the `REVIEW BUDGET` briefing alongside the budget it refers to, and a regression test asserts each clause survives protocol stripping.
- **Step 1 now clears every per-run artifact.** Stale-artifact cleanup previously missed `*-review.md`, `*-scope-summary*.json`, `*.started`, `reconciliation-context.json`/`.md`, `critic-context.md`, and `.telemetry-log-path` in reused output directories. Consequences: a stale `.telemetry-log-path` survived a fail-open `start()`, so later steps appended events to the previous run's log and rewrote its manifest; an agent's Write no-op'd on a pre-existing unread Markdown file; a previous-day `reconciliation-context.json` sat alongside fresh artifacts; stale `.started` markers could turn a forgotten dispatch into `TIMED_OUT` instead of `NOT_DISPATCHED`; and stale scope summaries could contaminate the run-level inline-coverage map. (The root cause of the stale change inventory itself — prior-run `review-context.json` masquerading as precomputed context — is fixed by this release's interactive step-1 context reset, below.)
- **Capped budgets no longer claim calibration.** Above ~650 scoped lines the tool-call budget clamps at 80, yet the briefing still said "Calibrated to YOUR scope" — a claim agents quoted back as justification for stopping early. When the cap is hit, the briefing now states the scope exceeds what the target can fully cover and presents the target as an effort floor, not proof of coverage. Registry `budget_override` values are never presented as capped.
- **Measurement internals retain one canonical contract and one-pass transcript evidence.** The planner, pipeline, telemetry, and cohort metrics now share one dispatch-status vocabulary, including counting `DISPATCH_OVERRIDE` correctly in dispatched and conditional totals. Cohort ingestion also reads the default telemetry directory from the producer contract, correlated subagent transcripts are decoded once while preserving partial parse evidence, aggregation families have focused pure boundaries without changing the stable report schema, and local CLI failures retain their exception context.
- **Dispatch-plan consumers classify every status explicitly.** The canonical vocabulary now exposes the complete skipped-state set, and pipeline orchestration, status reporting, and telemetry no longer infer skipped agents by negating dispatched states or matching a prefix. Missing, null, empty, structured, and unknown hand-edited statuses fail with the offending agent and exact value in orchestration/status paths, while telemetry keeps its fail-open guarantee by omitting the malformed summary.
- **Measurement projections now validate their own completeness.** Model availability requires every accepted per-model token bucket to conserve measured agent usage field by field without degrading otherwise complete total or per-agent usage. Telemetry and strict ingestion consistently reject Unicode control and format characters in reported repository paths while preserving ordinary non-ASCII paths, and running-log overlays require only the append-ordered pipeline start, step, and end timeline to be nondecreasing without imposing global ordering on parallel agent events.
- **Review telemetry keeps lifecycle state owned by the current run.** Interactive Step 1 now replaces reusable output-directory context with the minimal current-run seed before telemetry starts while preserving bot-provided noninteractive context. Nullable reviewer domains serialize canonically, and repeated corrected saves remain append-only in JSONL while manifests and running-log overlays retain only the latest completion for each execution; a later start still records a genuine retry.
- **Transcript enrichment stays within one review run.** Main-session evidence is now bounded by the manifest's timezone-aware start/end window before dispatch correlation, usage, failures, and stage totals are derived. Reviewer correlation extracts one canonical bootstrap command from the multiline Step 6 prompt, synthesis correlation accepts the pipeline's same-line and split output-directory labels, and orchestrator stages come from validated manifest step timestamps instead of reconstructing multiline shell commands.
- **Session quality analysis ignores unrelated JSON writes.** Quality reports now require a `*-review.json` path and the reviewer/issues schema before treating a captured `Write` payload as review output. This prevents scalar JSON such as `.nvmrc` from crashing analysis and package/config JSON from creating bogus `unknown` reviewer records.
- **Parallel reviewers no longer create shared temporary builder scripts.** In the historical analyzed cohort, 30 of 139 reviewer runs failed their first builder-script write when parallel agents reused generic filenames in the parent session's shared scratch directory. Bootstrap is now the sole executable source for the collision-safe one-shot quoted Python heredoc and explicitly prohibits temporary builder scripts; the shared protocol now points reviewers to bootstrap instead of carrying an unreachable duplicate command, giving transcript measurement one canonical command shape.
- **Review measurements recognize the pipeline-owned builder envelope.** Transcript enrichment classifies a Bash submission when its first line contains exactly the four required bootstrap environment assignments—values may be empty and names may appear in any order—followed by `python3 <<PY`; it treats the body as opaque and the structured Bash result as authoritative for attempt success, failure, and recovery. Write calls remain ordinary writes and cannot enter builder-attempt or recovery measurements. The `artifact_writes` family reports `builder_attempts`, `builder_successes`, `builder_failures`, and `first_builder_attempt_succeeded` consistently across sanitization, projections, availability, and cohort consumption. Because transcript evidence is classified at read time, historical transcripts receive the same envelope-based interpretation retroactively.
- **Main-orchestrator and synthesis reads stay out of reviewer scope measurements.** The `all`/`in_scope`/`out_of_scope` partition now contains only exact correlated regular-reviewer reads. Planning and orchestration reads are excluded, while exact `review-reconciliator`, `decision-reviewer`, and `critic` reads remain available in a sanitized, deduplicated `non_scope_comparable` bucket and separate complete/partial cohort totals and per-path breakdowns instead of inflating reviewer out-of-scope paths.
- **Observed-read contracts separate actor-family evidence and fail closed.** The v2 transcript payload and stable v2 JSON report independently track regular-reviewer and synthesis completeness, availability, and cohort denominators, with only a conservative combined state. Exact synthesis identities prevent critic-like or reconciliator-like reviewer names from being misclassified, and strict canonical repository-relative path validation rejects legacy/mismatched schemas, absolute or traversal paths, dot or empty segments, Windows separators and drive paths, and controls instead of silently admitting or zero-filling them.
- **Incomplete dispatch decisions cannot masquerade as measured adjustments.** Telemetry and cohort ingestion now require the pipeline's supported nonempty status vocabulary for every compared or projected agent decision. Missing, null, empty, unknown, and structured non-string statuses fail closed without interrupting manifest refresh or cohort ingestion. Initial and final plans with different agent identity sets likewise retain their individually measured dispatch counts but disable adjustment comparison; telemetry emits only deterministic identity-to-status projections, and ingestion rederives both counts while rejecting unsafe identities, malformed statuses, contradictory sets, extra fields, or projections outside this exact mode. Valid legacy final-only projections and duplicate-plan diagnostics remain supported.
- **Incomplete retry executions retain their multiplicity.** Lifecycle manifests now emit `agents.incomplete` as a deterministic sorted multiset with one repeated agent name per unmatched start, including current running-state observations. Strict complete-manifest ingestion validates the exact start-minus-completion counts without weakening causal checks, canonical duplicate-run comparison preserves repeats, and per-run/cohort reports expose unmatched execution totals, unique identities, and deterministic per-agent counts.
- **Running coverage snapshots stay visible without entering complete-only aggregates.** Structurally valid running manifests expose coverage as partial in per-run output, while complete-only cohort coverage denominators exclude them.
- **Running lifecycle measurements retain fresh append-only events.** When a valid running sidecar trails concurrent agent telemetry, ingestion overlays only the strictly validated same-run JSONL suffix after proving the sidecar lifecycle is an exact causal prefix. Fresh events are reduced to lifecycle measurement evidence without copying raw prose or scope paths, retry multiplicity is recomputed with counter semantics, complete sidecars remain authoritative, and malformed, foreign, or chronologically inconsistent logs fail closed only for lifecycle availability. Lifecycle availability is derived from the explicitly measured lifecycle summary.

## [1.111.0] - 2026-07-29

Adds first-class Codex installation and execution while preserving the
existing Claude Code workflows as the canonical authoring surface.

### Added

- **Generated Codex packaging.** The repository generator creates the Codex
  plugin manifest and seven explicit command-skill adapters from the canonical
  marketplace entry and command files.
- **Native Codex review orchestration.** The review pipeline persists a host
  selection and emits Codex briefings that dispatch parallel subagents,
  reconciliation, and decision criticism with native Codex agent tools.
  Reviewer names are normalized to Codex-safe task identifiers while the
  canonical hyphenated names remain unchanged everywhere else.
- **Shared reviewer definitions.** Codex subagents load the same canonical
  reviewer Markdown files as Claude Code instead of maintaining a second
  prompt tree. Briefings explicitly treat YAML frontmatter as Claude Code
  packaging metadata and do not translate model or tool labels.

### Changed

- Shared skill resource paths now use a host-neutral `SKILL_DIR` convention.
- Review pipeline stop and dispatch instructions adapt to the selected host
  while retaining Claude Code as the default for existing invocations.

### Fixed

- **`copy-as` clipboard injection.** Clipboard content is now written with the
  Write tool instead of a shell heredoc, so copied text containing the heredoc
  delimiter can no longer terminate it early and execute the remainder as shell.
- **`code-review` mode.** The pipeline invocation now passes a computed `MODE`
  (full/reset switches to `full`) instead of hardcoding `--mode incremental`,
  so full/reset runs actually request a full review.
- **`pr-update` artifact discovery.** Optional PR-template discovery is guarded
  so an empty template directory no longer runs `cat` with no argument, and the
  review-artifact paths match the repo-qualified directories the review
  commands actually produce.
- **`switch-to` git safety.** The fork remote is pointed at this PR's fork
  whether or not a remote of that name already exists (`set-url` when present,
  `add` when missing), so a stale same-named remote can't make the checkout
  fetch the wrong repository; stashing includes untracked files so the dirty
  summary and the stash agree.
- **`iterative-review` worktree safety.** The loop stops for user confirmation
  before committing a dirty worktree rather than blanket-committing unrelated
  edits or secrets into history.
- **Self-contained Codex review adapters.** Generated shell examples that use
  `CODEX_PLUGIN_ROOT` now assign it first (Codex does not export it), and Codex
  repo-reviewer dispatch spawns the installed generic adapter task instead of
  the synthetic per-instance name.
- **Self-contained `SKILL_DIR` examples.** Runnable snippets in the
  `using-figma`, `analyzing-cc-sessions`, and `decision-critic` skills assign
  `SKILL_DIR` before use.
- **`copy-as` Linux fallback.** The `xclip` guidance now documents publishing
  both the HTML and plain-text targets (xclip owns one selection target at a
  time) instead of only `text/html`.
- **Codex generator robustness.** `$ARGUMENTS` substitution is word-boundary
  matched so `$ARGUMENTS_LIST`-style names are not corrupted, and the
  `--host codex` injection now fails loudly if a pipeline-invoking command stops
  matching the expected pattern instead of silently dropping the flag.

## [1.110.0] - 2026-07-27

Closes a review blind spot around speculative extension surface and sharpens two structural lenses. Field feedback from WooCommerce Subscriptions reviews showed new hooks/filters being introduced without a stated need and maintained nearly forever afterward — while simplification-reviewer's framework-convention exemption actively excluded them from YAGNI review and wp-architecture-reviewer only checked for *missing* hooks, never unwarranted ones.

### Added

- **Speculative extension surface (YAGNI) check.** wp-architecture-reviewer now reviews every hook the diff ADDS as the inverse of its missing-hooks check: a new public hook with no stated need and no named consumer is flagged as a permanent backwards-compatibility commitment, with a "For NEW Hooks Added by This Diff" checklist gate. Hooks with documented use cases, in-tree consumers, or an established sibling pattern are exempt.
- **Mixed abstraction levels smell.** architecture-reviewer flags methods that interleave high-level orchestration with low-level mechanics (SQL/string/array plumbing) and recommends extracting the low-level steps into named methods — with a concrete-symptom requirement (name the interleaving lines) so it cannot degrade into abstract refactoring notes.

### Changed

- simplification-reviewer's framework-convention exemption no longer implicitly clears new public hooks: using hooks is convention, introducing speculative ones is wp-architecture-reviewer's YAGNI territory (explicit boundary, no double-reporting).

## [1.109.0] - 2026-07-22

Lets the repository under review contribute its own review knowledge and reviewers. General reviewers structurally miss repo-specific bug classes (runtime-environment assumptions, upstream-internals contracts, failure-path semantics, cross-flow blast radius) — knowledge that is regression-seeded and can only live with the code. A repository now declares that knowledge, and its own domain-expert lenses, in an optional `review` section of its `.pirategoat/config.json`, and pirategoat applies and dispatches them natively.

### Added

- **Repo-contributed review rules.** A repo declares markdown checklists in `review.rules[]`. Bootstrap injects the ones applicable to each agent (by agent name, domain, or a changed-file path glob) as a fenced, demoted `REPO REVIEW RULES` block after the generic domain rules, so project standards override generic patterns. Repo-supplied bodies are treated as semi-trusted: a dynamically-sized fence plus a provenance/demotion banner prevents them from overriding the reviewer's output contract. `scripts/review/review_config.py` is the new single source of truth for parsing the section and for applicability matching.
- **Repo-contributed reviewers via a generic adapter.** A repo declares self-contained, pirategoat-agnostic reviewer prompts in `review.reviewers[]`. `plan_dispatch.py` expands each into a synthetic dispatch entry targeting the new `repo-reviewer-adapter` agent, gated by applicability like a conditional agent. The adapter runs the repo's prompt against the scoped diff (bootstrap ref-mode) and normalizes its findings into the standard format, so reconciliation, verification, and the verdict ingest them like any native lens. Per-instance output naming keeps N adapter instances from colliding. Inline execution ships in this release; isolated (headless, different model family) is reserved behind the `execution` flag.
- **Advisory channel.** Rules and reviewers can declare `"channel": "advisory"`. Advisory findings are listed but never gate the verdict (`_calculate_verdict` skips them; the reconciliation context and reconciliator preserve the channel). This gives judgment-call lenses (reuse, naming, boundaries) a separate precision budget without eroding the blocking channel's credibility. Native agents never set a channel, so behavior is unchanged for them.

### Changed

- `context.py` carries the parsed `review_config` into `review-context.json` (recomputed each run, alongside `host_context`). `schemas/review-output.ts` documents the optional `channel` field on `Issue`.

## [1.108.0] - 2026-07-21

Makes reviewer scope budgeting resilient and coverage gaps visible. A 2026-07-21 full-code-review on a test-heavy branch exposed a starvation failure: the largest changed file (a test file) was admitted unconditionally, blew the entire diff budget, and every remaining file — including all eight production files — was silently skipped. Six of seventeen agents reviewed zero production code and returned verdicts anyway; six findings were recovered only by manual re-runs.

### Fixed

- **One oversized diff no longer evicts the entire scope.** The protected oversized leading diff is tracked outside the ordinary budget pool, so later files still budget against the full `--max-lines` allowance. Coverage now degrades to "biggest file plus as many more as fit" instead of "one file".
- **Bootstrap resolves the plugin root from its own location first.** The `/tmp/.pirategoat-tools-root` hook cache previously outranked own-location discovery, so bootstrap could drive a different install's `scope.py` with flags that version doesn't understand — and the resulting argparse failure (exit 2) was conflated with scope's "no changes" signal, degrading reviews silently. Scripts that ship together now run together.
- **Reviewer protocol describes the real sort order** (priority tiers, largest-first within tier, one protected oversized file) instead of the stale "smallest-first" text, and makes NOT DIFFED handling mandatory: review each skipped file or declare it under `Not reviewed (budget):`.

### Added

- **Production-first budget priority.** The five mixed domains (`code`, `security`, `performance`, `wp-architecture`, `patterns`) now budget production files before test files (shared `_TEST_EXCLUDE` classifier; same tier mechanism as a11y's `markup_evidence`), so test-heavy branches can no longer spend the whole budget on test files while the code under review goes unseen. Test-only domains keep largest-first — test files are their evidence.
- **Per-agent scope summary sidecars.** `scope.py --summary-json-out` persists each agent's admitted/skipped file sets as `<agent>-scope-summary.json`; bootstrap wires it for primary and secondary domains.
- **Run-level inline coverage accounting.** Reconciliation aggregates the sidecars; files skipped by every matching agent surface as a prominent "Inline Diff Coverage Gaps" section in `reconciliation-context.md`, and pipeline step 9 deterministically injects a "Review coverage" instruction into the report briefing — a starved review can no longer present as a clean one.

## [1.107.1] - 2026-07-21

Hardens the reconciliation renderer against malformed reviewer output. A full-code-review aborted at the reconciliation step when a reviewer agent emitted a list-valued `recommendation`: the field flowed unchecked into `re.sub`, raising `TypeError: expected string or bytes-like object, got 'list'` and taking down the entire review. Reviewer JSON is model-authored, so a schema-string field can arrive as a list, number, or null — the pipeline must render it, not crash on it.

### Fixed

- **Non-string finding fields no longer crash the review.** `_escape_backtick_runs` and `_strip_critic_severity_floor_markers` — the two regex chokepoints every free-form finding field passes through — now coerce any value to a string first (lists join on newlines, `None` becomes empty), crash-proofing both `to_markdown` (reconciliation context, step 8) and `build_critic_context` (critic context, step 10) against malformed reviewer output.
- **Coerced titles can't inject Markdown structure.** Titles render inline (`**N. …**`, `### F1: …`) without block-syntax escaping, so a coerced multiline title could otherwise forge a heading or thematic break and split the structured context. Titles are now collapsed to a single line — at the producer and defensively at both render sites — collapsing every line ending (LF, bare CR, and CRLF, all of which CommonMark treats as line breaks), not just LF.
- **Legacy severity floors survive malformed descriptions.** `resolve_severity_floor` now coerces the description before scanning for a `Severity-floor:` marker, so a list-valued description no longer hits the non-string guard and returns `None` — which `load_agent_findings` would treat as "no floor" and drop, silently downgrading a mandatory floor during reconciliation.
- **Producer-side validation stops malformed findings at the source.** `ReviewOutputBuilder.add_issue` and `add_recommendation` coerce `title` (single-line), `description`, `recommendation`, and recommendation text to strings at write time, so a non-string value never reaches disk. Defense in depth alongside the renderer guard.
- **Reconciliation failures surface their root cause.** `reconciliation_context.py` now prints the full traceback to stderr on failure instead of only a terse message, so a future malformed-field abort names the offending field and finding instead of just "got 'list'".

## [1.107.0] - 2026-07-19

Makes review decisions evidence-driven end to end. The motivating full-code-review exposed three related gaps: server-rendered markup could miss accessibility review, small changes could dispatch broad reviewer cohorts without a concrete signal, and several agents repeating the same incomplete search could look like independent confirmation. This release turns those lessons into explicit dispatch, verification, and regression-review contracts.

### Added

- **Executable triage contracts.** Every conditional review criterion now has a minimal probe that must dispatch through the real planner with neutral commit and PR text. Completeness and neutrality checks keep registry prose, keywords, structural checks, and runtime behavior aligned. Language matrices document representative positive recognition without promoting detector silence into a negative inference.
- **Auditable clearance claims.** Reviewers can record blast-radius clearances with `add_clearance(claim, method, evidence)`. Clearances carry agent attribution and exact verification methods through the reconciliation context, making clearance-versus-finding conflicts visible and preventing unauditable "nothing depends on this" conclusions from quietly supporting approval.
- **Rendered markup as a review contract.** Accessibility scope now includes server-rendered markup and common template families (HTML/Twig/Mustache/Handlebars/ERB, EJS, Liquid, Nunjucks/Jinja, JSP, Razor, generic templates, FreeMarker/Velocity, Haml/Slim, and compound Blade files), prioritizes inherent template/style evidence within the diff budget, and recognizes explicit output, conventional renderer methods, and WordPress/WooCommerce helpers. The WooCommerce regression corpus adds a `markup-contract` invariant for CSS, JavaScript, tests, and other consumers that depend on rendered structure.

### Changed

- **Partial detector silence never authorizes a skip.** Keywords and structural checks remain positive-evidence accelerators, while conditional reviewers dispatch conservatively regardless of diff size. Explicit applicability gates remain separate and define an agent's scope rather than claiming exhaustive semantic detection; any future negative inference would require an executable completeness proof.
- **Verification strength follows method, not head count.** Correlated agent conclusions count as one probe, verdicts and severities cannot move on vote totals alone, and a negative search proves only that its exact pattern was absent. Reviewers must search dependency surfaces in their own vocabulary, enumerate relevant occurrences across whole artifacts, and report the methods behind absence claims.
- **Triage checks have one data-driven execution model.** Check metadata, diff requirements, and runner coverage are bound by guarded registries instead of parallel condition ladders. Shared helpers now own extension handling, markup classification, iteration coverage, and other repeated derivations; pure keyword and diff normalizations are cached for the lifetime of the planner process.

### Fixed

- **Keyword and patch matching now follow code and git structure.** Keywords respect identifier and camel-case boundaries, tolerate code separators, ignore repository-scaffolding path segments, and inspect changed lines rather than headers or unchanged context. Diff parsing handles spaces, non-ASCII paths, deletions, dash-prefixed content, and function context without confusing patch markers with source.
- **Detector silence is no longer mistaken for absence.** Failed diff fetches remain an explicit unknown and dispatch conservatively. Representative forms cover multiline declarations and imports, type bodies, public API changes, route registrations, SQL and HTTP-client calls, collection iteration, template composition, raw markup, and framework-generated UI; silence remains unknown rather than becoming a skip.
- **Accessibility evidence survives broad backend diffs.** Pure template and stylesheet changes are inherent UI evidence, and markup-bearing files are budgeted before large unrelated backend files. PHP/PHTML changes dispatch conservatively even when finite renderer and markup recognition is silent, covering arbitrary composition surfaces without pretending a helper vocabulary proves irrelevance. Centralized template language and suffix definitions drive domain scope, dispatch, and budget priority—including Razor, Nunjucks/Jinja aliases, and `*.blade.php` compound suffixes. Comment-aware code/content classification recognizes PHP short echo, core WordPress renderers, and view-like output methods without treating quoted prose, event emitters, or byte streams as UI.
- **Generic structural words no longer masquerade as domain evidence.** Terms such as `function`, `class`, `remove`, and `config` were removed from reviewer keyword lists where structural checks can establish the signal directly, reducing accidental specialist dispatch without weakening documented criteria.

### Tests

- **All plugin suites can run in one pytest session.** Test trees no longer declare a shared top-level `tests` package, and the root configuration pins importlib mode so plugin conftests and same-named modules cannot collide. A repository guard prevents package markers from being reintroduced; current release verification passes 3,518 tests together, with 24 skipped.

## [1.106.0] - 2026-07-16

Fixes silent reviewer-finding loss discovered by root-cause-analyzing 1,825 reviewer subagent transcripts from the last 60 days: 8 agents / 11 findings — including HIGHs in real WooCommerce and WPCOM PR reviews — were silently demoted from verdict-counting issues to informational observations because `add_issue()` redirected any `line=None` finding to `add_observation()` with no signal.

### Fixed

- **Line-less findings are now first-class, verdict-counting issues.** `ReviewOutputBuilder.add_issue(line=None)` records a file-scoped issue (`line: null`, `scope: "file"`) that counts toward `by_severity` and the verdict and renders under its severity section in Markdown, instead of silently demoting to a non-counting observation that the reconciliation stage never sees. Finding classes that are line-less by nature — missing test coverage, missing assertions, git-history precedent, cross-file architecture — now survive the pipeline. The path is loud: a stderr NOTE names the recorded title and severity so accidental `line=` omission for point defects stays visible. Invalid lines (0, negative, non-int) still raise, and the diff-anchored norm still requires `line=` for findings that have one (protocol, bootstrap output instructions, and reconciliator guidance updated accordingly; `schemas/review-output.ts` documents the additive `scope` field).

- **Info issues now render in Markdown.** `to_markdown()` iterated only critical/high/medium/low, so any `info` issue — file-scoped or line-anchored — was counted in `Total Issues` but omitted from the document entirely (a pre-existing gap made visible by file-scoped issues, which previously surfaced under Observations). The severity loop now covers every severity that counts toward the total.

- **The fallback (non-bootstrap) protocol path now goes through `save()`.** The shared protocol's File-Based Output example instructed manual `to_json()`/`to_markdown()` writes, which skip the RECORDED COUNTS echo and leave fallback agents reporting counts from intent — the exact failure mode the echo exists to prevent. The example and the core-methods list now route output through `builder.save()`, and the `/tmp/` collision guidance uses a timestamped directory so the single `save()` path serves it too.

- **`save()` now echoes the recorded state to stdout.** After writing the JSON/Markdown pair, the builder prints `RECORDED COUNTS: critical: N, high: N, ...` plus total issues, observation count, and verdict — so the true saved state is visible in the agent transcript. The RCA showed agents composing their final `COUNTS:` from intent rather than saved output, which masked the demotion bug for 60 days; reviewer agents are now required to reconcile their reported COUNTS against this echo before declaring FINISHED.

### Changed

- **Reviewer protocol hardens builder invocation and self-reporting.** Reviewer agents must drive `ReviewOutputBuilder` via a written script file or heredoc — never inline `python3 -c "…"` with finding prose (18% of 1,825 scanned agent runs used inline `-c`; ~10% of those crashed on apostrophes/em-dashes in finding text) — and must copy the final `COUNTS:` return signal from `save()`'s `RECORDED COUNTS` echo, investigating any mismatch with intent before returning `STATUS: FINISHED`. Applied in both the bootstrap output instructions (bootstrap-driven agents) and the shared reviewer protocol (fallback path).

### Consumer audit

- All `issues[]`/`line` readers verified tolerant of `line: null` before the schema change: reconciliation context builder (keeps line-less issues conservatively, renders file-only locations, skips snippet gathering), agents status, telemetry, critic context, compliance graders (`line` not required), and pirategoat-bot (checks only file existence of `*-review.json`; parses no issue contents — no bot change needed).

## [1.105.1] - 2026-07-15

### Fixed

- **Parallel reviewers now retain their own large scoped diffs.** Bootstrap previously spilled every scope over 15 KiB to one run-global `scoped-diff.patch`, so a later parallel bootstrap could replace the domain diff an earlier reviewer had been instructed to read. Spill files are now namespaced by reviewer agent, and reused review directories clean both the namespaced files and the legacy shared filename before a new run.

### Tests

- Added regression coverage proving two reviewers sharing an output directory receive distinct, domain-correct spill files, plus cleanup coverage for both filename generations.

## [1.105.0] - 2026-07-15

Hardens the review pipeline against unverified-dismissal misses, prompted by a shipped case (woocommerce/woocommerce#66488 → #66613) where a detected regression was demoted to "narrow and acceptable corner" tradeoff prose: the collision was framed as coincidental when the upstream producer made it systematic for an entire store-configuration class.

### Added

- **`woo-regression-reviewer` invariant 11: heuristic proxy predicates vs. store-configuration variance.** When a change gates behavior on a proxy inferred from persisted state shape ("zero shipping lines ⇒ virtual order", "meta key absent ⇒ feature unused", "field equality ⇒ derived copy"), the agent must enumerate every writer of the compared state and every supported configuration under which the proxy diverges from intent — a guard that is guaranteed-true under some supported configuration is not a narrowing guard for that population, and "coincidental" co-occurrence must be verified at the producers. Adds a per-hunk audit row (so soft dismissals reach the self-audit), a `proxy-predicate` finding category, and updates the agent description/registry focus. Neither this prompt set nor the upstream ai-regression-review checklist previously encoded this failure class; the ai pipeline caught #66613 only via its generic-correctness remit plus triage verification.

### Changed

- **Dismissal and mitigation verification now applies to every finding, not just floored/regression-category ones.** The reconciliator gains a general "Dismissal & Mitigation Discipline" contract: frequency claims ("unlikely", "rare in practice", "narrow corner", "coincidental") justify nothing without a cited file:line structural reason; "coincidental" co-occurrence must be verified by reading the code that writes the compared values — with upstream-producer tracing beyond the pre-gathered snippets explicitly sanctioned; and mitigation claims must be verified at file:line for the cited input shape before they can dismiss or downgrade a verified concern.
- **"Tradeoffs Identified" now has exit criteria.** A tradeoff entry must state its trigger condition, a file:line-verified affected-population claim, and why the compromise is intentional. A tradeoff with an unverified likelihood claim is emitted as a Low/Medium finding via `add_issue()` instead of prose, so it survives as an actionable item.
- **The report-synthesis step forbids prose demotion.** Both default output-instruction sets (PR and branch modes) now require carrying every reconciled finding into the report as a finding and prohibit asserting unverified likelihood claims as fact.
- **Change purpose is now presented as claims-to-verify, not context-to-adopt.** The change-purpose.md handoff requires attributing intent to its source and keeping author-asserted discriminators recognizable as claims; the reconciliation context Markdown carries a claims-to-verify preamble; the reconciliator's context contract states that a finding is not wrong for contradicting the author's framing; and the step-8/step-9 briefings label the purpose as author-stated with the reconciled findings as the source of truth. This de-anchors reviewers and synthesis from persuasive PR descriptions — in the #66488 case the PR body pre-framed "no persisted shipping line" as the correct virtual-order discriminator, and the review adopted the framing instead of testing it.

### Tests

- Prompt-contract coverage for the reconciliator's general dismissal discipline, upstream-tracing sanction, and tradeoff exit criteria; step-9 briefing coverage for the prose-demotion prohibition in both modes.

## [1.104.1] - 2026-07-12

### Fixed

- **Reviewer filesystem discovery is now bounded.** The shared reviewer protocol prohibits recursive searches from `/` and `$HOME`, limits discovery to explicit repository, Host Context, dependency, configuration, and selected-sibling roots, and requires reviewers to stop instead of widening the search. `ecosystem-integration-reviewer` adds a concrete one-pass upstream lookup order and falls back to RULE 0 (omit unverifiable findings) when source remains unavailable, preserving advisory Host Context behavior without allowing whole-filesystem scans.

### Tests

- Added regression coverage for the shared bounded-root contract in generated reviewer prompts and the ecosystem reviewer's ordered discovery and clean-exit behavior.

## [1.104.0] - 2026-07-10

Adds a WooCommerce-focused regression-invariants reviewer, ported from the production AI regression-review pipeline's tuned WooCommerce prompts, so regression classes that shipped in the WC ecosystem are caught pre-merge instead of post-merge.

### Added

- **`agents/woo-regression-reviewer.md`** — reviews WooCommerce core/extension changes against corpus-derived ecosystem invariants (Action Scheduler traps, meta equality/sync-on-read write loops, template/theme overrides, broken-until-JS defaults, filter return-type variance, PHP 8.4 coercion, migration legacy state, interface/hook contract breaks). Uses a mandatory per-hunk invariant audit and a self-audit pass that promotes soft dismissals ("pre-existing", "unlikely", "guarded elsewhere") to Medium findings. Dispatch is gated on WooCommerce signals via `require_triage_keyword_match` (first consumer of that mechanism) plus `require_php_source_file` — non-WooCommerce repos triage the agent out.
- **Severity floors are now structured end-to-end.** `ReviewOutputBuilder` validates and enforces optional `severity_floor` metadata, reconciliation context normalizes the two current reason-only markers for backward compatibility, and the reconciliator carries the strongest verified floor into its own output and decision-critic context. Legacy inference is confined to the agent-input boundary, and critic context strips recognized markers—including rejected malformed syntax—from every rendered free-text channel, so a floor rejected during reconciliation cannot reappear from stale prose. Regression categories still trigger strict mitigation verification but no longer fabricate a numeric floor.

### Fixed

- **Not-applicable reviewers now complete through one persisted contract.** Agent-local applicability gates defer to the shared reviewer protocol's mark → save → `STATUS: FINISHED` sequence. Bootstrap includes that shared Quick Relevance sequence in every generated prompt instead of skipping it with scope-discovery instructions, and integration tests verify the generated contract while rejecting duplicated agent-local `mark_not_applicable(...)` calls that could drift into unsaved exits.
- **WooCommerce extension triage now includes opt-in repository identity across every fetch remote.** The dispatch planner supports source-specific `triage_repository_keywords`, which `woo-regression-reviewer` matches against all fetch remote URLs plus the Git top-level name. Extension-native WooPayments and AutomateWoo changes now reach the reviewer even when `origin` is a renamed fork or mirror, a canonical `upstream` carries the WooCommerce identity, and paths, commit/PR text, and patch contain no generic WooCommerce keywords. Push-only destinations are excluded, and ambient identity stays out of generic `triage_keywords`, so unrelated reviewers and quick-mode exclusions cannot be re-enabled by checkout-wide signals.

### Tests

- `tests/review/test_plan_dispatch.py` — WC-signal dispatch, repository-identity dispatch for WooPayments and AutomateWoo, non-WC skip, PHP-source gate, and quick-mode isolation from ambient repository signals.

## [1.103.0] - 2026-06-10

Fixes a systematic blind spot in reviewer scoping: non-web languages were invisible to the review domains. On a pure-Rust repo, 12 of 16 production-code domains returned `NO_DOMAIN_FILES` — `security-reviewer` never read `src/auth/` and signed off after reviewing only the CI workflow. Root cause was 16 independently hand-maintained per-domain extension regexes that covered only the web/WordPress stack; `.rs` was wired into the Rust *test* domains but never the production-code ones (`.cs` had the same partial gap). Centralizes language recognition into a single source of truth and adds a fail-loud safety net for languages the catalog still doesn't cover.

### Fixed

- **Reviewer domains now recognize all mainstream languages.** `review/agent/scope.py` composes every general-purpose code domain's include pattern from shared language-group constants (`_PROG_LANGS`, `_STYLE_LANGS`, `_QUERY_LANGS`, `_DOC_LANGS`, `_DATA_LANGS`, `_FRONTEND_LANGS`) instead of 16 separate hand-maintained regexes. Coverage broadens from the web/WordPress stack to Rust, C/C++, C#, Kotlin, Swift, Scala, Objective-C, Elixir, Clojure, Haskell, F#, Lua, Perl, Dart, Vue, Svelte, shell, and more. Adding a language is now a one-line edit picked up by every domain — eliminating the partial-addition drift that caused the Rust (and C#) gaps. Test-reviewer domains, `wp-architecture`, `a11y`, `toolchain`, and `config-ops` keep their intentionally-curated patterns.
- **Secondary-domain masking now produces an honestly-scoped verdict (defense in depth).** When an agent's primary domain matched no files but a secondary domain did (e.g. a CI-only change for `security-reviewer`), bootstrap previously emitted a contradictory prompt — a top-level `NO_DOMAIN_FILES` (telling the agent to exit) above an appended secondary-scope section with real content. The agent would either drop the secondary files or review them with a verdict indistinguishable from a full domain review. `review/agent/bootstrap.py` now resolves this (`resolve_overall_status`): it flips the status to a scoped `OK` so the secondary files are actually reviewed, and injects a `=== COVERAGE NOTE ===` that requires the agent to scope its verdict — an APPROVE means "no issues in the secondary files," not that primary-domain code was reviewed. This makes a coverage gap visible even if the language catalog ever regresses again.

### Added

- **Fail-loud safety net for unrecognized source languages.** `review/plan_dispatch.py` detects changed source files whose language no reviewer domain covers (`detect_unrecognized_source`) and surfaces a prominent `UNRECOGNIZED SOURCE` warning in the dispatch plan (`scope_summary.unrecognized_source` + `warnings[]`). The pipeline renders it at the top of the Step 5 briefing, so a coverage gap can no longer masquerade as a clean review. Uses a deliberate superset of the actively-reviewed languages so the next exotic language (e.g. Solidity, Nim) is flagged even before the catalog learns it.

### Tests

- Added language-coverage regression tests in `tests/review/agent/test_scope.py` (`.rs` across all production domains, plus Kotlin/Swift/C++/C#/Scala; `rust-test-dirs` still excludes `src/`) and safety-net tests in `tests/review/test_plan_dispatch.py` (`TestDetectUnrecognizedSource`, `TestSafetyNetInPlan`).

## [1.102.0] - 2026-05-13

Adds UX-review capabilities to the `browser-interaction` skill so review-class agents can reach specific page states and exercise interaction disciplines without per-agent improvisation. Plus pipeline orchestration reliability fixes for waiting-state persistence, context gathering, degraded host context, Linear routing, and registry-driven dispatch checks.

### Added

- **`skills/browser-interaction/SKILL.md` — Capability Patterns section.** 10 patterns covering page states (responsive, theme, loading, error, accessibility) and interaction disciplines (keyboard, scroll, focus, multi-user sign-in, log inspection). Each pattern maps Chrome DevTools and Playwright invocations alongside verification anchors; a top-of-section Capability Index table gives directional hints for pattern selection. RULE 0 gains a contrastive WRONG/RIGHT example showing the uid-reuse-after-navigation failure mode. Centralizes browser tooling knowledge for `frontend-ux-reviewer`, `e2e-tests-reviewer`, and future browser consumers — capability gaps become a single point of maintenance instead of fanning out to per-agent system prompts.

### Fixed

- **Step 8 WAITING state now persists and blocks routing.** The review pipeline records `first_waiting_at` before writing `pipeline-state.json`, does not mark Step 8 complete while agents are still running, and renders a distinct `PIPELINE WAITING` footer instead of the terminal completion sentinel until reconciliation is safe to run.
- **Context gathering can outlive dependency installs.** The Step 3 wrapper timeout now exceeds `ensure_installed.py`'s 20-minute per-manager timeout, so normal Composer/npm installs are not killed by the outer pipeline.
- **Install-cache failure banners survive host-context rebuilds.** `review/context.py` now preserves `ensure_installed.py`'s `install_failed` banner in `host_context.banner` when dependency-source verification degrades.
- **Linear small-fix routing now affects active steps.** The Linear pipeline reads `complexity.json` into state and skips Self-Review/Re-Verify steps for `small` fixes, matching Step 11 guidance.
- **Registry triage checks fail fast when unsupported.** `plan_dispatch.py` now validates `triage_checks` before domain filtering and implements the structural checks currently declared by the registry.

### Tests

- Added regression coverage for Step 8 waiting persistence/routing, context timeout sizing, host-context install banners, Linear complexity routing, and registry triage-check validation.

## [1.101.2] - 2026-04-27

Cache-based fulfillment for unresolved upstream hosts, plus a new resolver that reads WordPress plugin/theme headers as host-need declarations. Together: a repo that declares WordPress and WooCommerce as dependencies — via `Requires at least`, `WC requires at least`, or `Requires Plugins:` — gets both fulfilled from the ecosystem cache automatically, even on a fresh clone with no local sibling checkouts and no committed `docker-compose.override.yml`.

### Added

- **`scripts/hosts/cache/manager.py` — `ensure_fresh(name, max_age_seconds=86400)`.** TTL-gated wrapper around `update_host()`. Returns `{"action": "fresh"}` without git work when the slot's `.last_updated` marker is within the window; otherwise calls `update_host()` synchronously. 24-hour default amortizes the pull cost across a day's reviews while keeping integration findings honest.
- **`scripts/hosts/resolvers/ecosystem_cache.py` — `EcosystemCacheResolver.resolve_for_names(names)`.** Fulfillment-mode method that emits cache entries only for explicitly requested names (filtered against `_KNOWN_HOSTS = {"wordpress", "woocommerce"}`), refreshing each via `ensure_fresh()` first. Unknown names are silently dropped. Confidence is `high` because the slot is guaranteed within the freshness window after refresh. Empty-cache slots produce `unresolved` entries with `reason: "cache_unpopulated"`.
- **`scripts/hosts/chain.py` — post-loop fulfillment pass.** After all `_DEFAULT_RESOLVERS` run, the chain calls `EcosystemCacheResolver().resolve_for_names()` with names from the unresolved list, pre-filtered to exclude any `runtime-host:<name>` already in `seen_names`. The pre-filter is critical — it prevents `ensure_fresh()` (a potential network call) from firing for hosts a higher-priority resolver already won. Fulfilled entries promote to `resolved` and clear from `unresolved`. Diagnostics record `ecosystem-cache-fulfillment` in `resolvers_consulted` only when fulfillment actually fires.
- **`scripts/hosts/resolvers/plugin_headers.py` — `PluginHeadersResolver`.** Reads the standard WP plugin/theme header block from the repo's main file and emits declared dependencies as unresolved entries. Parses `Requires at least` (WP minimum) → unresolved `wordpress`, `WC requires at least` (WooCommerce convention registered via `extra_plugin_headers`) → unresolved `woocommerce`, and `Requires Plugins:` (WP 6.5+ comma-separated wp.org slugs) → unresolved entries per slug with a `fulfillable` flag. Theme detection via `style.css` `Theme Name:`. The chain's cache-fulfillment pass then satisfies known ecosystem hosts; unfulfillable declared deps (e.g. `Requires Plugins: jetpack`) surface in the manifest banner so the reviewer knows source is missing. Position in the default chain is after `DockerComposeResolver` — operational mounts that actually resolve to a path on disk win over static header declarations; header decls fill gaps the operational config didn't cover (fresh-clone bot environment, repos without committed compose overrides).

### Changed

- **`scripts/hosts/resolvers/docker_compose.py` — `core` self-mount semantics.** When a `/var/www/html` (core) target binds to a path inside the repo but not equal to repo root (e.g. WooPayments' `./docker/wordpress:/var/www/html/`), the resolver now emits an `unresolved` entry with `reason: "vendored_self_mount"` instead of silently dropping. This surfaces the WP need so cache fulfillment can satisfy it. Plugin/theme self-mounts and `core` mounts where source equals repo root keep their silent-skip behavior — those are "repo IS the host" or monorepo subdirectories, not vendored upstream.

### Tests

- New: `tests/hosts/resolvers/test_docker_compose.py` — `core` self-mount inside repo emits unresolved; `core` self-mount where source equals repo root silent-skips; plugin self-mount in subdirectory stays silent.
- New: `tests/hosts/cache/test_manager.py` — `ensure_fresh` no-ops within window, calls `update_host` when slot missing or stale, respects custom `max_age_seconds`.
- New: `tests/hosts/resolvers/test_ecosystem_cache.py::TestResolveForNames` — empty input no-ops; unknown names filtered; fresh cache returns `confidence: "high"` with `notes.fulfillment: True`; empty cache slot returns unresolved with `reason: "cache_unpopulated"`.
- New: `tests/hosts/test_chain.py::TestCacheFulfillment` — fulfillment promotes unresolved → resolved with `source: "ecosystem-cache"`; diagnostics record `ecosystem-cache-fulfillment`; empty repo + populated cache produces no fulfillment (no leakage); pre-filter skips fulfillment when higher-priority resolver already won (no spurious `update_host` calls); cache offline + missing slot → banner falls back to `fully_unavailable`.
- New: `tests/hosts/resolvers/test_plugin_headers.py` — `Plugin Name` detection, `Requires at least` → wordpress unresolved, `WC requires at least` → woocommerce unresolved, `Requires Plugins:` per-slug emission, dedup across header forms, theme detection via `style.css`, WooPayments-style end-to-end header block.
- New: `tests/hosts/test_chain.py::TestPluginHeadersIntegration` — fresh-clone WooPayments simulation: only committed `docker-compose.yml` (WP self-mount) + plugin file with WC headers + populated cache → both WP and WC resolved via fulfillment, banner null. Local-dev case: `docker-compose.override.yml` mounting WC sibling wins over plugin-headers' declaration (no spurious `update_host` call). Unfulfillable declared dep (`Requires Plugins: jetpack`) surfaces in the partial-unresolved banner.
- Updated: `tests/hosts/test_chain.py::test_partial_unresolved_sets_banner` — now mocks `update_host` to block network so the partial-unresolved banner path is exercised without fulfillment rescuing the entry.
- Updated: `tests/hosts/test_chain.py::test_diagnostics_records_which_resolvers_ran` — expanded expected resolver set to include `plugin-headers`.

## [1.101.1] - 2026-04-27

Behavioral-alignment scope for `ecosystem-integration-reviewer`. The reviewer now also catches mismatches where the wiring is shape-correct but the downstream code's runtime expectations contradict upstream's runtime behavior at the same site — state assumptions, timing/lifecycle, return-value semantics, side-effect ordering, implicit pre/post conditions. The standalone "Lifecycle reasoning" section folds into this broader category.

### Added

- **`Behavioral assumption alignment` check class** in `agents/ecosystem-integration-reviewer.md`. Distinct from the three shape-correctness checks: requires two citations (downstream assumption site + upstream behavior site), forbids speculative findings, and surfaces five categories — state assumptions, timing/lifecycle, return-value semantics, side-effect ordering, implicit pre/post conditions. Includes worked CORRECT/INCORRECT examples mirroring the canonical shape-check sections.

### Changed

- **`ecosystem-integration-reviewer` description and registry `focus`** broadened to cover behavioral-alignment scope (kept in sync per `agents/<name>.md` description ↔ `agent_registry.json` focus rule).
- **`lifecycle_confidence` field renamed to `behavior_evidence`** in `scripts/review/agent/output.py` and `schemas/review-output.ts`. The field captures "how was upstream behavior determined" — generic to any behavioral claim, not timing-specific. Enum tightened from `('cited' | 'inferred' | 'speculative')` to `('cited' | 'inferred')` since `speculative` was already disallowed by the "inferred or higher" rule. New `behavior-assumption` value joins the finding `category` enum, replacing `lifecycle`. Findings with `category: "behavior-assumption"` should set `behavior_evidence` and provide both downstream (`file:line`) and upstream (`source_cited`) citations.
- **`tests/grading/test_graders.py`** renames lifecycle_confidence assertions to behavior_evidence and adds a regression test that `speculative` is rejected.

## [1.101.0] - 2026-04-27

Upstream host context for the review pipeline. Reviewer agents now receive advisory paths to upstream runtime hosts (WordPress core, WooCommerce, bundled libraries) and library-dep roots on disk, and a new specialist agent (`ecosystem-integration-reviewer`) verifies integration correctness against that source. Two cache layers populate dependencies opportunistically — a per-clone library-dep install cache during review setup, and a machine-wide WordPress + WooCommerce source cache the bot updates on its own cadence.

### Added

- **Host-context discovery.** `scripts/hosts/host_context.py` runs a resolver chain (`.pirategoat/config.json` → `.wp-env*.json` → `docker-compose*.{yml,yaml}` → per-clone install cache → `vendor/` / `node_modules/`) and writes `host-context.json` under the review output directory. Each resolver reads local filesystem signals and emits `HostEntry` records without side effects. Self-mounts inside the reviewed repo are skipped so first-party code is never reported as upstream. `EcosystemCacheResolver` and `SiblingResolver` exist as standalone helpers but are excluded from the default chain to prevent ambient machine layout from being treated as authoritative.
- **`ecosystem-integration-reviewer`** agent. Conditionally dispatched when `host-context.json` resolves a runtime-host AND the diff contains PHP integration patterns. Owns integration-correctness checks: filter/action callback signatures vs upstream `apply_filters` / `do_action` call sites, override compatibility against parent classes (abstract / final / visibility / signature), REST route schemas vs controller method usage, and the same declaration-meets-contract test generalized to function calls into upstream APIs, registration APIs (`register_block_type`, `register_post_type`, `register_setting`, `register_taxonomy`), and constants/options with upstream-defined names. Discipline: every claim about upstream behavior cites a specific upstream `file:line` (a `Citation form` rule keeps reviewer setup details out of reports — citations are upstream-relative and reproducible). Both presence and absence are in scope when grounded in source; uncited speculation is not.
- **Per-clone library-dep install cache.** `scripts/hosts/ensure_installed.py` populates `~/.cache/pirategoat/library-deps/<clone_id>/<manager>/` for composer / npm / pnpm / yarn, atomically replacing the slot when the lockfile content changes and never modifying the working tree. Mandatory `--ignore-scripts` / `--no-scripts` / `--no-plugins` are bracketed on both sides of any user `extra_args`, and the override parser rejects arguments that could subvert script-blocking. `js.manager` is allowlisted to `{npm, pnpm, yarn}` and the `env` allowlist accepts only `NODE_AUTH_TOKEN` plus standard `COMPOSER_*` / `NPM_*` / `PNPM_*` / `YARN_*` prefixes. Frozen-lockfile flags are mandatory for JS managers. Known-failure retry table handles `EBADENGINE` and `ERESOLVE`. Opportunistic stale-clone GC removes cache entries whose recorded `.realpath` marker no longer exists. Install populate runs automatically during `review/context.py` step 3 (Gather Context); `InstallCacheResolver` then emits library-dep entries pointing at the cache directory. Best-effort — install failures degrade host-context with a banner but do not block the review.
- **Ecosystem source cache.** `scripts/hosts/ecosystem_cache.py` manages a machine-wide cache of WordPress core and WooCommerce source at `~/.cache/pirategoat/ecosystem/<name>/latest/` with `--update` / `--list` / `--verify`. Subprocess failures (`TimeoutExpired`, missing git) return structured errors instead of tracebacks; an advisory lock per host serializes concurrent updates. The plugin is oblivious to the caller — bot, CI, or interactive user — and only consumes the cache via `EcosystemCacheResolver` when invoked directly.

### Changed

- `scripts/review/context.py` — `load_and_fill()` enriches `review-context.json` with a `host_context` block at step 3, gains `repo_path` kwarg and `--repo-path` CLI flag for host discovery (falls back to CWD), populates the install cache before host resolution, and recomputes host context on every run instead of preserving stale absolute paths from reused output directories. The `hosts.chain` import is lifted to module load.
- `scripts/review/agent/bootstrap.py` injects a HOST CONTEXT section into reviewer prompts between REVIEWER-REQUESTED FOCUS and REVIEW BUDGET. Repo-derived values (host names, paths, banner messages) are JSON-string-serialized so they cannot break out of Markdown bullets. Resolved entries cap at 20 per kind and unresolved at 10, sorted by name with a `(+N more)` tail.
- `scripts/review/reconciliation_context.py` and `schemas/review-output.ts` propagate a `HostContextBanner` through reconciliation into final review output. The Markdown briefing uses collision-safe fences when the banner contains backticks.
- `scripts/review/plan_dispatch.py` adds `host_context_runtime_host_resolved` as a registry-driven dispatch gate, includes patch-body integration patterns as a triage keyword source so hook calls dispatch the reviewer even when commit metadata is generic, and memoizes domain-scoped patch text across conditional agents.
- `scripts/review/agent/output.py` and `schemas/review-output.ts` — `ReviewOutputBuilder.add_issue()` and `Issue` gain optional `lifecycle_confidence` (`cited` | `inferred` | `speculative`) and `source_cited` (`<file>:<line>`) fields for reconciliator-side weighting of timing claims.
- `agents/shared/reviewer-protocol.md` adds a Host Context Usage section. Paths are advisory starting points, not exhaustive inventories — reviewers continue local exploration when needed and never edit paths under `~/.cache/pirategoat/library-deps/`.
- `agents/review-reconciliator.md` propagates the host-context banner into final review output.
- `plugins/pirategoat-tools/requirements-dev.txt` (new) — documents PyYAML as a test dependency for docker-compose resolver coverage.

## [1.100.1] - 2026-04-14

### Changed
- **devils-advocate-reviewer prompt optimized.** Unified Gate/Step numbering into consistent Step 1-5, restructured evidence constraint as affirmative RULE 0, fixed contradictory Step 2 language, normalized "no finding" exit as expected outcome, rebalanced confidence scoring (start at 80, floor 85), balanced stakes with false-positive cost, connected Step 1 to bootstrap output.
- **simplification-reviewer prompt optimized.** Added RULE 0 (framework conventions are not over-engineering), balanced stakes with false-positive cost, connected Step 1 to bootstrap output, expanded complexity categories into scannable list, converted confidence modifiers to table with -15 penalties for framework-related reducers.

### Fixed
- **Production `*Page.ts` files no longer get misrouted as E2E tests.** Tightened the `e2e-tests` scope heuristic so it only matches Playwright/E2E-owned paths instead of any filename ending in `Page.ts` or `PageObject.ts`. This fixes both over-dispatch to `e2e-tests-reviewer` and under-dispatch in conditional reviewers that rely on test-file detection.
- **devils-advocate-reviewer triage now matches its registry criteria.** Large architecture-scope production changes dispatch again even without hard-coded keywords. The reviewer now uses structural triage for new abstraction-shaped files plus an explicit substantial-non-test-additions signal, instead of treating the keyword list as a strict gate.
- **`min_added_lines` now counts in-scope production additions.** The 50-line gate for `devils-advocate-reviewer` now uses non-test additions in the reviewer’s own scope, preventing test-heavy or docs-heavy PRs from dispatching it based on unrelated diff volume.
- **Renamed files now preserve per-file addition counts in triage.** `git diff --numstat` rename entries are normalized to the post-rename path before `file_stats` is recorded, so reviewer thresholds still see added lines on rename-heavy refactors.
- **Quick mode now actually skips `simplification-reviewer`.** Quick reviews now exclude low-signal blocklisted agents even when they are `dispatch_class: "always"`, while still honoring stronger explicit triage signals for conditional agents.
- **SQL migrations now reach architecture triage.** Expanded the `architecture` scope domain to include `.sql` files so database schema and migration changes can trigger architecture-oriented reviewers, including `devils-advocate-reviewer`.
- **devils-advocate database/infrastructure signals strengthened.** Added `database`, `schema`, and `table` triage keywords to better catch infrastructure PRs whose intent is expressed in file paths, commit messages, or PR text.

## [1.100.0] - 2026-04-14

### Added
- **simplification-reviewer** — new always-on domain reviewer that catches unnecessary complexity: over-abstraction, premature generalization, defensive code for impossible cases, unnecessary indirection, and verbose logic. Every finding includes a concrete simpler alternative with line-count comparison.
- **devils-advocate-reviewer** — new conditional domain reviewer (opus tier) that questions the fundamental approach of substantial PRs (50+ added lines). Three-gate methodology: identify approach, search for reframing, pass strict evidence test. 85+ confidence floor.
- **Simplification bias principle** added to shared reviewer protocol — all agents now have permission to recommend simplification within their domain
- New `simplification` domain in scope.py — all production code file types excluding tests
- New `min_added_lines` registry field for dispatch gating — agents with this field set are skipped when PR additions fall below threshold

## [1.99.0] - 2026-04-08

### Added
- **reference-integrity-reviewer** — new domain reviewer agent that verifies references in code actually resolve to existing targets. Checks plugin slugs against declared registries (WordPress.org, npm, etc.), verifies asset files exist at declared paths, validates URLs in configuration, and confirms constant/class references resolve. Uses a three-step resolution cascade: verify on declared target, search broadly if not found, advisory for unreachable private systems. Inspired by a CodeRabbit finding on woocommerce/woocommerce#64059 where a plugin slug was declared as `PLUGIN_TYPE_WPORG` but only existed on WooCommerce.com Marketplace.
- New `reference-integrity` domain in scope.py — includes code + config files (JSON, YAML), excludes tests

## [1.98.1] - 2026-04-08

### Changed
- Telemetry log filenames now use a structured format: `<mode>-<repo_slug>-<identifier>-run<N>--<timestamp>.jsonl` (e.g., `pr-Users-vladolaru-Work-a8c-woocommerce-payments-64051-run1--20260408T075342.jsonl`). Replaces the old approach of deriving names from the output directory basename, which produced non-descriptive filenames like `first--<ts>.jsonl` for bot-mode reviews.
- `ReviewTelemetry.start()` accepts new `mode`, `repo_path`, and `identifier` parameters. Falls back to output dir basename for legacy callers.
- Added `ReviewTelemetry.path_to_slug()` classmethod for converting absolute paths to filename-safe slugs

## [1.98.0] - 2026-04-07

### Changed
- Renamed `pr-reviewer` agent to `code-reviewer` to reflect its role as the generalist code reviewer across all review modes (PR, full, incremental)
- Renamed telemetry log directory from `~/.pirategoat-tools/logs/pr-reviews/` to `~/.pirategoat-tools/logs/reviews/` — move existing logs manually if needed

### Fixed
- Telemetry logs from test runs no longer pollute `~/.pirategoat-tools/logs/reviews/` — added session-scoped `PIRATEGOAT_TELEMETRY_LOG_DIR` isolation in conftest

## [1.97.0] - 2026-04-06

### Added
- **Curated critic context document.** The decision critic now receives a single Markdown file (`critic-context.md`) with the review report, sequentially-IDed findings (F1, F2, ...), recommendations, and reconciliation metrics — instead of reading raw `review-findings.json` + a separate report. Mirrors the reconciliator's curated Markdown pattern for ~40% token savings on the Opus-tier critic agent.

### Changed
- `critic.py` accepts `--context` flag replacing the removed `--findings-json`.
- Pipeline step 10 builds `critic-context.md` before dispatching the decision critic.
- Decision reviewer agent definition updated for the new single-file input contract with `critic-context.md` structure documentation.
- Critic step 1 now reuses the pre-assigned finding IDs (F1, F2, ...) from the context document instead of inventing new ones, eliminating the ID namespace collision between findings and factual claims.

## [1.96.2] - 2026-04-06

### Changed
- **Agent status line colors.** Updated colors for concurrency-reviewer (magenta → orange), a11y-reviewer (green → pink), and docs-drift-reviewer (yellow → pink).

## [1.96.0] - 2026-04-06

### Changed
- **Reconciliation context: Markdown format for LLM consumption.** The reconciliation context is now written as both Markdown (`reconciliation-context.md`) and JSON (`reconciliation-context.json`). The reconciliator agent reads the Markdown version — structured with section headers, tables, and fenced code blocks — which is ~40% more token-efficient and eliminates the 11-chunk Read pattern observed with the JSON format (down to ~3-5 reads). JSON is retained as a data artifact for debugging and tooling. Pipeline step 8 briefing now points to the `.md` file and provides the output builder path explicitly.

### Fixed
- **Markdown context: full changed-file list preserved.** `to_markdown()` was truncating the changed-files list to 20 entries. The reconciliator uses this list for in-scope decisions ("file not in this list → out of scope"), so findings on file 21+ were silently misclassified as out-of-scope.
- **Markdown context: safe fencing for source snippets.** Source snippets containing triple backticks (common in `.md` file reviews) would close the outer ``` fence early, corrupting all subsequent sections. Fences now dynamically size to be longer than any backtick run in the snippet.
- **Markdown context: recommendations preserved.** `to_markdown()` was skipping `recommendations` (immediate/important/suggestions). These now render in the Markdown output. Observations are intentionally excluded — they bypass the scope/snippet pipeline and would give the reconciliator unverified claims.
- **Markdown context: backtick escaping in issue text.** Agent-written Markdown in issue title, description, or recommendation (e.g., fenced code samples) was injected verbatim, corrupting the document structure when it contained triple backticks. Runs of 3+ backticks in free-form text are now neutralized with a zero-width space.
- **Markdown context: agent headers match dispatched names.** Subsection headers baked issue count and verdict into the `###` heading, so the reconciliator's dispatched-vs-reported agent name matching failed. Headers are now plain `### agent-name` with metadata on a separate line.
- **Markdown context: multiline text contained.** Issue description/recommendation fields and agent-level recommendation items with embedded newlines spilled into top-level Markdown. Continuation lines are now indented to stay inside list items.
- **Markdown context: change-purpose isolated.** The change-purpose block (a `.md` artifact from step 3/4) was injected verbatim — fenced code blocks or headings in it could corrupt or spoof subsequent sections. Now wrapped in a dynamically-sized code fence.
- **Markdown context: positive observations excluded.** Like observations, positive observations bypass the scope/snippet pipeline and can skew reconciliation decisions without evidence. Excluded from Markdown; retained in JSON for debugging.
- **Reconciliator prompt: `metadata_only` scope status documented.** `check_scope()` emits `OUT_OF_SCOPE:metadata_only` for rename/chmod-only diffs, but the prompt only listed 4 statuses. The reconciliator now correctly treats those entries as out-of-scope.
- **Markdown context: block syntax in agent text escaped.** Agent-written descriptions or recommendations containing ATX headings (`## …`), thematic breaks (`---`), setext underlines (`===`), or block quotes (`>`) could corrupt the document structure — CommonMark recognises these with up to 3 leading spaces, so indentation alone cannot prevent them. Triggering characters are now backslash-escaped.
- **Markdown context: out-of-scope findings pre-filtered.** Findings with structurally certain out-of-scope status (`file_not_in_diff`, `metadata_only`) are now excluded from the Markdown context before the reconciliator sees them, reducing wasted LLM tokens. Ambiguous `not_in_hunk` findings are kept — agent line numbers can be imprecise, and the source snippet may reveal the code is adjacent to changed lines. Scope annotations table is also trimmed to exclude pre-filtered entries. Reconciliator prompt updated to reflect pre-filtering and soften `not_in_hunk` handling.
- **Markdown context: missing agents pre-computed.** The script now computes dispatched-but-no-output agents and includes them as a `Missing agents` line in the metadata section. The reconciliator no longer needs to do set arithmetic across dispatched agents list and `### agent-name` headers.
- **Markdown context: noisy commit-derived change purpose removed.** When no explicit change-purpose artifact existed (e.g., `/full-code-review`), the script derived one from raw commit subjects. These were often noisy ("wip", "fix lint", "merge main") and added tokens without meaningful signal for severity calibration. The reconciliator already has the actual code changes via agent findings and source snippets.

## [1.96.1] - 2026-04-06

### Fixed
- **Decision critic: scope claims removed.** The critic's decomposition phase extracted scope claims (what's changed vs pre-existing) and its verification phase re-ran `git diff` to check them. Scope is already verified deterministically by `reconciliation_context.py` and the reconciliator — the critic running the same `git diff` a third time added tool calls without new signal.

## [1.95.0] - 2026-04-05

### Changed
- **Reconciliator performance: pre-gathered context.** New `reconciliation_context.py` script pre-gathers all agent findings, source snippets (±10 lines around each referenced location), scope annotations, and the ReviewOutputBuilder path into a single `reconciliation-context.json` before dispatching the reconciliator agent. The reconciliator now reads one file instead of 11+ agent JSONs and 15-25 source files individually, reducing LLM turns from ~50 to ~5-8 (est. 5-10x speedup). Pipeline step 8 calls the new script during orchestration and passes the context file path in the briefing.

### Fixed
- **Hunk-level scope: false in-scope gap on shifted hunks.** `_parse_diff_hunks()` was computing a union range across old-side and new-side coordinates. When insertions shift later hunks (e.g., `@@ -200,2 +300,2 @@`), this marked untouched lines between the old and new positions as IN_SCOPE. Now stores old-side and new-side ranges as separate entries, eliminating the false gap while preserving old-side coverage for deletion hunks.
- **Reconciliator: pre-change snippet lookup guidance.** Added instructions telling the reconciliator to check `[pre-change] <file>` entries in `source_snippets` when a finding's claim doesn't match the post-change snippet — prevents valid findings on deleted/rewritten code from being incorrectly marked as false positives.
- **Zero-dispatch reconciliation context.** When the dispatch plan selects 0 agents (e.g., docs-only change), pipeline now passes `--dispatched-agents ""` so the context builder loads nothing instead of falling back to stale `*-review.json` files from prior runs.

## [1.94.0] - 2026-04-02

### Added
- **Toolchain reviewer agent.** New `toolchain-reviewer` agent for developer toolchain config changes: package manager configs (pnpm, npm, yarn), build tools (webpack, vite, esbuild, turbo, nx), linter/formatter configs (ESLint, Prettier, PHPCS, PHPStan), TypeScript/Babel configs, CI/CD pipelines, version constraints (.nvmrc, engines), and supply chain security settings. Key differentiator: proactively searches changelogs via WebSearch to verify config settings against actual tool versions before reporting deprecations or invalid options. Runs on sonnet model tier, dispatched as `conditional` class with 31 triage keywords.
- **`toolchain` scope domain.** Matches 20+ config file patterns including pnpm-workspace.yaml, .npmrc, package.json, tsconfig, webpack/vite/rollup configs, ESLint/Prettier configs, composer.json, phpstan, CI workflows, Dockerfiles, .nvmrc, turbo.json, renovate/dependabot, and Makefiles.
- **`list_only` domain field for scope.** New optional field in `DOMAIN_CATALOG` entries that rescues files from noise filtering and includes them in the file list and diffstat, but skips their full diff (too large/noisy for inline review). The toolchain domain uses this for lock files (`pnpm-lock.yaml`, `package-lock.json`, `composer.lock`, `yarn.lock`, `go.sum`, etc.) — the agent sees they changed and their +/- stats, but isn't flooded with thousands of lockfile diff lines.

### Changed
- **Toolchain reviewer prompt optimized.** Applied 9 prompt engineering techniques: consolidated identity block (Identity Establishment + Emotional Stimuli), added pre-review toolchain scan (Pre-Work Context Analysis + Plan-and-Solve), absorbed RULE 2 into RULE 0 eliminating 3x duplication of "search the changelog" instruction (Emphasis Hierarchy), slimmed RULE 1 to WebSearch-only focus (Scope Limitation), added conditional routing to all 6 checklists with file pattern triggers (Conditional Sections + Category-Based Generalization), moved FALSE POSITIVE GATE before checklists as pre-filter (Document Positioning), merged Finding Confidence and Final Check into single verification template (Embedded Verification), reframed "Not in scope" as "Your domain" (Affirmative Directives). Net: ~25 lines removed through deduplication, deduplicated Toolchain Engineer's Questions from 5 to 3 unique items.
- **Performance reviewer: added FALSE POSITIVE GATE and conditional checklists.** Added 5-item false positive gate with domain-specific checks (framework auto-caching, micro-optimization without scale evidence, 10x/100x test verification). Added conditional routing to all 3 review checklists ("When database queries are added or modified" instead of "For Each Database Query").
- **FALSE POSITIVE GATE added to 4 domain reviewers.** Added domain-specific false positive gates to security-reviewer (5 items: performance scope, PII lifecycle scope, framework-handled sanitization, defense-in-depth severity, incomplete trace verification), a11y-reviewer (5 items: code clarity scope, architecture scope, visual preference filter, parent component verification, non-interactive context), architecture-reviewer (5 items: WP-specific scope, patterns scope, security scope, premature abstraction Rule of Three, framework convention check), wp-architecture-reviewer (5 items: general SOLID scope, API contract scope, security scope, pragmatic hooks principle, framework API pattern verification).

## [1.93.3] - 2026-03-28

### Fixed
- **Iterative review: deferred findings re-surfacing across rounds.** Three reinforcing changes prevent the reviewer from re-flagging items the engineer already deferred: (1) new "Previously Deferred Items" section in the reviewer prompt lists all deferred items with explicit "do not re-raise" instruction, (2) Review History instruction now addresses both rejected AND deferred items (previously only mentioned rejected), (3) pushback log severity gate bypassed for deferred items so they appear in the chronological history regardless of severity.

## [1.93.2] - 2026-03-28

### Fixed
- **Skill reference file paths across 12 skills.** Replaced ~80 bare relative paths (`references/foo.md`, `../testing-patterns/references/bar.md`, `$PLUGIN_ROOT/scripts/...`) with `${CLAUDE_SKILL_DIR}`-based paths so CC resolves them directly via string substitution instead of searching the filesystem. Affected skills: testing-patterns, php/js/go/python/rust/e2e-testing-patterns, wordpress-backend-dev, software-architecture, accessible-frontend-dev, using-figma, decision-critic, analyzing-cc-sessions.

## [1.93.1] - 2026-03-27

### Fixed
- **decision-reviewer model corrected to opus.** Agent `.md` frontmatter said `model: sonnet` but `agent_registry.json` (source of truth) said `model_tier: "opus"`. Fixed `.md` to match registry. Also corrected model tier documentation in README for 4 agents (pr-reviewer, a11y-reviewer, review-reconciliator, decision-reviewer) and rewrote Model Tiers section with accurate counts: opus(3)/sonnet(18)/haiku(6).
- **pirategoat-tools README missing `/iterative-review` command and `create-github-pr` skill.** Added both to their respective tables; updated command count from 6 to 7.

### Changed
- **Testing patterns skill rewritten.** Applied 9 prompt engineering techniques to SKILL.md: Identity Establishment (directive opening), Emphasis Hierarchy (RULE 0: test behavior not implementation), Pre-Work Context Analysis (read existing tests first), Affirmative Directives (verb headers), Category-Based Generalization (routing table expanded from 8 to 12 entries), Scope Limitation (removed duplicate reference library listing), Error Normalization (flaky tests = implementation bug hint), Hint-Based Guidance (routing entry hints), Contrastive Examples (WHY column in FORBIDDEN table). Added missing language-specific skill references (rust, python).
- **Testing reference corpus compressed ~32%.** All 8 core reference files compressed for agent consumption: Quick Reference tables added to every file for section-targeted routing, narrative/blog content extracted to principles, code examples trimmed to one per concept, duplicate content across files removed. test-quality.md merged into test-philosophy.md (60% overlap eliminated). README.md emoji headers removed per project standard. Individual reductions: test-layers 69%, test-smells 55%, test-structure 53%, test-benefits 53%, tdd-workflow 43%, README 43%, test-data 41%, coverage 32%, mocking-strategies 27%.
- **Python testing patterns skill optimized.** Applied prompt engineering patterns: directive framing (Identity Establishment + Affirmative Directives), severity-categorized red flags (Emphasis Hierarchy + Category-Based Generalization), promoted orphan mocking instruction to proper section with concrete boundary examples, extracted async auto-mode recommendation from code comment to directive prose.
- **Rust testing patterns skill optimized.** Applied same prompt engineering patterns: directive opening (Affirmative Directives), red flags subcategorized into three severity tiers — safety/correctness, async/concurrency, tooling/hygiene (Category-Based Generalization + Emphasis Hierarchy), promoted orphan mocking line to headed section with routing (Hint-Based Guidance).
- **Software architecture skill optimized.** Applied 7 prompt engineering techniques to SKILL.md: Identity Establishment (architect role identity), Pre-Work Context Analysis (4-step Diagnose→Route→Recommend→Implement workflow), Scope Limitation (prevent over-engineering beyond the presented problem), Category-Based Generalization (split routing table into "Patterns with Reference Files" vs "Quick Fixes"), Affirmative Directives (replaced hedging "feels like boilerplate" with testable "name the design pressure"), Emphasis Hierarchy (single RULE on the design-pressure gate), Hint-Based Guidance (checklist context: "use after recommending a pattern"). Fixed broken path to solid-principles.md, removed 2 references to nonexistent files (code-smells.md, refactoring-strategies.md), removed stale pointer to patterns/README.md which advertises patterns without reference files.
- **Removed 28 stale AI artifacts from `docs/`.** Cleaned out obsolete research proposals, implementation plans, progress reports, and session handoffs from January-February that belonged in `.claude/docs/` per repo conventions. Also removed two outdated guides (FALSE-POSITIVE-HANDLING-GUIDE.md, REAL-EXAMPLE-ANALYSIS.md) and their README references. Net: 18,479 lines removed.

## [1.93.0] - 2026-03-26

### Added
- **Python tests reviewer agent.** New `python-tests-reviewer` agent covering pytest fixtures and scoping, parametrize patterns, mock/patch target resolution, autospec, AsyncMock, pytest-asyncio modes, hypothesis property-based testing, freezegun/time-machine lifecycle, and factory_boy state isolation. Runs on haiku model tier, dispatched as `always` class with tests-reviewer protocol.
- **Python testing patterns skill.** New `python-testing-patterns` skill with reference routing and quick-reference tables for Python-specific assertion patterns, red flags, and mock/fixture anti-patterns.
- **Python testing patterns reference.** New `python-testing-patterns.md` reference file (~430 lines) in the testing patterns reference library.
- **`python-tests` scope domain.** Matches Python test files (`test_*.py`, `*_test.py`, `tests/**/*.py`, `conftest.py`).

### Fixed
- **Rust/Python test domains added to test-only triage.** `python-tests` and `rust-test-dirs` added to `_TEST_DOMAINS` so test-only PRs in Python or Rust correctly skip conditional production reviewers (security, performance, architecture).
- **Rust inline unit tests now visible to reviewer.** `rust-tests` scope expanded back to all `.rs` files so `#[cfg(test)] mod tests` blocks inside source files are included in the review. A dedicated `rust-test-dirs` catalog entry (`tests/` + `benches/`) handles the "is this a pure test file?" triage check without widening it to production source files.

## [1.92.0] - 2026-03-26

### Added
- **Rust tests reviewer agent.** New `rust-tests-reviewer` agent covering the built-in `#[test]` framework, `assert!`/`assert_eq!`/`debug_assert!` patterns, `#[should_panic]` correctness, Result-based tests, async test patterns (`#[tokio::test]`), `mockall` trait-based mocking, `proptest` property-based testing, `rstest`/`test-case` parameterized tests, `insta` snapshot testing, `criterion` benchmarks, and `serial_test` isolation. Runs on haiku model tier, dispatched as `always` class with tests-reviewer protocol.
- **Rust testing patterns skill.** New `rust-testing-patterns` skill with reference routing and quick-reference tables for Rust-specific assertion patterns, red flags, and test organization.
- **Rust testing patterns reference.** New `rust-testing-patterns.md` reference file (~350 lines) in the testing patterns reference library.
- **`rust-tests` scope domain.** Matches `.rs` files for inline unit tests and integration tests in `tests/` and `benches/` directories.

## [1.91.1] - 2026-03-25

### Changed
- **Iterative review: lower hard limit from 20 to 15 rounds.** 15 rounds is plenty for any review — reaching 20 was never productive.

## [1.91.0] - 2026-03-24

### Added
- **Claude Code CLI as fallback review backend.** When Codex is unavailable, iterative review now falls back to Claude Code CLI. New `backends/claude.py` module uses `--json-schema` for structured output, flag-based isolation (`--allowedTools`, `--disallowedTools`), and OAuth-compatible auth. New `claude-review-schema.json` provides a CC-adapted JSON Schema (no `additionalProperties` requirement). Backend selection runs at preflight: Codex (primary) → Claude Code (fallback).

### Changed
- **Normalized backend interface.** `check_auth`, `parse_output`, `invoke_review`, `TIMEOUT` are now the primary function/constant names in both backends. Old codex/claude-prefixed names (`check_codex_auth`, `invoke_codex_review`, `parse_codex_output`, `CODEX_TIMEOUT`, etc.) removed entirely — no backward-compat aliases. `invoke_review` in codex.py now contains the merged logic (output_file kwarg handling built in).
- **Backend-agnostic termination reasons.** Renamed `codex_unavailable` to `backend_unavailable`, `codex_timeout` to `backend_timeout`, `codex_timeout_at_cap` to `backend_timeout_at_cap`. Updated briefing display messages and linear pipeline outcome mapping to match.
- **Backend-agnostic briefing text.** Evaluation and degraded briefings now say "independent reviewer" instead of "Codex".

## [1.90.0] - 2026-03-24

### Added
- **Iterative review: `--autonomous` mode.** New flag for bot-driven pipelines where no human is present. Defaults to interactive (safe). Persisted in loop state so advance steps inherit the mode.
- **Iterative review: timeout handling.** Distinguishes timeout from generic unavailability via sentinel return value. Autonomous mode: timeout handler bypasses advance — records skipped rounds directly in state, auto-skips; two consecutive timeouts terminate the loop. Interactive mode: defers round recording until the user chooses retry/skip/stop (no state pollution). Round cap enforcement prevents skips beyond the configured budget.
- **Iterative review: stalemate escalation (round 3+).** Evaluation briefings now instruct the LLM to force-defer recurring rejection patterns rather than burning rounds on disagreements.
- **Iterative review: severity in outcomes.** Outcomes format now includes severity (P0-P3) copied from findings. `outcome_severity()` pure function in `loop.py` falls back to outcome severity when findings join fails, making convergence decisions resilient. Deferred items also use `outcome_severity()` so degraded-round severity is preserved.
- **Review rubric: finding scope guidance.** Instructs the reviewer to report all P0-P2 findings for thoroughness (reduces total rounds) and suppress P3 when higher-severity findings exist (reduces triage noise).
- **Iterative review: tiered round extension.** P0/P1 fixes at the round limit now extend by +2 rounds (signals something seriously wrong). P2 fixes extend by +1 (real issues in new code). P3-only does not extend. Previously all extensions were +1 and P2 didn't trigger extension.
- **Linear pipeline: `--autonomous` passthrough.** Step 12 reads `autonomous_iterative_review` from run-config.json and passes `--autonomous` to the iterative review CLI (same pattern as `adaptive_iterative_review`). Pipeline maps `codex_timeout` and `codex_timeout_at_cap` to degraded review outcomes with distinct degradation notes.

### Changed
- **Iterative review command.** Documents timeout expectations, fourth outcome type (timeout briefing), severity in outcomes format, and tiered round extension behavior. Uses `${CLAUDE_PLUGIN_ROOT}` for scripts path (was vague prose that could resolve to the source repo instead of the plugin cache).
- **Reviewer backend.** Extracts `CODEX_TIMEOUT = 1800` constant, replacing hardcoded values.
- **Timeout briefings.** Use tool-agnostic language ("independent reviewer" / "the reviewer") instead of naming Codex, per the convention from v1.87.0.

## [1.89.2] - 2026-03-23

### Changed
- **Bootstrap prompts & shared protocols: prompt-optimize.** Applied same 7-technique treatment from v1.89.1 to the prompts each reviewer agent receives via `bootstrap.py`, `reviewer-protocol.md`, and `tests-reviewer-protocol.md`. Tightened STOP CHECK and verification items into numbered quality gates, promoted 47% false-positive stat as directional stimulus, compressed PR INTENT/REVIEW FOCUS/BUDGET preambles, collapsed test severity categories and checklists. ~33% token reduction per agent prompt (~18K tokens saved across a full 20-agent pipeline). Net -68 lines across 3 source files.

## [1.89.1] - 2026-03-23

### Changed
- **Review pipeline: prompt-optimize all 12-step briefings.** Applied 7 research-backed prompt engineering techniques (Affirmative Directives, Scope Limitation, Concise CoT, Emphasis Hierarchy, Hint-Based Guidance, Conditional Sections, Format Strictness) across identity constants, phase transitions, and all step functions. Tightened `_PIPELINE_MISSION` and `_PHASE_TRANSITIONS` (~35% token reduction on text injected at 5 steps). Converted negative framing to affirmative directives, separated informational text from action directives, compressed Step 5 triage override (15→5 lines), Step 7 wait instructions (10→7 lines), Step 10 verdict paths to structured format, Step 8 reconciliation to numbered actions. Net -79 lines.
- **Linear pipeline: prompt-optimize all 15-step briefings.** Same technique set applied to identity constants, phase transitions, and step functions. Removed redundant `_PIPELINE_MISSION` re-injection at Step 8 clarity gate (~60 tokens). Compressed Step 1 mismatch path (7→2 lines), Step 3 repo verification checks to single-line items, deduplicated Step 9 plan spec (merged 3 overlapping actions into 1), compressed Step 11 complexity routing (10→4 lines). Net -45 lines.
- **Pipeline test resilience.** Updated keyword-brittle tests in both review and linear test suites to check semantic intent with broader keyword sets, preventing breakage from future prompt rephrasing.

## [1.89.0] - 2026-03-23

### Added
- **Iterative review: adaptive reasoning effort.** New `--adaptive-effort` flag dynamically adjusts Codex `model_reasoning_effort` per review round. Round 1 runs at `high`, rounds 2+ default to `medium`. Prior-round signals escalate: P0/P1 findings fixed bump `medium` → `high`, P0/P1 rejected bump `high` → `xhigh`. When effort is `high` or `xhigh`, `service_tier="fast"` is also injected to keep throughput manageable. The flag is persisted in `review-loop-state.json` so rounds 2+ pick it up automatically. New `effort.py` pure-logic module with `resolve_effort()`, `effort` parameter on `invoke_codex_review()`, per-round effort tracking in state, and `effort_profile` in `review-loop-result.json` and telemetry.
- **Codex CLI reference: reasoning effort and service tier docs.** Documented all five `model_reasoning_effort` levels (`minimal`, `low`, `medium`, `high`, `xhigh`) and both `service_tier` values (`fast`, `flex`) with invocation examples.

### Changed
- **Iterative review: prompt ordering for cache efficiency.** Reordered `write_prompt_file` to place static content (rubric, context, task description) before dynamic content (pushback log, analysis paths). OpenAI's automatic server-side prefix caching can now cache the stable prefix across review rounds, reducing input token costs on rounds 2+.
- **Linear pipeline step 12: adaptive effort passthrough.** When `run-config.json` has `adaptive_iterative_review: true`, step 12 appends `--adaptive-effort` to all iterative review CLI invocations (both round 1 and round N).

## [1.88.0] - 2026-03-23

### Changed
- **Tests reorganization.** Moved 32 test files from flat `tests/` directory into subdirectories mirroring the scripts package structure: `review/` (10 files), `review/agent/` (6 files), `linear/` (3 files), `analysis/` (2 files), `iterative_review/` (5 files), `commands/` (1 file), `grading/` (2 files). Shared utilities moved to `helpers/` (3 files). Test file names now match their source module names.

### Removed
- **E2E test suite.** Removed `tests/e2e/` directory (12 files) — model-call-dependent tests that were never run.

## [1.87.0] - 2026-03-22

### Changed
- **Scripts reorganization.** Moved 17 scripts + 1 JSON config from flat `scripts/` directory into 5 domain-based Python packages: `review/` (pipeline orchestration), `review/agent/` (bootstrap, scope, output, diff noise filter), `linear/` (linear issue pipeline + events), `figma/` (design spec extraction), `analysis/` (session analysis). All files renamed to underscore convention for valid Python module names. `semantic-filter.py` renamed to `diff_noise_filter.py` to accurately describe its regex-based noise stripping. `agent-registry.json` renamed to `agent_registry.json` for naming consistency.

### Fixed
- **Bootstrap plugin root cache validation.** The cached root file (`/tmp/.pirategoat-tools-root`) now validates that the cached path contains the expected file layout (`scripts/review/agent/scope.py`), not just that the directory exists. Prevents stale cache entries from pointing to old plugin installations with different directory structures.

## [1.86.0] - 2026-03-22

### Added
- **Iterative review: cognitive traps priming (round 1).** The evaluation
  briefing now includes a compact anti-patterns block at round 1 covering
  rubber-stamping, positional entrenchment, and scope inflation. Shown once
  to prime evaluation posture; not repeated at rounds 2+.
- **Iterative review: self-correction prompt (round 2+).** The stalemate-
  breaking section now requires the agent to state specifically what was
  wrong in its prior reasoning when reversing a rejection. The correction
  flows into the pushback log, giving Codex better signal in subsequent
  rounds. Adapted from the receiving-code-review skill's "Gracefully
  Correcting Your Pushback" pattern.

## [1.85.2] - 2026-03-22

### Fixed
- **Iterative review: degraded-mode briefing/outcome contract mismatch.**
  The degraded briefing implied per-issue outcomes ("for each issue, apply
  evaluation steps as a normal round") but only one finding ID (`rN_raw`)
  exists in the degraded path. Reworded to frame the raw output as a single
  finding, with mixed resolutions noted in the summary field.
- **Iterative review: degraded mixed-round action priority.** Added a
  priority rule (fixed > deferred > rejected) for the single degraded-mode
  outcome. Fixed takes precedence to prevent premature convergence (the
  `fixed == 0` check would otherwise terminate the loop after real code
  changes). Deferred items are called out explicitly so the completion step
  can surface them as PR follow-ups.
- **Iterative review: all_rejected message misreports deferred-only rounds.**
  The `all_rejected` termination (triggered by `fixed == 0`) displayed "All
  findings were rejected" even when some were deferred. Updated to "No code
  changes needed — findings were rejected or deferred."

### Changed
- **Iterative review: structured verification steps.** Replaced the triage
  decision tree (if real → fix, if wrong → reject) with a 4-step cognitive
  forcing function (READ → VERIFY → EVALUATE → DECIDE). Separates code
  reading from claim assessment, preventing confirmation bias when evaluating
  external review findings. Grounded in factored verification, quote extraction
  for grounding, and plan-and-solve prompting techniques.
- **Iterative review: prompt engineering optimizations.** Applied 5
  research-backed techniques to the command prompt: identity establishment
  with stakes framing, RULE 0 emphasis hierarchy with STOP escalation for
  skepticism, completeness checkpoints for the fix sequence (siblings →
  right-size → commit), history accumulation for multi-round context, and
  error normalization for the UNAVAILABLE path.

## [1.85.1] - 2026-03-22

### Changed
- Redesigned bootstrap integration tests: 257 mechanically-parameterized tests → 48 targeted tests covering every conditional path (43s → ~6s)
- Domain routing tests use direct function calls instead of subprocess (23s → ~3s)
- Fixed mock gap in review_scope tests where freshen_base_ref did real git fetch calls (~7s → <1s)
- Full test suite: 90s → ~30s with zero coverage loss

### Added
- Category representative tests for secondary_domains (security-reviewer) and file_history without budget_override (api-contract-reviewer)
- New `php-with-ci-config.diff` test fixture for exercising the secondary scope append branch
- DOMAIN RULES invariant now covers all 4 test agents (php, js, e2e, go), not just 2
- Registry validation for no_semantic_filter agents moved to test_agent_registry.py

## [1.85.0] - 2026-03-22

### Added
- **Iterative review: scoped sweep-for-siblings instruction.** Evaluation
  briefing and command now instruct the session to check for sibling
  instances of a finding within the branch's scope before fixing. Siblings
  in files the branch already touches get fixed proactively (avoiding extra
  rounds); siblings outside scope get logged as follow-ups for the PR
  description rather than expanding the branch's mandate.

## [1.84.0] - 2026-03-22

### Added
- **Clarity gate (step 8):** LLM-based assessment of issue clarity before
  implementation. Evaluates 3 hard gates (problem statement,
  reproduction/scope, success criteria) and 3 soft signals (conflicting
  signals, missing technical context, implicit assumptions). Produces
  `clarity-assessment.json` with structured findings
- New `needs_clarification` verdict and `blocked` pipeline status for issues
  lacking implementation clarity
- `clarity_gate` and `clarity_gate_overridden` fields in
  `pipeline-result.json` for bot routing and false-positive tracking
- Override support: bot can resume pipeline at step 9 with
  `skip_clarity_gate` config flag
- Step 9 (Write Plan) briefing incorporates flagged ambiguities as
  documented risks when clarity gate was overridden

### Changed
- Linear issue pipeline step count: 14 → 15 (existing steps 8-14
  renumbered to 9-15)
- `_eval_condition("fix_mode_and_unresolved")` now checks
  `clarity_blocked` state flag

## [1.83.0] - 2026-03-22

### Added
- **Step 8 time-based escalation gate** — when waiting on running agents
  exceeds `agent_timeout_seconds + 60s`, step 8 escalates instead of
  looping: clears the waiting state, instructs the LLM to TaskStop stuck
  agents, and proceeds with reconciliation using available results. This
  prevents the pipeline from spinning on retries until the blunt
  pipeline-level timeout kills everything (losing completed agent work)

### Changed
- Renamed `agents_blocked` state key to `waiting_on_agents` for clarity —
  the pipeline is waiting on agents to finish, not blocked by them
- Step 8 readiness gate title changed from `BLOCKED` to `WAITING`

## [1.82.0] - 2026-03-22

### Added
- **`not_applicable` verdict** — reviewer agents that determine changes are not
  relevant to their domain produce `verdict: "not_applicable"` with a `skip_reason`
  instead of a misleading `approve`. This prevents the reconciliator and orchestrator
  from over-indexing on agent counts that include abstentions
- **`mark_not_applicable(reason)` method** on `ReviewOutputBuilder` — the API agents
  call during the Quick Relevance Check or NO_DOMAIN_FILES path to signal abstention
- **Reconciliator not-applicable awareness** — separates abstaining agents from
  reviewing agents in both narrative output and `meta.reconciliation` metadata
  (`not_applicable_count`, `not_applicable_agents`, `reviewing_agents`)
- **Iterative review pre-flight check** — verifies the review CLI is installed and
  authenticated before spending time on prompt composition and diff computation;
  exits with `UNAVAILABLE` message and structured result on failure
- **`independent_code_review` outcome field** in Linear pipeline result — replaces
  boolean `codex_review_applied` with a string enum (`not_run`, `unavailable`,
  `clean`, `converged`, `max_rounds`, `hard_limit`) that surfaces what happened
- **Linear pipeline step 11 unavailable handling** — when the review tool is
  unavailable, notes degradation and skips to PR creation

### Changed
- Reviewer protocol's Quick Relevance Check uses `mark_not_applicable()` instead of
  informal positive-observation-only pattern
- NO_DOMAIN_FILES path uses `mark_not_applicable()` instead of plain approve
- Tests-reviewer protocol's "No test files" path uses `mark_not_applicable()`
- `Verdict` type extended with `'not_applicable'`; `skip_reason` field added to
  `ReviewOutput` schema
- Linear pipeline step 11 briefing is now tool-agnostic (no CLI name in prose)
- `mark_not_applicable()` raises if issues already recorded (prevents contradictory
  output where verdict is `not_applicable` but findings exist)

## [1.81.0] - 2026-03-22

### Added
- **`/iterative-review` command** — standalone multi-round Codex review on any
  feature branch. Orchestrates the iterative review loop directly in the main
  session: run Codex, triage findings, fix, commit, advance, repeat until
  convergence. Supports quick mode (`--max-rounds 1`) and user-specified round
  limits via free-form arguments
- **`--max-rounds` CLI argument** for `iterative_review` module — overrides
  diff-size-based computation, capped at hard limit of 20
- **Reviewer-requested focus** — `additional_instructions` from `run-config.json`
  surfaces prominently in Step 5 (dispatch review), agent bootstrap briefings,
  and Step 8 (reconciliation) to steer review attention based on the requester's
  guidance

## [1.80.0] - 2026-03-22

### Changed
- Step 11 (Self-Review) replaced with iterative Codex review loop — multi-round
  independent review with pushback tracking, convergence detection, and
  root-cause-aware fix discipline
- Step 12 (Re-Verify) now handled by review loop — each round includes verification
- Step 13 (Create Draft PR) reads deferred items from pruned review result and
  includes them as Follow-ups in the PR description
- `codex_review_applied` in pipeline result now derived from artifact existence
- VALIDATION phase transition updated for iterative review terminology
- Steps 8, 10 updated from "codex reviewer" to "iterative review loop"

### Added
- `scripts/iterative_review/` sub-module with 7 files:
  - `loop.py` — state management, convergence detection, max rounds (diff-size-based),
    pushback log (severity-gated P0/P1), deferred items JSONL, outcome validation
  - `telemetry.py` — JSONL progress log and pipeline events
  - `backends/codex.py` — output parsing, prompt composition with review rubric,
    repo-relative path normalization, CLI invocation from repo root
  - `backends/codex-review-rubric.md` — 8 bug criteria, P0-P3 severity, conservative threshold
  - `backends/codex-review-schema.json` — OpenAI Structured Outputs schema for findings
  - `briefing.py` — evaluation briefings with root-cause-aware fix discipline,
    verify-first mindset, completion summaries, degraded mode
  - `__main__.py` — CLI entry point (`--action review|advance`)
- Dynamic max rounds: starts at 3-10 based on diff size, extends +1 when fixed
  P0/P1 findings appear at the limit, hard-capped at 20
- Idempotent advance — retrying the same round doesn't duplicate records
- Uncommitted changes detection — blocks review with actionable instructions
- Stale artifact cleanup on round 1 rerun (findings, outcomes, prompts, analysis docs)
- Round 2+ state validation — rejects review when no round 1 state exists
- Cross-round deferred item pruning by (title, location) in review-loop-result.json
- Dynamic default branch detection (origin/HEAD → main → trunk → develop)
- Deferred items surfaced in PR description with LLM-level dedup instruction
- Noise-filtered diff sizing (imports from review-scope.py)
- Context size tracking with auto-truncation

## [1.79.0] - 2026-03-21

### Added

- **Quick review mode for pr-review** (`--quick` flag or user intent detection)
  - Excludes 5 lower-signal agents (wp-architecture, history-insights, data-flow-privacy, concurrency, reliability) unless triage keywords match — keyword-confirmed agents dispatch normally even in quick mode
  - Nudges orchestrator toward aggressive skips at dispatch (step 5)
  - Conditionally skips decision critic when reconciliation verdict is approve/comment (step 10); maps to `unavailable` for pirategoat-bot contract
  - Full telemetry capture: `quick_mode` flag in pipeline start/summary (cross-process safe), `SKIPPED_QUICK_MODE` dispatch status, critic skip decisions via `log_step` decisions dict
  - Correctly resets on rerun: `--quick` flag syncs both ways (quick→normal and normal→quick)

## [1.78.0] - 2026-03-21

### Added

- **`linear-issue-pipeline.py`** — 14-step state machine for investigating Linear issues and optionally implementing fixes with draft PRs. Two modes: `investigate` (steps 1-7 + 14: fetch issue, check existing work, gather context, investigate with type-specific paths, write report, post to Linear) and `fix` (all 14 steps: adds write plan, implement, verify, self-review via codex-reviewer, re-verify, create draft PR). Follows the same curated-context-pipeline pattern as `review-pipeline.py` with `STEP_SEQUENCE`, condition-based routing, state management, and curated briefings. Bot-mode only (`interactive: false`).
- **`pipeline_events.py`** — `PipelineEventEmitter` class for pipeline-to-bot communication via `pipeline-events.jsonl`. Emits milestone events (status message edits), deliverable events (separate Slack messages), and lifecycle events (`step_started`, `step_completed`, `pipeline_complete`, `pipeline_failed`). All writes are best-effort — never raises.
- **Two-layer repo verification** — Step 1 runs a deterministic `git remote get-url origin` check against `issue-context.json`'s `repo_slug` (catches clone pool errors, interactive wrong-directory). Step 3 adds semantic verification where the LLM cross-references linked PRs, file paths, and component names against the codebase (catches team prefix → wrong repo mapping, e.g., TRAPLAT issue about transact-platform-server routed to wpcom). Both layers write `pipeline-result.json` with `status: "failed"` and a clear degradation note on mismatch.
- **Complexity-conditional codex review** — Step 8 (Write Plan) instructs the LLM to assess implementation complexity and write `complexity.json`. Step 10 (Verify) routes based on the assessment: small changes (≤3 files, single concern) skip the codex reviewer and rely on the per-task `superpowers:code-reviewer` from subagent-driven-development; medium/large changes continue to step 11 for independent codex review.
- **`fix_mode_and_unresolved` condition** — Steps 8-13 use this condition so fix-mode runs skip planning/implementation when step 3 finds a merged PR that already resolves the issue.
- **Pipeline result status derived from real outputs** — `pipeline-result.json` status is `failed` (not `success`) when no verdict or report was produced, `degraded` when fix mode completes without a PR URL, and `success` only when actual artifacts exist. Prevents partial runs from appearing successful to bot consumers.
- **Mode-specific step 7 guidance** — The "jumps to step 14" note only appears in investigate mode, preventing fix-mode LLMs from stopping after posting the investigation report.

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
