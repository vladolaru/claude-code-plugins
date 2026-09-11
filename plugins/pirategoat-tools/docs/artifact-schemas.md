# Artifact schemas

Read this before changing the shape of any JSON artifact the pipeline writes, or before adding a new one. The rule itself is in `AGENTS.md`; this document explains when the rule applies, records the one carve-out, and lists the artifacts that carry a schema today.

## The rule

An artifact that carries a `schema` field gets that field bumped in the same commit as any change to its shape. A shape change is a key added, removed, or re-typed. Bump the producing constant, update `schemas/review-output.ts` if the artifact is declared there, and note the bump in the changelog. The key is always the integer `schema`, never `schema_version` and never a `version` string.

The rule exists because a schema number that lags the shape is worse than none: it states a compatibility guarantee the producer is not honoring.

## The carve-out: same unreleased window

A shape change made within the same UNRELEASED version that introduced the current schema number updates the contract in the same commit but does not bump, unless a reader must be able to tell the old shape from the new one. The number states a compatibility guarantee only once released, so bumping before release publishes a shape no artifact ever had; but a reader that would silently accept the old shape as the new one (missing required keys, a field whose meaning changed) needs the bump to fail closed.

Check `git tag` for the plugin's last released version before deciding. If the number's introducing version is already tagged, the carve-out does not apply and you bump regardless.

A consequence of the default: an in-window key rename reads as unavailable on an artifact written before the rename, rather than resolving to the old key, since no bump marks the boundary.

## The second carve-out: additive optional keys

A released schema does not bump for a purely additive optional key whose absence reads as a defined default, because no reader can misread an old artifact and a bump would only make history unreadable. Telemetry schema 3 is the precedent: `run.repo` and `run.target` read as unavailable when absent, and the one consumer that requires them (`telemetry_share.py`) refuses an identity-less manifest on its own. A key a reader would silently treat as present, or a field whose meaning changed, is not additive and bumps.

## Which artifacts carry a schema

The field earns its place where an artifact outlives the run that wrote it, or crosses a validating producer/consumer boundary whose reader did not write it. `pipeline-state.json` and `dispatch-plan.json` carry none: they are read only by this plugin within one run. `pipeline-result.json` and `run-config.json` carry none even though pirategoat-bot parses the former and writes the latter; that cross-repo contract is tracked by reading the bot's source before changing either file.

| Artifact | Schema | Producing authority |
|---|---:|---|
| `reviewers/<agent>/review.draft.json`, `reviewers/<agent>/review.json` | 2 | `REVIEW_OUTPUT_SCHEMA`, `scripts/review/agent/output.py` (the optional `verifies` check field is additive; an absent key reads as "cites nothing") |
| `review-findings.json` (the findings ledger) | 3 | `LEDGER_SCHEMA`, `scripts/review/findings_ledger.py` |
| `review-intake.json` | 2 | `close_review_intake()`, `scripts/review/reviewer_lifecycle.py` |
| Telemetry JSONL events and `<log>.manifest.json` | 3 | `EVENT_SCHEMA`, `scripts/review/telemetry.py` |
| `synthesis-agents.json` | 1 | `LIFECYCLE_SCHEMA`, `scripts/review/synthesis_lifecycle.py` |
| `usage-snapshot.json` | 1 | `SNAPSHOT_SCHEMA`, `scripts/analysis/usage_snapshot.py` |
| `dependency-refresh.json` | 1 | `REPORT_SCHEMA`, `scripts/review/dependency_refresh.py` |
| `observed_reads` payload in transcript enrichment | 2 | `_OBSERVED_READS_SCHEMA`, `scripts/analysis/review_transcript.py`; the same-named constant in `review_metrics/contracts.py` is the consumer's expected value and moves in lockstep |
| `review_run_metrics.py --format json` report | 5 | `_REPORT_SCHEMA`, `scripts/analysis/review_metrics/contracts.py` |
| `reviewers/<reviewer>/assignment.json` | 5 | `persist_review_assignment()`, `scripts/review/agent/bootstrap.py` |
| `reconciliation-context.json` | 4 | `main()`, `scripts/review/reconciliation_context.py`; carries `orchestrator_notes` (registered through `reconciliation_notes.py`) and the `verify_items`, `context_items` and `change_purpose_problems` parsed by `change_purpose.py` |
| `decision-critic-adjustments.json`, `decision-critic-verdict.json` | 2 | `ADJUSTMENTS_SCHEMA`, `VERDICT_MARKER_SCHEMA`, `scripts/review/critic_adjustments.py`; the orchestrator's adjudication request (stdin only, never persisted) validates at `ADJUDICATION_SCHEMA` |
| Per-agent sidecars: worktree baseline and hygiene | 1 | literal at the write site |
| Per-agent scope summaries (`reviewers/<reviewer>/scope-summary*.json`) | 4 | `write_scope_summary()`, `scripts/review/agent/scope.py` |

**Exception:** `review-context.json` and `issue-context.json` carry `version: 1`, and that key is not ours. pirategoat-bot writes both files and asserts on that field (`src/orchestrator-review.test.js`, `src/orchestrator-linear.test.js`). Leave it alone.

## Reader behavior

Readers accept exactly the schema they were written against and route anything else down their unsupported path: never a crash, and never a silent read of fields whose meaning the producer did not vouch for. At validating boundaries, tests pin the exact integer and exercise prior and future integers, numeric strings, missing keys, booleans, and non-object payloads where that boundary accepts external input. Dropping support for an old schema is allowed; reporting a wrong measurement for artifacts written under it is not (see `_BUILDER_ENV_REQUIRED` in `scripts/analysis/review_transcript.py` for the transcript form of this).
