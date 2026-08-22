# Agent Compliance Input Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make compliance-eval model attribution and CLI mode validation depend on explicit payload and option-presence contracts.

**Architecture:** Keep the change inside the existing eval runner. Project `modelUsage` records onto recognized token counters and canonical identities; preserve `--trials` omission until CLI compatibility checks finish, then normalize it to the existing runtime default.

**Tech Stack:** Python 3, argparse, pytest

---

## File map

- `plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py`: define and consume the two explicit input contracts.
- `plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py`: behavior-level regression tests for all three review findings.
- `plugins/pirategoat-tools/tests/TESTING.md`: document token-based primary attribution and canonical model identity.
- `plugins/pirategoat-tools/CHANGELOG.md`: extend the existing unpushed `1.114.0` entry with the corrected benchmark behavior.

### Task 1: Interpret model usage semantically

**Files:**
- Modify: `plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py:190-227`
- Modify: `plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py:580-628,669-675`
- Modify: `plugins/pirategoat-tools/tests/TESTING.md:201-211`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md:14`

- [ ] **Step 1: Add the capacity-metadata regression test**

Add this method to `TestDispatchIdentity`:

```python
def test_capacity_metadata_does_not_affect_primary_model_attribution(self):
    routed = next(
        a for a in _eval_mod.ALL_AGENTS
        if (_eval_mod.AGENT_CONFIG[a].get("model_tier") or "inherit")
        in _eval_mod._DISPATCHABLE_MODELS
    )
    tier = _eval_mod.AGENT_CONFIG[routed]["model_tier"]
    primary = f"claude-{tier}-5"
    usage = {
        primary: {
            "inputTokens": 6_000,
            "outputTokens": 5_000,
            "contextWindow": 200_000,
            "maxOutputTokens": 64_000,
        },
        "auxiliary": {
            "inputTokens": 1,
            "outputTokens": 1,
            "contextWindow": 1_000_000,
            "maxOutputTokens": 128_000,
        },
    }

    assert _eval_mod._primary_model(usage) == primary
    assert _eval_mod.check_dispatched_models(routed, usage) is None
```

- [ ] **Step 2: Run the capacity test and verify RED**

Run:

```bash
pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestDispatchIdentity::test_capacity_metadata_does_not_affect_primary_model_attribution -v
```

Expected: FAIL because `_primary_model()` returns `"auxiliary"`.

- [ ] **Step 3: Add the canonical-alias regression test**

Add this separate method to `TestDispatchIdentity`:

```python
def test_canonical_model_identity_is_used_for_routing(self):
    routed = next(
        a for a in _eval_mod.ALL_AGENTS
        if (_eval_mod.AGENT_CONFIG[a].get("model_tier") or "inherit")
        in _eval_mod._DISPATCHABLE_MODELS
    )
    tier = _eval_mod.AGENT_CONFIG[routed]["model_tier"]
    canonical = f"claude-{tier}-5"
    usage = {
        "gateway-primary": {
            "canonicalModel": canonical,
            "inputTokens": 6_000,
            "outputTokens": 5_000,
        },
    }

    assert _eval_mod._primary_model(usage) == canonical
    assert _eval_mod.check_dispatched_models(routed, usage) is None
```

- [ ] **Step 4: Run the alias test and verify RED**

Run:

```bash
pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestDispatchIdentity::test_canonical_model_identity_is_used_for_routing -v
```

Expected: FAIL because `_primary_model()` returns `"gateway-primary"`.

- [ ] **Step 5: Implement the explicit model-usage projection**

Near `_primary_model()`, add only the recognized fields and a canonical-name fallback:

```python
_MODEL_USAGE_TOKEN_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
)


def _model_identity(model: str, usage: object) -> str:
    if isinstance(usage, dict):
        canonical = usage.get("canonicalModel")
        if isinstance(canonical, str) and canonical:
            return canonical
    return model
```

Change `_primary_model()` so each record sums only `_MODEL_USAGE_TOKEN_FIELDS` and stores `_model_identity(model, usage)` as the winner. Change `check_dispatched_models()` to build its fallback `models` list through `_model_identity()` as well. Change `dispatch_agent()`'s evidence `models` list to use the same projection, keeping report evidence aligned with validation.

Do not introduce a dataclass, generic schema validator, provider registry, or speculative malformed-input policy.

- [ ] **Step 6: Run both model tests and verify GREEN**

Run:

```bash
pytest \
  plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestDispatchIdentity::test_capacity_metadata_does_not_affect_primary_model_attribution \
  plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestDispatchIdentity::test_canonical_model_identity_is_used_for_routing \
  -v
```

Expected: 2 passed.

- [ ] **Step 7: Update the benchmark contract documentation**

In `tests/TESTING.md`, replace “largest numeric usage” with language stating that primary attribution sums the four token counters and resolves `canonicalModel` before checking the registry tier. In the existing `1.114.0` changelog paragraph, replace the broad “usage weight” wording with the same concise contract. Do not add a new version section or version bump because `1.114.0` is already the unpushed feature version.

- [ ] **Step 8: Run the focused module**

Run:

```bash
pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 1**

Stage only the four Task 1 files and commit with:

```text
fix(grading): interpret model usage explicitly

Primary-model attribution treated every numeric modelUsage field as work
and treated provider map keys as canonical identities. Capacity metadata
could therefore outweigh token usage, while gateway aliases could reject a
correctly routed run.

Project each record onto the four token counters and resolve its
canonicalModel before attribution and tier validation. This also keeps
recorded model evidence aligned with the routing decision.
```

### Task 2: Preserve explicit trial-option presence

**Files:**
- Modify: `plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py:67-124`
- Modify: `plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py:940-980`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md:14`

- [ ] **Step 1: Add the no-dispatch explicit-default regression test**

Add this test to a new `TestCliModes` class:

```python
def test_explicit_default_trials_requires_dispatch(self, tmp_path):
    result = _run_eval("--trials", "1", cwd=tmp_path)

    assert result.returncode == 2
    assert "require --dispatch" in result.stderr
```

- [ ] **Step 2: Run the no-dispatch test and verify RED**

Run:

```bash
pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestCliModes::test_explicit_default_trials_requires_dispatch -v
```

Expected: FAIL because the command prints help and exits 0.

- [ ] **Step 3: Add the grade-only incompatibility regression test**

Add this separate test to `TestCliModes`:

```python
def test_grade_only_rejects_explicit_default_trials(self, tmp_path):
    result = _run_eval(
        "--grade-only", str(tmp_path), "--trials", "1", cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "--grade-only cannot be combined" in result.stderr
```

- [ ] **Step 4: Run the grade-only test and verify RED**

Run:

```bash
pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestCliModes::test_grade_only_rejects_explicit_default_trials -v
```

Expected: FAIL because grading runs and exits 0.

- [ ] **Step 5: Preserve omission through mode validation**

Change `--trials` to use `default=None`. Update the minimum-value check to run only when the option is present. In both mode-compatibility conditions, use `args.trials is not None` instead of `args.trials != 1`. After both conditions and before the grade-only branch, normalize once:

```python
if args.trials is None:
    args.trials = 1
```

Keep every dispatch-loop use of `args.trials` unchanged. Do not inspect `sys.argv`, add a custom argparse action, or introduce subcommands.

- [ ] **Step 6: Run both CLI tests and verify GREEN**

Run:

```bash
pytest \
  plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestCliModes::test_explicit_default_trials_requires_dispatch \
  plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py::TestCliModes::test_grade_only_rejects_explicit_default_trials \
  -v
```

Expected: 2 passed.

- [ ] **Step 7: Update the existing release note**

Extend the current `1.114.0` sentence about dispatch-only flags so it explicitly covers default-valued `--trials 1` and its incompatibility with `--grade-only`. Keep it in the same release paragraph; do not add another version bump.

- [ ] **Step 8: Run the focused module**

Run:

```bash
pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 2**

Stage only the three Task 2 files and commit with:

```text
fix(grading): preserve explicit trials presence

Argument validation compared the parsed trial count with its default, so
an explicitly supplied --trials 1 was indistinguishable from omission.
Automation could therefore omit --dispatch or combine the option with
--grade-only while still receiving exit 0.

Preserve omission as None through mode validation, reject every explicit
trial option in incompatible modes, then normalize to the existing runtime
default before evaluation.
```

### Task 3: Verify the integrated correction

**Files:**
- Verify only; no planned source changes.

- [ ] **Step 1: Run the prescribed grading suite**

Run:

```bash
pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the broader grading tests**

Run:

```bash
pytest plugins/pirategoat-tools/tests/grading/ -v
```

Expected: all tests pass.

- [ ] **Step 3: Check generated compatibility state**

Run:

```bash
python3 scripts/generate_codex_compat.py --check
```

Expected: exit 0 with no generated drift.

- [ ] **Step 4: Inspect the final diff and history**

Run:

```bash
git status --short
git diff HEAD~2..HEAD --check
git log -2 --oneline
```

Expected: no uncommitted tracked changes, no whitespace errors, and exactly the two focused fix commits.
