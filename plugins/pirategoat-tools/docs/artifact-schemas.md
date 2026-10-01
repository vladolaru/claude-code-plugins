# Artifact schemas

Read this before changing the shape of any JSON artifact the pipeline writes, or before adding a new one. The rule itself is in `AGENTS.md`; this document explains when the rule applies, records the two carve-outs, and lists the artifacts that carry a schema today.

## The rule

An artifact that carries a `schema` field gets that field bumped in the same commit as any change to its shape. A shape change is a key added, removed, or re-typed. Bump the producing constant, update `schemas/review-output.ts` if the artifact is declared there, and note the bump in the changelog. The key is always the integer `schema`, never `schema_version` and never a `version` string.

The rule exists because a schema number that lags the shape is worse than none: it states a compatibility guarantee the producer is not honoring.

## The carve-out: same unreleased window

A shape change made within the same UNRELEASED version that introduced the current schema number updates the contract in the same commit but does not bump, unless a reader must be able to tell the old shape from the new one. The number states a compatibility guarantee only once released, so bumping before release publishes a shape no artifact ever had; but a reader that would silently accept the old shape as the new one (missing required keys, a field whose meaning changed) needs the bump to fail closed.

Check `git tag` for the plugin's last released version before deciding. If the number's introducing version is already tagged, the carve-out does not apply and you bump regardless.

A consequence of the default: an in-window key rename reads as unavailable on an artifact written before the rename, rather than resolving to the old key, since no bump marks the boundary.

## The second carve-out: additive optional keys

A released schema does not bump for a purely additive optional key whose absence reads as a defined default, or for a widened value space that no earlier artifact can carry, because no reader can misread an old artifact and a bump would only make history unreadable. Telemetry schema 3 is the precedent: `run.repo` and `run.target` read as unavailable when absent, and the one consumer that requires them (`telemetry_share.py`) refuses an identity-less manifest on its own. A key a reader would silently treat as present, or a field whose meaning changed, is not additive and bumps.

## Which artifacts carry a schema

The field earns its place where an artifact outlives the run that wrote it, or crosses a validating producer/consumer boundary whose reader did not write it. `pipeline-state.json` and `dispatch-plan.json` carry none: they are read only by this plugin within one run. `pipeline-result.json` and `run-config.json` carry none even though pirategoat-bot parses the former and writes the latter; that cross-repo contract is tracked by reading the bot's source before changing either file. After step 6, `dispatch-plan.json` also carries the optional plan-level `dispatch_waves` record, which versions itself with its own `schema: 1` (`dispatch_status.DISPATCH_WAVES_SCHEMA`) because `load_dispatch_plan()` validates it on every read; a plan without it is a legacy plan with no waves. The record's `grace_seconds` defaults to 180. The watchdog (`agents_status.py`) adds two optional keys under the output-directory lock when it exits SLOT_FREE, still `schema: 1` because a reader that ignores them only loses release tracking: `released` (reviewer name, from `wave_1` or `queued`, to `{"at": latest release, "count": releases, "counted": releases that count toward abandonment}`, an ISO-8601 time, a positive int and an int from 0 to `count`; no other entry form is valid) and `last_release` (`at`, ISO-8601, and `terminal_count`, a non-negative int). A NOT_DISPATCHED row with `dispatch_status.MAX_RELEASES` (2) counted releases, past its latest grace window, is abandoned by the watchdog (a release counts only when nothing of the review was RUNNING or pending and no reviewer ended since the previous release; `dispatch_status.record_release()`) and counted in the manifest's `never_started`. Known limitation: host threads held by TIMED_OUT reviewers, by accepted launches still bootstrapping past grace, or by hung accepted launches are invisible to that rule, so under a very small host limit a refusal caused by contention can count toward abandonment; grace runs from the release stamp, not the actual launch; an abandoned reviewer that starts before step 8 closes intake still counts (see `review-pipeline.md`). The manifest's `late_starts` is an upper bound on rejected launches: a healthy start slower than `grace_seconds` counts too.

| Artifact | Schema | Producing authority |
|---|---:|---|
| `reviewers/<agent>/review.draft.json`, `reviewers/<agent>/review.json` | 2 | `REVIEW_OUTPUT_SCHEMA`, `scripts/review/agent/output.py` (the optional `verifies` check field is additive; an absent key reads as "cites nothing") |
| `review-findings.json` (the findings ledger) | 3 | `LEDGER_SCHEMA`, `scripts/review/findings_ledger.py`. Since 1.119.6 a finding's `sources` may cite a confirmed orchestrator note as `{"reviewer": "orchestrator", "id": "nN"}`; additive under the second carve-out, because a ledger written before it never carries one, this version's readers project either form, and the three recorded field runs under `tests/fixtures/review-runs/` stay readable. Since 1.119.6 an `invalidated_assessments` record may hold a null `text` and an `invalidated_recommendations` record an all-empty prior (revised text displaced nothing); the same carve-out, for the same reason |
| `review-intake.json` | 2 | `close_review_intake()`, `scripts/review/reviewer_lifecycle.py` |
| Telemetry JSONL events and `<log>.manifest.json` | 3 | `EVENT_SCHEMA`, `scripts/review/telemetry.py`. The manifest's `dispatch_waves` section (with `availability.dispatch_waves`) is additive under the second carve-out: a manifest without it reads as a run with no wave record |
| `synthesis-agents.json` | 1 | `LIFECYCLE_SCHEMA`, `scripts/review/synthesis_lifecycle.py` |
| `usage-snapshot.json` | 1 | `SNAPSHOT_SCHEMA`, `scripts/analysis/usage_snapshot.py` |
| `dependency-refresh.json` | 1 | `REPORT_SCHEMA`, `scripts/review/dependency_refresh.py`; may carry `superseded`, an additive optional list (newest last, at most 5) of the earlier canonical reports a later save replaced, each with `superseded_at`; added under the additive-key carve-out without a bump |
| `observed_reads` payload in transcript enrichment | 2 | `_OBSERVED_READS_SCHEMA`, `scripts/analysis/review_transcript.py`; the same-named constant in `review_metrics/contracts.py` is the consumer's expected value and moves in lockstep |
| `review_run_metrics.py --format json` report | 5 | `_REPORT_SCHEMA`, `scripts/analysis/review_metrics/contracts.py`. The per-run `dispatch_waves` section, its `metric_availability` family and the aggregate's `dispatch_waves` block are additive under the second carve-out: an older report without them reads as unmeasured, never as "no queueing" |
| `reviewers/<reviewer>/assignment.json` | 5 | `persist_review_assignment()`, `scripts/review/agent/bootstrap.py` |
| `reconciliation-context.json` | 4 | `main()`, `scripts/review/reconciliation_context.py`; carries `orchestrator_notes` (registered through `reconciliation_notes.py`) and the `verify_items`, `context_items` and `change_purpose_problems` parsed by `change_purpose.py` |
| `decision-critic-adjustments.json`, `decision-critic-verdict.json` | 2 | `ADJUSTMENTS_SCHEMA`, `VERDICT_MARKER_SCHEMA`, `scripts/review/critic_adjustments.py`; the orchestrator's adjudication request (stdin only, never persisted) validates at `ADJUDICATION_SCHEMA` |
| Per-agent sidecars: worktree baseline and hygiene | 1 | literal at the write site |
| Per-agent scope summaries (`reviewers/<reviewer>/scope-summary*.json`) | 4 | `write_scope_summary()`, `scripts/review/agent/scope.py`; the integer both readers gate on is `reviewer_lifecycle.SCOPE_SUMMARY_SCHEMA`, because this artifact has **two** independent readers — `bootstrap.load_scope_facts()` and `manifest_sections.aggregate_file_review()` — and a bump that moves one leaves the other silently unmeasured |

**Exception:** `review-context.json` and `issue-context.json` carry `version: 1`, and that key is not ours. pirategoat-bot writes both files and asserts on that field (`src/orchestrator-review.test.js`, `src/orchestrator-linear.test.js`). Leave it alone.

## Reader behavior

Readers accept exactly the schema they were written against and route anything else down their unsupported path: never a crash, and never a silent read of fields whose meaning the producer did not vouch for. At validating boundaries, tests pin the exact integer and exercise prior and future integers, numeric strings, missing keys, booleans, and non-object payloads where that boundary accepts external input. Dropping support for an old schema is allowed; reporting a wrong measurement for artifacts written under it is not (see `_BUILDER_ENV_REQUIRED` in `scripts/analysis/review_transcript.py` for the transcript form of this).
