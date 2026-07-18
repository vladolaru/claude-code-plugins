# Mixed-Markup Routing Polarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unsound scoped evidence gate so token-silent PHP/PHTML changes dispatch accessibility review conservatively, with no dormant gate machinery or contradictory documentation left behind.

**Architecture:** `has_markup_changes` remains a positive recognizer shared by dispatch and scope budgeting. The planner deletes the generic `evidence_gated_extensions` negative branch, so mixed-markup detector silence reaches the existing conservative conditional default; registry and documentation describe that policy directly.

**Tech Stack:** Python 3 standard library, pytest, JSON agent registry, Markdown release and architecture documentation.

---

## File map

- `plugins/pirategoat-tools/tests/review/test_plan_dispatch.py`: behavior regressions for unknown PHP/PHTML composition surfaces, conservative backend routing, test-file interactions, and diff-fetch semantics.
- `plugins/pirategoat-tools/scripts/review/plan_dispatch.py`: conditional-routing layers; delete the generic scoped negative gate.
- `plugins/pirategoat-tools/scripts/review/agent_registry.json`: remove the obsolete field and align accessibility focus text.
- `plugins/pirategoat-tools/scripts/review/agent/scope.py`: update mixed-markup source-of-truth comments without changing classification or positive evidence.
- `plugins/pirategoat-tools/AGENTS.md`: remove the obsolete registry-field contract.
- `plugins/pirategoat-tools/CHANGELOG.md`: amend the unpushed `1.107.0` entry to describe conservative mixed-markup routing.
- `.claude/docs/analysis/2026-07-19-codex-php-renderer-routing-gate.md`: retain investigation and execution outcome.
- `.claude/docs/plans/2026-07-19-mixed-markup-routing-implementation.md`: retain this executable checklist.

### Task 1: Pin conservative mixed-markup behavior with failing tests

**Files:**
- Modify: `plugins/pirategoat-tools/tests/review/test_plan_dispatch.py:2126-2479`
- Modify: `plugins/pirategoat-tools/tests/review/test_plan_dispatch.py:2850-2910`

- [x] **Step 1: Replace gate expectations with routing outcomes**

Rename `TestA11yMarkupGatedDispatch` to `TestA11yMixedMarkupDispatch`. Add this regression after the existing positive renderer table:

```python
@pytest.mark.parametrize(
    "filepath, render_call",
    [
        ("theme/header.php", "get_header();"),
        ("theme/footer.php", "get_footer();"),
        ("theme/comments.php", "comments_template();"),
        ("views/shell.phtml", "my_plugin_render_shell();"),
    ],
)
def test_dispatches_when_mixed_markup_detector_is_silent(
    self, registry, filepath, render_call,
):
    status, reason = triage_conditional_agent(
        "a11y-reviewer",
        self._a11y_config(registry),
        [filepath],
        "compose rendered page",
        self._large_diffstat(filepath),
        diff_text=f"+ {render_call}",
    )
    assert status == "DISPATCH"
    assert "no triage signal to skip" in reason
```

Change the backend-query and PHP-loop tests to expect `DISPATCH` with `"no triage signal to skip"` in the reason. Change the production-PHP-plus-unrelated-test regression to expect the same conservative dispatch. Update class, method, and comment wording so none describes PHP/PHTML as evidence-gated.

- [x] **Step 2: Remove synthetic tests for the deleted configuration concept**

In `TestDiffFetchFailureConservatism`, delete `_gated_config()`, `test_evidence_gate_dispatches_when_scan_failed()`, and `test_evidence_gate_still_skips_on_successful_empty_scan()`. Keep blanket applicability-gate and generic detector-fetch tests intact; they cover separate active behavior.

- [x] **Step 3: Run the mixed-markup tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  plugins/pirategoat-tools/tests/review/test_plan_dispatch.py::TestA11yMixedMarkupDispatch -v
```

Expected: the unknown composition, backend-query, PHP-loop, and production-plus-test expectations fail because the current `evidence_gated_extensions` branch returns `SKIPPED_TRIAGE`.

### Task 2: Delete the scoped negative gate and align its contracts

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/review/plan_dispatch.py:1515-1650`
- Modify: `plugins/pirategoat-tools/scripts/review/agent_registry.json:412-449`
- Modify: `plugins/pirategoat-tools/scripts/review/agent/scope.py:383-432`
- Modify: `plugins/pirategoat-tools/AGENTS.md:126-140`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md:8-34`

- [x] **Step 1: Remove the runtime gate**

Delete Layer 5.6 from the `triage_conditional_agent()` docstring and delete this entire runtime block:

```python
gated_exts = config.get("evidence_gated_extensions")
non_test_domain_files = [f for f in domain_files if not is_test_file(f)]
if gated_exts and non_test_domain_files:
    exts = {_ext_of(f) for f in non_test_domain_files}
    if exts <= set(gated_exts):
        return "SKIPPED_TRIAGE", (
            f"all domain files are evidence-gated types ({', '.join(sorted(exts))}); "
            "no keyword or check matched"
        )
```

Retain the failed-diff safeguard, `require_triage_keyword_match`, and the final conservative `DISPATCH` unchanged.

- [x] **Step 2: Remove the registry field and revise focus**

Delete `a11y-reviewer.evidence_gated_extensions`. Replace the focus with:

```json
"focus": "ARIA correctness, keyboard operability, focus management, screen reader support, WCAG 2.2 AA — in any UI-emitting language (JSX/TSX, CSS/SCSS, server-rendered PHP/HTML, templates). Mixed PHP/PHTML dispatches conservatively because finite markup detectors cannot prove that arbitrary composition code emits no UI."
```

- [x] **Step 3: Remove obsolete documentation and amend release notes**

Delete the `evidence_gated_extensions` row from `plugins/pirategoat-tools/AGENTS.md`. In `scope.py`, describe PHP/PHTML as conservatively routed mixed-markup languages and remove claims that the class is evidence-gated or avoids every backend PHP review.

In changelog `1.107.0`:

- remove `evidence gates` from the executable-contract alignment list;
- state that partial detector silence never authorizes a skip, while explicit agent applicability gates remain separate;
- state that PHP/PHTML accessibility scope dispatches conservatively even when finite renderer/markup recognition is silent.

- [x] **Step 4: Run the targeted suites and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  plugins/pirategoat-tools/tests/review/test_plan_dispatch.py \
  plugins/pirategoat-tools/tests/review/test_criteria_coverage.py -v
```

Expected: all tests pass with no warnings.

### Task 3: Prove there are no leftovers and commit the fix

**Files:**
- Modify: `.claude/docs/analysis/2026-07-19-codex-php-renderer-routing-gate.md`
- Modify: `.claude/docs/plans/2026-07-19-mixed-markup-routing-implementation.md`

- [x] **Step 1: Audit obsolete symbols and policy prose**

Run:

```bash
rg -n "evidence_gated_extensions|_gated_config|TestA11yMarkupGatedDispatch|test_evidence_gate_|all domain files are evidence-gated|server-rendered-only diffs need|still require markup evidence|dispatch requires positive evidence|a11y's evidence-gated|accessibility dispatch evidence-gates|Dispatch is evidence-gated" \
  plugins/pirategoat-tools
```

Expected: no matches. Inspect any broader `evidence-gated` matches rather than deleting unrelated active policies such as the devils-advocate size threshold.

- [x] **Step 2: Run the complete plugin suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider plugins/pirategoat-tools/tests/ -v
```

Expected: all pirategoat-tools tests pass with no warnings or collection residue.

- [x] **Step 3: Verify repository hygiene and release metadata**

Run:

```bash
git diff --check
git status --short --untracked-files=all
rg -n '"version": "1.107.0"' .claude-plugin/marketplace.json
git diff --stat 5014875..HEAD
```

Expected: no whitespace errors; only intentional modified/tracked artifacts before commit; marketplace remains `1.107.0`; the diff contains no unrelated files.

- [x] **Step 4: Record verification and commit one logical runtime fix**

Update the analysis log with RED/GREEN/full-suite evidence and mark every plan checkbox complete. Force-add the ignored analysis/plan artifacts alongside the runtime, registry, test, changelog, and architecture files. Commit with subject:

```text
fix(review): Route mixed-markup changes conservatively
```

The body must explain the previous scoped detector-silence skip, why finite PHP renderer recognition cannot prove irrelevance, how deleting the generic gate fixes the entire class, and the deliberate increase in accessibility dispatch for backend-only PHP/PHTML diffs.
