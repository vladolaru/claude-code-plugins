# Detection Benchmark Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove two sources of complexity from the detection benchmark identified in the 2026-08-09 critique — dead per-check majority voting and the inferred `dispatched` boolean — replacing them with strictly simpler, explicit mechanisms. (A third candidate, the model-attribution gate, was dropped at the spec gate — see D2.)

**Architecture:** All changes are confined to the eval harness (`plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py`, `tests/helpers/graders.py`, their tests, and TESTING.md). No production pipeline code changes. Behavior contract: the aggregate pass/fail decision for multi-trial runs is unchanged (the outright-majority gate already dominated); the model gate is untouched (D2 dropped at spec gate); report consumers switch from an inferred boolean to an explicit status enum.

**Tech Stack:** Python 3 stdlib, pytest.

**Source critique:** `.claude/docs/analysis/2026-08-09-claude-detection-benchmark-branch-analysis.md`

---

## Explicitly out of scope (and why)

| Critique item | Why deferred |
|---|---|
| Doctrine-floor recalibration; abstention `NO_DOMAIN_FILES` verdict conflict | Requires editing production agent definitions / shared protocol — changes reviewer behavior in real reviews, deserves its own change with its own validation. |
| Generative fixture pipeline (source trees → git-generated diffs) | New infrastructure, not cleanup; existing guards work. |
| CLI-hygiene reverts (`--trials` presence dance, append-mode pre-flight) | Shipped, working, tested; reverting is churn, not simplification. |

## Design decisions locked in

**D1 — Aggregation.** `aggregate_detection_trials` keeps exactly one check: a strict majority of trials must pass outright (`need = trials // 2 + 1`). Rationale: a majority of outright-passing trials *implies* every per-check majority (those same trials passed each check), so per-check votes can never be the sole failure — they only duplicated diagnostics that `per_trial_failures` already carries in full. The `key` parameter becomes unused and is removed. Detail keeps `{trials, per_trial}`; the caller continues to add `per_trial_failures`, `per_trial_passed`, `models`, and (new, Task 2) `per_trial_status`.

**D2 — Model gate: DROPPED after spec gate (2026-08-09, decision-reviewer verdict REVISE).** The original design gated on membership only and deleted `_primary_model`. The spec gate refuted the core premise: the "contrived" vouching hole is the *normal* case for the three haiku-tier agents (go/python/rust-tests-reviewer, all keyed) — auxiliary calls run on haiku, so a haiku aux call satisfies membership even when the main loop ran elsewhere, making the gate inert exactly where routing drift would matter, and their `expect_not_applicable` keys would pass on any model, hiding the drift in the score too. Weighing the risks: a silent mismeasurement channel is worse for a benchmark than a loud heuristic rejection (which carries the models list and is diagnosable), and no false failure from weight attribution was ever observed live. Current gate stays unchanged; no task for D2.

**D3 — Status enum.** Every code path that knows its outcome stamps an explicit `status` (module constant `ENTRY_STATUSES`):

| Status | Set where | Meaning |
|---|---|---|
| `graded` | `run_dispatch_scenario` success paths (output_pair keyed/unkeyed, signal_format) | live model run produced a graded artifact |
| `bootstrap_only` | no_domain_files / error_exit grader paths | deterministic entry, no model call by design |
| `agent_missing` | agent definition file absent | pre-dispatch refusal |
| `routing_drift` | `check_model_routing` refusal | pre-dispatch refusal |
| `bootstrap_failed` | bootstrap nonzero rc | pre-dispatch failure |
| `cli_missing` | `dispatch_agent` (claude not on PATH, incl. FileNotFoundError) | no model call |
| `timed_out` | `dispatch_agent` timeout | model calls likely occurred; no gradable evidence |
| `dispatch_error` | `dispatch_agent` non-JSON output, `is_error` payload, nonzero rc | dispatch failed after invocation |
| `model_mismatch` | `dispatch_agent` membership gate | run rejected: wrong instrument |
| `harness_error` | exception handlers in `main()`; unknown-grader fallthrough in `run_dispatch_scenario` | harness bug/infra failure |

Wiring: `dispatch_agent` returns `status` inside its evidence dict (each early return sets it; success sets `completed` → mapped to `graded`/`dispatch_error` by the caller — no, simpler: `dispatch_agent` sets the terminal failure statuses and `"completed"`; `run_dispatch_scenario` maps `completed` + successful grading to `graded`, and puts `status` into every `GradeResult.detail` it returns, including the currently detail-less failure results). The `main()` exception handlers attach `detail={"status": "harness_error"}`.

Report changes: per-entry `status` replaces `dispatched` + `dispatch_count`. Entry status is derived from `result.detail` ONLY (spec-gate fix: never from `trial_grades` directly, so a harness error raised AFTER trials completed cannot masquerade as `graded`/`degraded`): a detail carrying `per_trial_status` yields `graded` when every trial status is `graded` else `degraded`; any other detail yields its own `status` (default `harness_error`). `per_trial_status` is computed and attached to the aggregate detail inside the try block, right after aggregation. Note `degraded` is a deliberate semantic change from `dispatched`: it describes gradability, not spend — a trial that dispatched and was then rejected counts as not graded. `model_dispatched` is removed everywhere. `ENTRY_STATUSES` includes `degraded` (aggregate-only value).

Consumer guidance (TESTING.md): reviewer-behavior pass rates filter on `status == "graded"`. `timed_out` is explicitly documented as "model calls likely occurred (money spent) but no gradable evidence" — no longer silently conflated with never-dispatched.

**D4 — Docs/changelog.** TESTING.md sections (Dispatch identity, Multi-trial semantics, Report schema) are rewritten in place per task. CHANGELOG 1.114.0 (unpushed — coalescing rule) bullets that describe majority voting and the `dispatched` flag are reworded to the new contract. No version bump (unpushed 1.114.0 absorbs it).

---

### Task 1: Collapse trial aggregation to the outright-majority gate

**Files:**
- Modify: `plugins/pirategoat-tools/tests/helpers/graders.py:608-675` (`aggregate_detection_trials`)
- Modify: `plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py:1093` (caller drops `key` arg)
- Test: `plugins/pirategoat-tools/tests/grading/test_graders.py:680-806` (`TestAggregateDetectionTrials` rewrite)
- Modify: `plugins/pirategoat-tools/tests/TESTING.md` (Multi-trial semantics section)

- [ ] **Step 1: Rewrite `TestAggregateDetectionTrials`** to the new contract (replace the class wholesale):

```python
class TestAggregateDetectionTrials:
    """Aggregation = strict majority of trials passing outright.

    Per-check majority votes were removed: an outright majority implies a
    per-check majority for every check (the same passing trials passed each
    one), so per-check votes could never be the sole failure and only
    duplicated per_trial_failures diagnostics.
    """

    @staticmethod
    def _grade(passed, detail=None):
        return GradeResult(
            passed=passed, score=1.0 if passed else 0.0,
            failures=[] if passed else ["some check failed"],
            checks_run=1, checks_passed=1 if passed else 0,
            detail=detail,
        )

    def test_majority_passing_trials_pass(self):
        grades = [self._grade(True), self._grade(True), self._grade(False)]
        result = aggregate_detection_trials(grades)
        assert result.passed

    def test_minority_passing_trials_fail(self):
        grades = [self._grade(True), self._grade(False), self._grade(False)]
        result = aggregate_detection_trials(grades)
        assert not result.passed
        assert any("1/3" in f for f in result.failures)

    def test_even_trials_require_strict_majority(self):
        # --trials 2: one pass is not "more than half" — both must pass.
        grades = [self._grade(True), self._grade(False)]
        assert not aggregate_detection_trials(grades).passed
        assert aggregate_detection_trials(
            [self._grade(True), self._grade(True)]).passed

    def test_single_check_regardless_of_key_complexity(self):
        result = aggregate_detection_trials([self._grade(True)])
        assert result.checks_run == 1
        assert result.checks_passed == 1

    def test_unreadable_trial_detail_never_improves_aggregate(self):
        # A failed trial with detail=None is just a failed trial.
        grades = [self._grade(True), self._grade(False, detail=None),
                  self._grade(False, detail=None)]
        result = aggregate_detection_trials(grades)
        assert not result.passed
        # Its detail slot is preserved (as {}) so per-trial lists stay
        # index-aligned with the requested trial count.
        assert result.detail["per_trial"] == [
            grades[0].detail or {}, {}, {}]

    def test_detail_carries_trial_count_and_per_trial(self):
        d0 = {"verdict": "approve", "compliance_passed": True}
        result = aggregate_detection_trials([self._grade(True, d0)])
        assert result.detail["trials"] == 1
        assert result.detail["per_trial"] == [d0]
```

- [ ] **Step 2: Run to verify failures** (old signature takes `key`, old semantics vote per check):
Run: `pytest plugins/pirategoat-tools/tests/grading/test_graders.py::TestAggregateDetectionTrials -v`
Expected: FAIL (TypeError on arity and/or check-count assertions)

- [ ] **Step 3: Replace `aggregate_detection_trials` in `graders.py`:**

```python
def aggregate_detection_trials(trial_grades: List[GradeResult]) -> GradeResult:
    """Aggregate multi-trial dispatches: a strict majority of trials must
    pass outright.

    Per-check majority votes were removed (2026-08-09): a majority of
    outright-passing trials implies a per-check majority for every check —
    those same trials passed each one — so per-check votes could never be
    the sole failure and only duplicated the diagnostics per_trial_failures
    already carries in full. With an even trial count the threshold is
    strictly more than half, so --trials 2 demands both trials pass. A
    trial with an unreadable/None detail is simply a failed trial —
    unreadable evidence never improves the aggregate.
    """
    trials = len(trial_grades)
    need = trials // 2 + 1
    passing = sum(1 for grade in trial_grades if grade.passed)
    result = _grade([
        (passing >= need,
         f"only {passing}/{trials} trials passed outright (need {need})"),
    ])
    result.detail = {
        "trials": trials,
        "per_trial": [grade.detail or {} for grade in trial_grades],
    }
    return result
```

- [ ] **Step 4: Update the caller** in `eval_agent_compliance.py` (line ~1093): `result = aggregate_detection_trials(trial_grades)` (drop `, key`).

- [ ] **Step 5: Run the grading suites:**
Run: `pytest plugins/pirategoat-tools/tests/grading/ -v 2>&1 | tail -15`
Expected: PASS (Task 3's metadata test still passes at this point since `model_dispatched` handling is untouched)

- [ ] **Step 6: Update TESTING.md** — replace the **Multi-trial semantics** paragraph body with:

> `--trials N` re-dispatches each *keyed* agent N times (unkeyed agents always run once; the `Running:` line prints once, so re-dispatches are silent). The aggregate passes when a strict majority of trials (`N // 2 + 1`) passed outright — so `--trials 2` demands both trials pass. There are no per-check votes: an outright majority implies a per-check majority for every check, and per-trial diagnostics live in `per_trial_failures`. An unreadable or raising trial is a failed trial. The aggregate is a single check, so its check counts are not comparable with single-trial check counts — the comparative metric remains per-entry `passed`.

- [ ] **Step 7: Update CHANGELOG.md** (1.114.0, unpushed — coalesce): in the "Detection benchmark in the compliance eval" Added bullet, change "controls nondeterminism through majority voting" → "controls nondeterminism by requiring a strict majority of trials to pass outright". In the Fixed bullet "Detection benchmark grading stays tied to the evidence it measures", drop the clause "whole-trial majority failures now participate in aggregate counters and score," (superseded — the whole-trial majority is now the only aggregate check).

- [ ] **Step 8: Commit**

```bash
git add plugins/pirategoat-tools/tests/helpers/graders.py \
        plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py \
        plugins/pirategoat-tools/tests/grading/test_graders.py \
        plugins/pirategoat-tools/tests/TESTING.md \
        plugins/pirategoat-tools/CHANGELOG.md
git commit -m "refactor(grading): collapse trial aggregation to the outright-majority gate"
```

(Body: context = per-check votes + outright gate; problem = outright majority implies every per-check majority so the votes were dead machinery with a doc caveat about incomparable counts; solution = single gate, per-trial diagnostics unchanged.)

### Task 2: Replace the inferred dispatched flag with an explicit status enum

**Files:**
- Modify: `plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py` (`dispatch_agent`, `run_dispatch_scenario`, `main()` report assembly; new `ENTRY_STATUSES` constant)
- Test: `plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py` (`TestDispatchReportMetadata` rewrite; `test_empty_report_path_is_rejected_before_dispatch` detail fixture)
- Modify: `plugins/pirategoat-tools/tests/TESTING.md` (Report schema section)

- [ ] **Step 1: Rewrite `TestDispatchReportMetadata`:**

```python
class TestDispatchReportMetadata:
    def test_entry_status_is_explicit_not_inferred(self, tmp_path, monkeypatch):
        # A multi-trial aggregate where one trial timed out must report
        # status "degraded" with per-trial statuses — consumers filter
        # reviewer-behavior pass rates on status == "graded" without
        # inferring anything from evidence shape.
        agent = "security-reviewer"
        scenario = {
            "agents": [agent],
            "expected": {agent: {"verdict_in": ["approve"]}},
        }
        trial_grades = iter([
            _eval_mod.GradeResult(
                passed=False, score=0.0,
                detail={"status": "graded"},
            ),
            _eval_mod.GradeResult(
                passed=False, score=0.0, detail={"status": "timed_out"},
            ),
            _eval_mod.GradeResult(passed=False, score=0.0),
        ])
        report_path = tmp_path / "report.json"

        monkeypatch.setattr(_eval_mod, "SCENARIOS", {"sample": scenario})
        monkeypatch.setattr(
            _eval_mod, "run_dispatch_scenario", lambda *args: next(trial_grades),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                str(EVAL_SCRIPT), "--dispatch", "--scenario", "sample",
                "--agent", agent, "--trials", "3",
                "--report-out", str(report_path),
            ],
        )

        with pytest.raises(SystemExit) as exc:
            _eval_mod.main()

        assert exc.value.code == 1
        entry = json.loads(report_path.read_text())["results"][0]
        assert entry["status"] == "degraded"
        assert entry["detail"]["per_trial_status"] == [
            "graded", "timed_out", "harness_error"]
        assert "dispatched" not in entry
        assert "dispatch_count" not in entry

    def test_single_trial_entry_carries_its_detail_status(
        self, tmp_path, monkeypatch,
    ):
        agent = "security-reviewer"
        scenario = {"agents": [agent], "expected": {}}
        report_path = tmp_path / "report.json"

        monkeypatch.setattr(_eval_mod, "SCENARIOS", {"sample": scenario})
        monkeypatch.setattr(
            _eval_mod, "run_dispatch_scenario",
            lambda *args: _eval_mod.GradeResult(
                passed=True, score=1.0, checks_run=1, checks_passed=1,
                detail={"status": "graded"},
            ),
        )
        monkeypatch.setattr(
            sys, "argv",
            [
                str(EVAL_SCRIPT), "--dispatch", "--scenario", "sample",
                "--agent", agent, "--report-out", str(report_path),
            ],
        )

        with pytest.raises(SystemExit) as exc:
            _eval_mod.main()

        assert exc.value.code == 0
        entry = json.loads(report_path.read_text())["results"][0]
        assert entry["status"] == "graded"

    def test_status_vocabulary_is_pinned(self):
        assert _eval_mod.ENTRY_STATUSES == {
            "graded", "bootstrap_only", "agent_missing", "routing_drift",
            "bootstrap_failed", "cli_missing", "timed_out", "dispatch_error",
            "model_mismatch", "harness_error", "degraded",
        }
```

Also update `test_empty_report_path_is_rejected_before_dispatch`'s fake detail from `{"model_dispatched": False}` to `{"status": "graded"}` (any valid status — the test's subject is the exit path).

- [ ] **Step 2: Run to verify failures:**
Run: `pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestDispatchReportMetadata -v`
Expected: FAIL (no ENTRY_STATUSES, entries carry dispatched/dispatch_count)

- [ ] **Step 3: Implement in `eval_agent_compliance.py`.**

Add near `_DISPATCHABLE_MODELS`:

```python
# Explicit per-entry outcome vocabulary. Each value is stamped by the code
# path that KNOWS what happened — never inferred from evidence shape.
# "degraded" is aggregate-only: a multi-trial entry where not every trial
# reached "graded". Consumers computing reviewer-behavior pass rates filter
# on status == "graded"; "timed_out" means model calls likely occurred
# (money spent) but produced no gradable evidence.
ENTRY_STATUSES = {
    "graded",          # live run produced a graded artifact
    "bootstrap_only",  # deterministic entry, no model call by design
    "agent_missing",   # pre-dispatch: agent definition file absent
    "routing_drift",   # pre-dispatch: frontmatter/registry mismatch
    "bootstrap_failed",  # pre-dispatch: bootstrap exited nonzero
    "cli_missing",     # claude CLI not found; no model call
    "timed_out",       # dispatch timeout; no gradable evidence
    "dispatch_error",  # non-JSON output, session error, nonzero exit
    "model_mismatch",  # run rejected: wrong model instrument
    "harness_error",   # eval-harness exception
    "degraded",        # aggregate: not every trial reached "graded"
}
```

`dispatch_agent` changes — every return carries a status in evidence:
- CLI missing (both sites): `return 1, "ERROR: ...", {"status": "cli_missing"}`
- Timeout: `return 1, "ERROR: ...", {"status": "timed_out"}`
- Non-JSON: `return 1, "ERROR: ...", {"status": "dispatch_error"}`
- After building `evidence`, set `evidence["status"] = "completed"` placeholder is NOT used — instead: model mismatch → `evidence["status"] = "model_mismatch"`; `is_error` or nonzero rc → `evidence["status"] = "dispatch_error"`; otherwise `evidence["status"] = "completed"`. (`"completed"` is internal to `dispatch_agent` — `run_dispatch_scenario` upgrades it to `"graded"` once grading actually runs; it never reaches a report.)

`run_dispatch_scenario` changes:
- Agent def missing → `detail={"status": "agent_missing"}` on the returned GradeResult.
- `check_model_routing` drift → `detail={"status": "routing_drift"}`.
- Bootstrap failure → `detail={"status": "bootstrap_failed"}`.
- `error_exit` and `no_domain_files` grader paths (both pass and fail results) → set `result.detail = dict(result.detail or {}, status="bootstrap_only")`.
- Dispatch rejected (`rc != 0`): `detail` keeps `dispatch_rejected: True`, `dispatch_evidence`, `output_dir`, and replaces `model_dispatched` with `"status": dispatch_evidence.get("status", "dispatch_error")`.
- Graded paths: replace every `model_dispatched=model_dispatched` in detail construction with `status="graded"` (unkeyed compliance detail, keyed detection detail, signal_format detail). Delete the `model_dispatched = bool(...)` inference and its comment block.

`main()` changes:
- Both `except Exception` handlers: add `detail={"status": "harness_error"}` to the constructed GradeResult.
- Also stamp `status` on trial grades whose detail is None/missing status (defensive normalization at aggregation time): when building the aggregate, compute `per_trial_status = [(g.detail or {}).get("status", "harness_error") for g in trial_grades]` and set `result.detail["per_trial_status"] = per_trial_status`.
- Entry meta: delete `dispatch_count`/`dispatched` computation. New:

```python
                meta = entry_meta[(scenario_name, agent_name)]
                if trial_grades:
                    per_trial_status = [
                        (g.detail or {}).get("status", "harness_error")
                        for g in trial_grades
                    ]
                    result.detail["per_trial_status"] = per_trial_status
                    meta["status"] = (
                        "graded"
                        if all(s == "graded" for s in per_trial_status)
                        else "degraded"
                    )
                else:
                    meta["status"] = (result.detail or {}).get(
                        "status", "harness_error")
```

- Report entry dict: replace the `"dispatch_count"` and `"dispatched"` lines with `"status": entry_meta[(scenario_name, agent_name)]["status"],`.

- [ ] **Step 4: Run the full grading + related suites:**
Run: `pytest plugins/pirategoat-tools/tests/grading/ -v 2>&1 | tail -15`
Expected: PASS

- [ ] **Step 5: Update TESTING.md Report schema paragraph.** Replace the `dispatched` sentence block with:

> `status` (explicit outcome stamped by the code path that knows what happened — never inferred from evidence shape): `graded` (live run, graded artifact), `bootstrap_only` (deterministic entry, no model call by design), pre-dispatch refusals/failures (`agent_missing`, `routing_drift`, `bootstrap_failed`), dispatch failures (`cli_missing`, `timed_out`, `dispatch_error`, `model_mismatch`), `harness_error`, and — aggregates only — `degraded` (not every trial reached `graded`; see `detail.per_trial_status`). Reviewer-behavior pass rates filter on `status == "graded"`. `timed_out` means model calls likely occurred (money spent) but produced no gradable evidence — it is deliberately not conflated with never-dispatched.

Also update the `detail` discrimination sentences: `detail: null` cases are now discriminated by `status` (and `keyed`); remove the `dispatched` cross-referencing prose.

- [ ] **Step 6: Update CHANGELOG.md** (coalesce into 1.114.0): reword the Fixed bullet "…report requests and dispatch attribution fail honestly" — replace "multi-trial results expose the number of model-backed attempts and enter reviewer pass rates only when every requested trial dispatched" with "each report entry carries an explicit `status` stamped by the code path that produced it (`graded`/`degraded`/failure kinds), replacing inference from dispatch evidence".

- [ ] **Step 7: Commit**

```bash
git add plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py \
        plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py \
        plugins/pirategoat-tools/tests/TESTING.md \
        plugins/pirategoat-tools/CHANGELOG.md
git commit -m "refactor(grading): report explicit entry status instead of inferred dispatched flag"
```

### Task 3: Full verification + live smoke (optional) + review gate

- [ ] **Step 1: Full plugin test run:**
Run: `pytest plugins/pirategoat-tools/tests/ 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 2: Offline harness smoke** (no model calls):
Run: `python3 plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py --dispatch --scenario nonexistent; echo "exit=$?"`
Expected: `exit=2` with unknown-scenario error (CLI wiring intact).

- [ ] **Step 3: Code review gate** — dispatch `pirategoat-tools:code-reviewer` on the branch delta for the three commits; address findings; amend/fix-forward as needed.
