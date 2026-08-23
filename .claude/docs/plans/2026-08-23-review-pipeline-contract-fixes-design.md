# Review Pipeline Contract Fixes Design

## Goal

Resolve the six reviewed regressions at their shared contract boundaries so the review pipeline publishes coherent machine state, gives subagents truthful provenance and handoffs, and cannot mark a run terminal before its deliverable exists.

## Design principles

The pipeline must derive each fact once from an explicitly named population, mechanically project every authoritative state without omission, and treat terminal artifacts as commit markers written only after every required handoff is satisfied. Optional files must represent the current snapshot rather than inherit meaning from whichever older file happens to remain.

## Architecture

### Channel-aware review derivation

`verdict_rules.py` will own a shared derivation over issue dictionaries in addition to the existing threshold ladder. The derivation will validate and count the complete issue population, isolate the blocking population by excluding `channel: advisory`, compute the gating verdict from blocking counts, and compute advisory suppression metadata from the complete population. `ReviewOutputBuilder` and `critic_adjustments` will both consume this derivation, so a freshly authored ledger and a critic-adjusted ledger cannot disagree about which findings gate or how suppression is reported.

The shared function will return the complete severity summary, the gating verdict, and the advisory measurement as one value. Callers will not independently filter, count, or compare verdict ranks. Invalid severities remain loud failures at the write boundary.

### Disjoint scope populations

Bootstrap will normalize the aggregated scope facts once into ordered, deduplicated, disjoint populations with explicit precedence: inline-diff files, deferred files not already inline, and list-only files in neither prior population. Telemetry scope remains the union of all three. Review-progress scope is the union of inline and deferred files because those are the populations the reviewer can cover through the supplied diff or `add_deferred_reviewed()`; list-only files remain visible to telemetry but do not create an unreachable progress denominator.

The deferred sidecar will receive the normalized deferred population, `in_scope_count` will be the size of the review-progress union, and `diffed_count` will be the size of the normalized inline population. The save echo will add only validated, unique deferred claims, making `0 <= covered <= in_scope_count` an invariant.

### Two-phase terminal publication

Step 11 will become a prepare-and-commit protocol without changing its step number or bot-facing filenames.

On the prepare pass, orchestration applies critic adjustments, rebuilds projections, derives the verdict and degradations, and records those facts in pipeline state. If `review-report.md` is absent, it does not write `pipeline-result.json`; guidance blocks progress, tells the orchestrator to author the report from the settled record, and explicitly requires rerunning step 11.

On the commit pass, the presence of `review-report.md` satisfies the handoff. Orchestration repeats its idempotent settlement checks, publishes `pipeline-result.json` with `report_path` pointing to the required report, clears the pending flag, and lets routing complete the step. Guidance reports the already-published artifacts and carries no open handoff. A process stopping after preparation remains resumable because the terminal result does not exist; a process with a terminal result is deliverable because the report already exists.

The bot contract remains unchanged: it still reads `pipeline-result.json` and then `review-report.md`. Resume discovery also remains unchanged because its existing result-file test becomes correct once the result file is a real commit marker.

### Complete critic decision projection

The findings ledger will retain the existing applied and rejected storage buckets, but the Markdown renderer will project both through one ordered decision view. Applied records preserve `verified` or `not_checked`; rejected records explicitly carry and render `refuted`. The section title will describe critic decisions rather than claiming every line was applied. Mixed and all-rejected batches will therefore render one line per decision.

The applier will stamp `spot_check: refuted` into each rejected ledger record instead of requiring renderers to infer it from the bucket name. This keeps the outcome explicit wherever the record is consumed.

### Current critic artifact snapshot

`critic.py --save` will always replace `decision-critic-adjustments.json`. A REVISE save writes the validated non-empty batch; STAND and ESCALATE write the canonical empty schema-1 batch. The verdict remains the last artifact published, preserving it as the save operation's commit point. A successful non-REVISE save therefore cannot leave an older pending batch semantically active, and downstream code needs no deletion race or absence-based inference.

### Truthful provenance

The decision-reviewer prompt will describe `review-record.md` as mechanically assembled and unedited while identifying findings, assessment, and clearances as projections of reconciliator-authored `review-findings.json` content. Machine measurements and run notes remain attributed to the pipeline. This distinguishes authorship, assembly, and edit history instead of collapsing them into a false machine-authored claim.

## Error handling and recovery

- A malformed issue population fails before any adjusted ledger write, as today.
- A missing report is a pending handoff, not a degradation and not a terminal result.
- A resumed step 11 is idempotent: settled adjustments are not reapplied, projections are regenerated from the canonical ledger, and terminal publication occurs only when the report exists.
- A non-REVISE critic save always overwrites stale adjustment state with a valid empty snapshot.
- Malformed sidecar counts remain absent from the save echo rather than repaired into plausible values.

## Testing

Each review comment gets a red-green regression test at the narrowest contract boundary:

1. An unrelated adjustment beside a high advisory and low blocking finding preserves APPROVE, recounts all severities, and refreshes advisory suppression metadata.
2. First-pass step 11 creates no `pipeline-result.json`, remains incomplete/resumable, and instructs a rerun; the second pass after creating `review-report.md` publishes a result whose report path exists.
3. Mixed and all-refuted adjustment ledgers render every adjustment id with its outcome.
4. The decision-reviewer prompt pins mechanical assembly and reconciliator provenance without the false no-agent-authorship claim.
5. Duplicate multi-domain paths and list-only paths produce bounded, reachable progress counts from disjoint populations.
6. STAND and ESCALATE saves replace a stale REVISE adjustments file with an empty canonical batch.

After focused suites pass, run all pirategoat-tools tests. Then dispatch a spec-compliance reviewer and a code-quality reviewer over the complete diff, resolve every blocking or important finding, re-run their gates as needed, and only then commit the behavioral fix with the required changelog and generated marketplace version synchronization.

## Compatibility and release notes

No bot-facing filename or verdict vocabulary changes. The deferred sidecar's schema-2 count semantics are corrected within the same unreleased `1.114.0` window, so the project schema rule does not require a bump. These are runtime behavior fixes and will be folded into the existing unpushed `1.114.0` changelog entry and marketplace version rather than creating another version bump.
