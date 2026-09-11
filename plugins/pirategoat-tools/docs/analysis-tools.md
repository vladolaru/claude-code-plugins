# Analysis and observability tools

Read this when you need to understand reviewer-agent behavior from raw session logs, measure review runs, or change anything under `scripts/analysis/`. Each script's `--help` and module docstring is the authoritative interface description; this document is the map and the measurement contract.

Paths below are relative to `plugins/pirategoat-tools/`; prefix them when your shell is at the repository root.

| Script | Answers | Test after changing it |
|---|---|---|
| `scripts/analysis/review_run_metrics.py` | Per-run and cohort review metrics from durable manifests, with optional transcript enrichment | `tests/analysis/test_review_run_metrics.py` |
| `scripts/analysis/review_transcript.py` | Privacy-preserving transcript enrichment used by the metrics | `tests/analysis/test_review_transcript.py` |
| `scripts/analysis/usage_snapshot.py` | One run's token usage, captured into its run directory | `tests/analysis/test_usage_snapshot.py`, `tests/review/test_orchestration_hygiene.py`, `tests/review/test_telemetry.py` |
| `scripts/analysis/session_analyzer.py` | Tool-call sequences and file-read patterns of subagent dispatches (retains prose; local use only) | `tests/analysis/test_session_analyzer.py` |
| `scripts/analysis/session_metrics.py` | Runtime, model, cache tokens and verdict per session transcript | `tests/analysis/test_session_metrics.py` |
| `scripts/analysis/codex_rollout.py` | Shared reader for Codex rollout files; the only module that knows that schema | `tests/analysis/test_codex_rollout.py` |
| `scripts/analysis/codex_session_analyzer.py`, `codex_session_metrics.py` | The Codex equivalents of the two session tools; metric names match so figures share a table | `tests/analysis/test_codex_session_scripts.py` |

## `review_run_metrics.py`

The supported review-pipeline run and cohort interface. It prefers durable `*.manifest.json` telemetry sidecars, falls back to privacy-reduced legacy JSONL records, and can enrich an exact run from Claude transcripts without weakening the pipeline-native measurements when transcripts are unavailable. The CLI is a thin entry point; the implementation is the `scripts/analysis/review_metrics/` package, whose imports flow one way only:

```text
contracts -> sanitize -> usage -> load -> {measure, cohort} -> render -> cli
```

`contracts.py` loads external contracts (telemetry, dispatch_status) and shared constants; `sanitize.py` holds field-level sanitizers and strict validators; `usage.py` token-usage accumulation; `load.py` manifest and JSONL discovery, the lifecycle overlay, `load_runs`, and `load_shared_runs` (the `v1/<login>/` shared-clone reader, which reuses the local grouping, conflict-resolution and limit rules and follows no symlink); `measure.py` per-run measurement and transcript enrichment; `cohort.py` cross-run aggregation; `render.py` table and JSON rendering; `cli.py` argument parsing.

```bash
python3 scripts/analysis/review_run_metrics.py --last 30
python3 scripts/analysis/review_run_metrics.py --last 30 --format json --output "$TMPDIR/review-runs.json"
python3 scripts/analysis/review_run_metrics.py --run-id <run-id> --no-transcripts
python3 scripts/analysis/review_run_metrics.py --shared-dir <clone-of-the-telemetry-repo>
```

`--log-dir` and `--shared-dir` are mutually exclusive: the first reads this machine's runs, the second a clone of the shared telemetry repository as a per-engineer cohort, attributing each run to the uploader login its `v1/<login>/` path carries and counting a run uploaded under two logins once. Transcript enrichment is always off for a shared source, since uploads null the session id.

**Transcript enrichment is bounded to explicit queries.** It costs one session discovery plus a full transcript parse per run, so a query without `--last` or `--run-id` reports the transcript family as `disabled` and prints how to enable it. The cohort itself is never truncated.

**The JSON report is local operational output, not a share-safe export.** It retains `repo_path`, `output_dir`, `session_id`, Git identifiers, and free-form orchestrator adjustment reasons because they are measurement evidence. Redact before sharing outside the local trusted context.

### Measurement contract

- Telemetry and manifest fields are authoritative for run identity, planner versus orchestrator adjustments, the generated-scope assignment, lifecycle, outcomes, critic verdict, and wall time.
- The `synthesis_agents` family (reconciliator and decision critic) is deliberately separate from `lifecycle` (reviewer `agent_start`/`agent_complete` events). Neither synthesis agent produces those events or appears in a dispatch plan, so folding them in would corrupt every reviewer count. Its durations run from the step marker to the completion artifact's mtime. The marker is written at the end of the step's script work, before the orchestrator registers its notes and issues the `Agent` call, so `duration_ms` includes that gap; `dispatch_lag_ms` measures the gap separately from the orchestrator transcript's dispatch timestamp, and is `None` whenever that transcript is unavailable or uncorrelated. A run predating the family reports `missing`, never a zero-duration phase. Quick mode commits the pipeline's own `SKIPPED` verdict without a dispatch marker and creates no row; a dispatched critic whose usable verdict never appears records `stalled` with no duration and degrades the run. Historical `SKIPPED` rows are counted separately as `skipped_runs` and excluded from `total_ms`/`mean_ms`.
- **Two distinct `usage` keys**, separate the same way: `measured["usage"]` (`availability.usage`) is the durable per-run snapshot projected from `usage-snapshot.json` through `manifest_sections.build_usage_manifest`, while `measured["transcript"]["usage"]` (`metric_availability.usage`) is the live transcript-derived family `measure_run()` computes fresh on every call. `usage_snapshot.py` bridges them: its capture calls `measure_run()`, then reads `measured_run["transcript"]["usage"]` to write the durable snapshot that later becomes `measured["usage"]`. At capture time the return value carries both keys side by side, the former near-always `None`, so an edit that read the former would silently capture nothing while looking plausible.
- There are no human overrides in this flow. Deterministic planning runs first; the orchestrator may then add or skip agents and supplies the reasons.
- Lifecycle `agents.incomplete` is a deterministic sorted multiset with one repeated name per unmatched start execution; `incomplete_count` measures executions, `incomplete_identities` unique names, `incomplete_by_agent` per-agent multiplicity. Complete manifests require the exact start-minus-completion multiset and suppress sibling overlays. Running manifests remain partial; the consumer may overlay only a strictly validated same-run JSONL lifecycle suffix after proving the sidecar arrays are exact causal prefixes, and must reduce fresh events without retaining raw prose or scope paths. Malformed, foreign, prefix-inconsistent, or chronologically invalid siblings fail closed for lifecycle only.
- Dispatch `adjustment_rate` measures changed agents over the full compared-agent union; `planner_removal_rate` measures removed agents over planner-dispatched candidates for comparable runs. Wall durations above one year are treated as implausible missing data.
- Valid plans with different agent identity sets disable adjustment comparison and carry only sorted identity-to-status projections. Ingestion rederives both dispatch counts from those projections, requires exact mismatch metadata, and fails malformed projections closed for the dispatch family without retaining plan prose.
- `tool_failures` lists every failed call as evidence, but an expected non-zero exit of the pipeline's own instruments is listed under its own category and left out of the cohort's `total`, `recovered`, and `partial_observed_total`. Today that is `poll_outcome`: `agents_status.py` exits 2 while agents are still running and 3 when a `--wait` window expires, and the harness flags both as errors. A search that found nothing is not among them, because the harness already records a `grep` or `rg` miss as a success.
- Transcript correlation is optional and exact: session id plus output directory plus a recognized reviewer, reconciliator, or critic identity.
- `lifecycle.inline_diff_lines` is the hunk-line count each reviewer's briefing actually carried, summed over dispatched reviewers and distinct from the diffstat total `scope.lines` that sizes the tool-call budget; it is `None` unless every dispatched reviewer carries the count, and the `inline_diff_empty` warning flags a run whose briefings carried no diff line while the diffstat said they should have.
- Every metric family distinguishes complete, partial, missing, and disabled data. Missing data is never a measured zero, and partial observations never enter complete denominators.
- **Legacy reconstruction is frozen.** Review-run legacy segments and identityless-segment recovery are best-effort; their availability families remain the reporting boundary. `session_analyzer.py` reads the finalized `reviewers/<reviewer>/review.json` the builder envelope names, and an artifact it cannot read is unmeasured rather than approximated. A new inference-precision edge is a known limitation, not another hardening round; crashes, privacy or safety failures, or contamination through foreign run, agent, or artifact identity confusion remain bugs, and new precision belongs in producers (manifests, sidecars, shared contracts).
- Stable reports use schema 5. Transcript-derived observed reads remain their exact schema-2 payload; legacy, missing, boolean, or future versions fail closed.
- Generated scope is descriptive, not proof of model reads, and observed reads are always non-exhaustive. Only scope-bearing regular reviewer reads enter the `all`/`in_scope`/`out_of_scope` partition; exact `review-reconciliator`, `decision-reviewer`, and `critic` identities, plus scope-exempt domainless reviewers (`tests-mutation-reviewer`), route to the separate `non_scope_comparable` bucket. Damaged scope-exempt evidence degrades that family, never the scope-comparable one. Near-match names are regular reviewers.
- Reviewer and synthesis read families carry independent completeness, availability, and cohort denominators; the combined `observed_reads` state is complete only when both are.
- Every observed-read entry is one canonical repository-relative path. Absolute, traversal, dot-segment, empty-segment, backslash-separated, drive-prefixed, empty, and control-character paths invalidate the whole read payload; normalized Unicode and spaces are preserved.
- Transcript privacy reduction excludes raw prompt bodies, source and finding prose, commands, and tool-result bodies. It does not make the report path-free or identifier-free.

## `review_transcript.py`

Lower-level privacy-preserving transcript enrichment used by the metrics. It correlates the exact main session and run-specific subagents, measures cache-aware usage, safe tool failure and recovery categories, first pipeline-owned Bash attempts, and emits a versioned observed-read payload with independent regular-reviewer and synthesis-identity completeness. `usage_summary_for_transcript()` is the public token-count wrapper: all callers use the one parser so a streamed response is counted once. Reviewer output evidence paired with `builder_attempted: false` means only that the required Bash path was not observed. It keeps completion-notification usage out of totals and never exposes raw prompts, commands, source, findings, or tool-result bodies.

### Settled design questions

Both were raised in review and decided; reopen them only with new evidence.

**Why reconstruct identity from transcripts instead of using `SubagentStop` / `PostToolUse` hooks?** The hooks emit `agent_id`, `agent_type`, `resolvedModel`, and `totalToolUseCount` directly, which would replace the correlation layer (session discovery, run-window bounding, dispatch-prompt parsing, and its four warning codes) but not transcript parsing itself, since `observed_reads` still requires reading each subagent transcript. The deciding tradeoff: hooks only measure runs after install, while the parser reads history, including the cohort the budget-utilisation baseline is built on. Correlation failure is reported explicitly rather than silently dropping agents from denominators.

**Why does this module parse session JSONL when `session_analyzer.py` already does?** Their contracts differ: `session_analyzer.py` retains prose for human-facing ad-hoc reports, while this module must never expose those bodies. JSONL is read by `review_metrics/load.py::_read_jsonl` (plus its strict variant), `review_transcript.py::_read_jsonl` and `_bounded_jsonl_entries`, `session_analyzer.py`, three sites in `session_metrics.py`, and `telemetry.py::_read_events`. Keep them separate because their contracts differ across binary/text input, strict/tolerant failure, and report/skip behavior, while the genuinely shared surface is about 15 lines. Reopen only if a malformed-line-handling fix has to be re-discovered per copy.

## `usage_snapshot.py`

Captures one run's token usage into its run directory as `usage-snapshot.json`. Step 11 invokes it as a subprocess; it is also runnable by hand over a finished run:

```bash
python3 scripts/analysis/usage_snapshot.py --output-dir <run dir> [--sessions-root ~/.claude/projects]
```

It is a thin projection over `review_metrics.measure_run`, never a second correlation implementation. It resolves the run manifest through `ReviewTelemetry.manifest_path` and falls back to `run-config.json` for a `session_id` the manifest lacks.

**Why a subprocess seam, not an import.** `scripts/analysis/` already loads `scripts/review/`'s telemetry, dispatch-status, and critic contracts by exact path. Importing the analysis package back into `orchestration.py` would close that loop; the CLI keeps the dependency one-way.

**The two halves are labelled independently.** At finalize every subagent transcript is closed, so subagent usage is complete evidence; the orchestrator is measuring its own still-open session, so its number is partial by construction. The subagent label is derived from subagent-scoped facts (correlated-vs-expected executions plus agent-scoped warning codes), and the orchestrator label is gated on `window.closed`, so a capture-time snapshot can never read `complete`.

**Window substitution.** A running manifest has no `ended_at`, and an unbounded window closes at the first human turn after it opens. The capture substitutes its own instant as the window end in its private view only; the manifest on disk is untouched, and `window.closed` records which kind of window the numbers cover. Re-running over a settled manifest is what upgrades a partial orchestrator half:

- **Monotonic, scoped to the artifact.** A re-run's candidate is compared half by half against the `usage-snapshot.json` already on disk; a candidate that would downgrade either half (most often rotated-out transcripts) is discarded wholesale and the existing file left byte-for-byte untouched. The guarantee protects the file, not the run: deleting the file is an explicit act, and the next capture re-measures from scratch. The CLI's one-line stdout summary carries `written`/`downgrade_avoided`.
- **The manifest follows, through `ReviewTelemetry.reproject_usage()`.** Telemetry projects the snapshot into the manifest's `usage` section once at finalize; a manual re-run calls this method so the manifest keeps one owning module with two entry points, the same shape `critic_adjustments.write_findings` gives the ledger. It patches only the manifest's `usage` key and its `availability.usage` flag through the same `atomic_write_json` primitive, never rebuilding the other sections. Two gates fail closed with no write: `status == "complete"` (a running manifest is `finalize()`'s alone, so the in-pipeline step-11 call is a no-op and `finalize()`'s full rebuild moments later settles `usage`) and `schema == EVENT_SCHEMA`. The outcome surfaces on stdout as `manifest_reprojection`, a reason string (`written`/`absent`/`not_settled`/`unsupported_schema`/`io_failure`) rather than a bool, and no non-written reason becomes a nonzero exit; the snapshot's own write path does fail loudly, because that write is the CLI's reason for existing.

**Availability doctrine.** An unreadable, absent, or transcript-less run (Codex writes no Claude-format transcripts) still writes the artifact with `missing` and null payloads: a recorded absence, distinct from a run that never attempted the capture. The Codex-host gap is permanent: no re-run can measure a host that never wrote a Claude-format transcript. Per-model buckets key on the dispatched model (`claude-opus-5[1m]`), not the per-message model inside the transcript, because the bracketed variant is separately priced.

The snapshot reaches two durable surfaces, the manifest's `usage` section and a compact `usage` block in `pipeline-result.json` (a pirategoat-bot consumer surface), both projected through `manifest_sections.build_usage_manifest()`. Step 11 may capture twice because its settlement pass is re-entered, but only the post-report pass publishes the compact block.

## `session_analyzer.py`

Parses subagent JSONL logs to extract tool-call sequences, categorize behavior, and generate efficiency metrics. Per dispatch it reports the categorized tool sequence (git-grep, git-show, git-log, git-diff, bootstrap, file-read-bash, file-list, other), the dispatch classification (reviewer, reconciliator, crashed), file-read patterns, output-file details (the Write tool or the canonical Bash builder heredoc, with the review read from the `reviewers/<reviewer>/review.json` its envelope names), and aggregate statistics. Output is `text` (default) or `json`.

```bash
# --sessions-dir is the project's absolute path with "/" replaced by "-"
python3 scripts/analysis/session_analyzer.py --sessions-dir ~/.claude/projects/<encoded-project-path> --agent patterns-reviewer --limit 20
python3 scripts/analysis/session_analyzer.py --sessions-dir ~/.claude/projects/<encoded-project-path> --agent security-reviewer --format json
python3 scripts/analysis/session_analyzer.py --sessions-dir ~/.claude/projects/<encoded-project-path> --limit 5 --output "$TMPDIR/analysis.txt"
```

## `session_metrics.py`

Extracts runtime, model, cache tokens, and verdict from session transcripts. Agent type is read from the host's `agent-<id>.meta.json` before transcript inference, and token counts use `review_transcript.usage_summary_for_transcript()` rather than a second per-record parser.

## Codex session tools

`codex_rollout.py` holds the shared primitives for Codex rollout files: thread metadata parsing, date-windowed discovery, single-pass thread scan, and tree building. It is deliberately separate from the Claude Code readers (different schema, different discovery model, small shared surface). `codex_session_analyzer.py` traces one thread tree in depth; `codex_session_metrics.py` emits one row per thread plus a roll-up by agent role.

```bash
python3 scripts/analysis/codex_session_analyzer.py --cwd /path/to/project            # newest thread tree for one project
python3 scripts/analysis/codex_session_analyzer.py --thread-id <thread-id> --format json
python3 scripts/analysis/codex_session_metrics.py --agent code-reviewer --since 30 --format markdown
```

## Session data locations

Claude Code stores transcripts separately from the durable review run directories:

```text
~/.claude/projects/<encoded-project-path>/   # absolute path with "/" replaced by "-"
├── {session-uuid}.jsonl           # main session transcript
├── {session-uuid}/
│   ├── subagents/
│   │   └── agent-{id}.jsonl       # one per dispatched agent; line 1 is the dispatch prompt
│   └── tool-results/
│       └── {hash}.txt             # cached tool results
```

Codex keeps one thread per file with no per-project partitioning; `cwd` is a field on line 1, and subagents are sibling rollouts linked by `agent_path`. Only finished sessions are analyzed, since a live rollout grows while being read.

```text
~/.codex/sessions/YYYY/MM/DD/
└── rollout-{ISO-timestamp}-{thread-id}.jsonl
```
