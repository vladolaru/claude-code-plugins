# Review Pipeline Contract Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six reviewed regressions through shared derivation, explicit artifact snapshots, complete projections, disjoint scope math, and a two-phase terminal handoff.

**Architecture:** Extend the pipeline's existing canonical boundaries instead of patching consumers independently: `verdict_rules.py` derives channel-aware review state, bootstrap partitions scope once, critic saves always replace their adjustment snapshot, and step 11 treats `pipeline-result.json` as a commit marker written only after `review-report.md` exists. Existing bot-facing filenames and verdict vocabularies remain unchanged.

**Tech Stack:** Python 3 standard library, pytest, Markdown agent definitions, deterministic Codex compatibility generator.

---

## File structure

- `plugins/pirategoat-tools/scripts/review/verdict_rules.py`: shared severity counting, channel-aware gating verdict, and advisory suppression derivation.
- `plugins/pirategoat-tools/scripts/review/agent/output.py`: consume shared verdict derivation and project applied plus rejected critic decisions.
- `plugins/pirategoat-tools/scripts/review/critic_adjustments.py`: consume shared derivation after mutations and record explicit refuted outcomes.
- `plugins/pirategoat-tools/scripts/review/agent/bootstrap.py`: create ordered, disjoint scope populations and persist reachable progress counts.
- `plugins/pirategoat-tools/scripts/review/critic.py`: replace the adjustment snapshot on every successful save.
- `plugins/pirategoat-tools/scripts/review/orchestration.py`: prepare step 11 state, withhold the terminal result until the report exists, then publish it.
- `plugins/pirategoat-tools/scripts/review/briefings.py`: distinguish pending report authoring from completed terminal publication.
- `plugins/pirategoat-tools/agents/decision-reviewer.md`: state record provenance accurately.
- Focused tests under `plugins/pirategoat-tools/tests/review/` and `plugins/pirategoat-tools/tests/review/agent/`: prove each regression and the shared invariants.
- `plugins/pirategoat-tools/CHANGELOG.md`: fold concise user-visible fixes into the existing unpushed `1.114.0` entry.

### Task 1: Centralize channel-aware verdict derivation

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/review/verdict_rules.py`
- Modify: `plugins/pirategoat-tools/scripts/review/agent/output.py`
- Modify: `plugins/pirategoat-tools/scripts/review/critic_adjustments.py`
- Test: `plugins/pirategoat-tools/tests/review/test_verdict_rules.py`
- Test: `plugins/pirategoat-tools/tests/review/test_critic_adjustments.py`
- Test: `plugins/pirategoat-tools/tests/review/agent/test_output.py`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`

- [ ] **Step 1: Write failing shared-derivation tests**

Add tests proving that a mixed population such as one high advisory plus one low blocking issue produces complete counts for both issues, an `approve` gating verdict, `advisory_suppressed: 1`, and `verdict_without_advisory: request_changes`. Add a critic-adjustment regression where an unrelated correction triggers recomputation without allowing the advisory high to gate.

```python
issues = [
    {"severity": "high", "channel": "advisory"},
    {"severity": "low"},
]
derived = derive_review_state(issues)
assert derived["counts"]["high"] == 1
assert derived["verdict"] == "approve"
assert derived["advisory"] == {
    "advisory_suppressed": 1,
    "verdict_without_advisory": "request_changes",
}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest plugins/pirategoat-tools/tests/review/test_verdict_rules.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/agent/test_output.py -q`

Expected: failure because the shared derivation does not exist and critic recomputation gates on advisory findings.

- [ ] **Step 3: Implement one shared derivation**

Add a named result or dictionary-returning helper in `verdict_rules.py` that validates the full severity population, counts full and blocking populations, calls `verdict_for_counts()` for both, and derives suppression metadata. Replace `_verdict_for_issues()`, `_advisory_measurement()`, and `_recount_summary()` policy duplication with calls to that helper. Keep `_recount_summary()` only as a thin compatibility wrapper if tests or local structure benefit; it must no longer choose a gating population independently.

```python
def derive_review_state(issues):
    all_counts = severity_counts(issues)
    blocking_counts = severity_counts(
        issue for issue in issues if issue.get("channel") != "advisory"
    )
    verdict = verdict_for_counts(blocking_counts)
    all_verdict = verdict_for_counts(all_counts)
    advisory = {"advisory_suppressed": sum(
        issue.get("channel") == "advisory" for issue in issues
    )}
    if VERDICT_RANK[all_verdict] > VERDICT_RANK[verdict]:
        advisory["verdict_without_advisory"] = all_verdict
    return {"counts": all_counts, "verdict": verdict, "advisory": advisory}
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest plugins/pirategoat-tools/tests/review/test_verdict_rules.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/agent/test_output.py -q`

Expected: all tests pass.

- [ ] **Step 5: Update the unreleased changelog and commit**

Fold a concise bullet into `1.114.0` describing advisory-safe critic recomputation. Commit with `fix(pirategoat-tools): centralize channel-aware verdict derivation`.

### Task 2: Partition scope once for reachable progress

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/review/agent/bootstrap.py`
- Test: `plugins/pirategoat-tools/tests/review/agent/test_bootstrap.py`
- Test: `plugins/pirategoat-tools/tests/review/agent/test_output.py`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`

- [ ] **Step 1: Write failing partition and progress tests**

Add a pure-function test with duplicates and cross-population overlap, and an integration-level save-echo test proving list-only files do not create an unreachable denominator.

```python
partition = partition_scope_paths(
    inline=["src/a.py", "src/a.py", "src/shared.py"],
    deferred=["src/shared.py", "src/b.py", "src/b.py"],
    list_only=["src/a.py", "asset.bin", "asset.bin"],
)
assert partition["inline"] == ["src/a.py", "src/shared.py"]
assert partition["deferred"] == ["src/b.py"]
assert partition["list_only"] == ["asset.bin"]
assert partition["progress_total"] == 3
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap.py plugins/pirategoat-tools/tests/review/agent/test_output.py -q`

Expected: failure because scope populations are currently counted from overlapping lists and list-only files enter only the denominator.

- [ ] **Step 3: Implement ordered disjoint partitioning**

Create one helper that order-deduplicates inline paths, removes inline paths from deferred, and removes both from list-only. Use the normalized values for deferred ordering/persistence, telemetry union, `in_scope_count`, and `diffed_count`. Keep list-only paths in telemetry scope but outside progress totals.

- [ ] **Step 4: Defensively bound save-echo coverage**

Count claimed deferred paths as a set intersected with the sidecar's authoritative deferred set. Do not clamp a wrong number; omit progress when the sidecar is malformed. The valid producer path must make `covered <= in_scope_count` true by construction.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap.py plugins/pirategoat-tools/tests/review/agent/test_output.py -q`

Expected: all tests pass, including duplicate, overlap, and list-only cases.

- [ ] **Step 6: Update the unreleased changelog and commit**

Fold the reachable progress behavior into the existing deferred-review/save-echo bullet. Commit with `fix(pirategoat-tools): derive progress from disjoint scope sets`.

### Task 3: Make critic state replacement and projection complete

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/review/critic.py`
- Modify: `plugins/pirategoat-tools/scripts/review/critic_adjustments.py`
- Modify: `plugins/pirategoat-tools/scripts/review/agent/output.py`
- Modify: `plugins/pirategoat-tools/agents/decision-reviewer.md`
- Test: `plugins/pirategoat-tools/tests/review/test_critic.py`
- Test: `plugins/pirategoat-tools/tests/review/test_critic_adjustments.py`
- Test: `plugins/pirategoat-tools/tests/review/test_report_assembly.py`
- Test: `plugins/pirategoat-tools/tests/review/agent/test_output.py`
- Test: `plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`

- [ ] **Step 1: Write failing stale-state and accounting tests**

Add parameterized STAND/ESCALATE tests that preseed a pending REVISE file, save without `--adjustments`, and expect `decision-critic-adjustments.json` to become `{"schema": 1, "adjustments": []}`. Add mixed and all-refuted render tests expecting every adjustment id and explicit `refuted` outcomes.

- [ ] **Step 2: Write a failing provenance pin**

Extend the agent-definition integration tests to require language equivalent to “mechanically assembled and unedited” and “content originates in the reconciliator-authored ledger”, and forbid “Nothing in it was authored by an agent”.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `pytest plugins/pirategoat-tools/tests/review/test_critic.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_report_assembly.py plugins/pirategoat-tools/tests/review/agent/test_output.py plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -q`

Expected: stale adjustments survive, rejected decisions do not render, and the provenance pin fails.

- [ ] **Step 4: Replace the critic adjustment snapshot on every save**

Build `adjustments_to_write = adjustments if adjustments is not None else {"schema": 1, "adjustments": []}` after validation. Write findings, then the current adjustment snapshot, then the verdict commit artifact. Keep rejection paths write-free.

- [ ] **Step 5: Project every critic decision**

Record `spot_check: "refuted"` in each rejected ledger record. In `render_review_body()`, combine applied and rejected records into one decision section, retaining storage order within each bucket and rendering one `<adjustment_id> — <outcome>` line per record. Legacy applied strings remain `not_checked`; malformed records remain safely ignored as today.

- [ ] **Step 6: Correct the decision-reviewer provenance contract**

Replace the false authorship sentence with wording that identifies mechanical assembly and no post-assembly model editing while naming reconciliator provenance for findings, assessment, and clearances.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run: `pytest plugins/pirategoat-tools/tests/review/test_critic.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_report_assembly.py plugins/pirategoat-tools/tests/review/agent/test_output.py plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -q`

Expected: all tests pass.

- [ ] **Step 8: Update the unreleased changelog and commit**

Fold complete critic decision accounting and current-snapshot saves into the existing structured-critic bullets. Commit with `fix(pirategoat-tools): make critic artifacts complete snapshots`.

### Task 4: Publish the terminal result only after report handoff

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/review/orchestration.py`
- Modify: `plugins/pirategoat-tools/scripts/review/briefings.py`
- Modify: `plugins/pirategoat-tools/scripts/review/pipeline.py` only if state/routing needs a narrow generic seam
- Test: `plugins/pirategoat-tools/tests/review/test_pipeline.py`
- Test: `plugins/pirategoat-tools/tests/review/test_pipeline_integration.py`
- Test: `plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py`
- Test: `plugins/pirategoat-tools/tests/review/test_critic_adjustments.py`
- Test: `plugins/pirategoat-tools/tests/review/test_synthesis_lifecycle.py`
- Test: `plugins/pirategoat-tools/tests/review/test_report_assembly.py`
- Modify: `plugins/pirategoat-tools/AGENTS.md`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`

- [ ] **Step 1: Write failing two-phase integration tests**

Test the actual pipeline CLI or `_orchestrate_step_11()` plus guidance in two passes. First pass without `review-report.md` must not create `pipeline-result.json`, must leave step 11 out of `completed_steps`, and must print a blocking rerun handoff. After writing `review-report.md`, the second pass must create `pipeline-result.json`, set `report_path` to that exact existing file, complete step 11, and expose no open handoff.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_synthesis_lifecycle.py plugins/pirategoat-tools/tests/review/test_report_assembly.py -q`

Expected: first-pass tests fail because the terminal result is currently written before the report handoff.

- [ ] **Step 3: Split preparation from terminal publication**

Keep all settlement, verdict, degradation, hygiene, and usage derivation before the report check, storing the report-pending fact and projection fields in state. If `<output_dir>/review-report.md` is absent, remove no existing user artifact, write no result, and return with pending state. If it exists, construct and atomically publish `pipeline-result.json` with that file as `report_path`.

- [ ] **Step 4: Make briefing state explicit**

When report-pending, return authoring actions, a handoff that requires the report, `blocks_progress: True`, and an instruction to rerun step 11. When terminal publication succeeded, return presentation/output actions with no handoff and `blocks_progress: False`. Do not print “PIPELINE COMPLETE” while pending.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `pytest plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_synthesis_lifecycle.py plugins/pirategoat-tools/tests/review/test_report_assembly.py -q`

Expected: all tests pass across first pass, resume/re-entry, critic adjustment, lifecycle, and report assembly seams.

- [ ] **Step 6: Update architecture docs and changelog, then commit**

Update the step-11 architecture description to explain prepare/commit semantics and fold the resumable handoff fix into the existing report-authoring bullet. Commit with `fix(pirategoat-tools): make the report the terminal publication gate`.

### Task 5: Full verification and review gates

**Files:**
- Verify every file changed by Tasks 1-4
- Modify only files needed to resolve review-gate findings

- [ ] **Step 1: Run formatting and generated-output checks**

Run: `python3 scripts/generate_codex_compat.py --check`

Expected: exit 0. If the decision-reviewer definition is shared directly and generates no adapter, no generated files change.

- [ ] **Step 2: Run the complete plugin test suite**

Run: `pytest plugins/pirategoat-tools/tests/ -q`

Expected: all tests pass with zero failures.

- [ ] **Step 3: Verify the complete diff**

Run: `git diff --check ef755984...HEAD && git status --short && git log --oneline ef755984..HEAD`

Expected: no whitespace errors; only intended files are modified or committed; commit subjects map to the four logical changes.

- [ ] **Step 4: Dispatch the spec-compliance review gate**

Give a fresh reviewer the approved design, all six original comments, base `ef755984`, and current HEAD. Require a requirement-by-requirement verdict and resolve every gap before proceeding.

- [ ] **Step 5: Dispatch the code-quality review gate**

After spec approval, give a fresh code reviewer the same range and require correctness, recovery, artifact-coherence, compatibility, and test-quality review. Resolve every critical or important finding and re-run the gate until approved.

- [ ] **Step 6: Run fresh final verification**

Run: `pytest plugins/pirategoat-tools/tests/ -q && python3 scripts/generate_codex_compat.py --check && git diff --check ef755984...HEAD`

Expected: all commands exit 0.

- [ ] **Step 7: Report the git range**

Run `git rev-parse HEAD` after final verification and report the range with fixed start `9e5f4bc4` and that exact returned SHA as the end.
